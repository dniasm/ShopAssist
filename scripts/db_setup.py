"""Create the five ShopAssist databases, one LOGIN role per service, and write their URLs to .env.

Usage (from the repo root; the server must be running, see scripts/pg.py):
    python scripts/db_setup.py            # idempotent: creates what is missing, changes nothing else
    python scripts/db_setup.py --reset    # drop and recreate the five databases, rotate role passwords

Needs PG_ADMIN_URL in .env (superuser). Writes these .env lines and nothing else:
    CATALOG_DATABASE_URL, INVENTORY_DATABASE_URL, ORDER_DATABASE_URL,
    CUSTOMER_DATABASE_URL, PAYMENT_DATABASE_URL, SESSION_SECRET, SVC_KEY_ORCHESTRATOR and SVC_KEY_ORDER
    (the last three only if missing; the SVC_KEY_* values authenticate service-to-service gRPC calls).
Every other .env line is preserved byte for byte. Passwords are never printed.

Isolation: each role owns its database. CONNECT is revoked from PUBLIC and granted only to that role,
so svc_catalog cannot connect to the inventory database, and so on.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from dotenv import dotenv_values
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"

# (database, role, .env variable that holds the role's URL)
SERVICES = [
    ("catalog", "svc_catalog", "CATALOG_DATABASE_URL"),
    ("inventory", "svc_inventory", "INVENTORY_DATABASE_URL"),
    ("orders", "svc_orders", "ORDER_DATABASE_URL"),
    ("customer", "svc_customer", "CUSTOMER_DATABASE_URL"),
    ("payment", "svc_payment", "PAYMENT_DATABASE_URL"),
]
SESSION_VAR = "SESSION_SECRET"
SERVICE_KEY_VARS = ("SVC_KEY_ORCHESTRATOR", "SVC_KEY_ORDER")  # one secret per gRPC caller identity


def _env_values() -> dict:
    if not ENV_FILE.is_file():
        sys.exit("db_setup: .env not found; copy .env.example to .env first")
    return dotenv_values(ENV_FILE)


def _role_url(admin_url: str, role: str, password: str, database: str) -> str:
    parts = urlsplit(admin_url)
    port = parts.port or 5432
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{parts.hostname}:{port}"
    return urlunsplit((parts.scheme, netloc, f"/{database}", parts.query, ""))


def _exists_db(cur, name: str) -> bool:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    return cur.fetchone() is not None


def _exists_role(cur, name: str) -> bool:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    return cur.fetchone() is not None


def _create_role(cur, role: str, password: str) -> None:
    cur.execute(sql.SQL(
        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
    ).format(sql.Identifier(role), sql.Literal(password)))


def _rotate_role(cur, role: str, password: str) -> None:
    cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
        sql.Identifier(role), sql.Literal(password)))


def _create_db(cur, database: str, role: str) -> None:
    cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
        sql.Identifier(database), sql.Identifier(role)))


def _drop_db(cur, database: str) -> None:
    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))


def _lock_down(cur, database: str, role: str) -> None:
    cur.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
    cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
        sql.Identifier(database), sql.Identifier(role)))


def _update_env(raw: bytes, updates: dict[str, str]) -> bytes:
    """Replace or append KEY=value lines; every other byte of the file is left as it was."""
    lines = raw.splitlines(keepends=True)
    default_eol = b"\r\n" if b"\r\n" in raw else b"\n"
    for key, value in updates.items():
        pattern = re.compile(rb"^" + re.escape(key.encode("ascii")) + rb"[ \t]*=")
        hits = [i for i, line in enumerate(lines) if pattern.match(line)]
        new_line = f"{key}={value}".encode("utf-8")
        if hits:
            first = hits[0]
            eol = lines[first][len(lines[first].rstrip(b"\r\n")):] or default_eol
            lines[first] = new_line + eol
            for i in reversed(hits[1:]):  # drop duplicate definitions of the same key
                del lines[i]
        else:
            if lines and not lines[-1].endswith(b"\n"):
                lines[-1] += default_eol
            lines.append(new_line + default_eol)
    return b"".join(lines)


def _write_env(updates: dict[str, str]) -> list[str]:
    """Apply updates to .env atomically. Returns the names whose line changed."""
    raw = ENV_FILE.read_bytes()
    current = dotenv_values(ENV_FILE)
    changed = [k for k, v in updates.items() if current.get(k) != v]
    if not changed:
        return []
    new_raw = _update_env(raw, {k: updates[k] for k in changed})
    tmp = ENV_FILE.parent / ".env.tmp"
    tmp.write_bytes(new_raw)
    os.replace(tmp, ENV_FILE)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Create ShopAssist databases and service roles.")
    parser.add_argument("--reset", action="store_true",
                        help="drop and recreate the five databases and rotate role passwords")
    args = parser.parse_args()

    env = _env_values()
    admin_url = env.get("PG_ADMIN_URL") or os.environ.get("PG_ADMIN_URL")
    if not admin_url:
        sys.exit("db_setup: PG_ADMIN_URL is not set in .env")

    updates: dict[str, str] = {}
    report: list[str] = []
    try:
        with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
            for database, role, env_var in SERVICES:
                db_there = _exists_db(cur, database)
                role_there = _exists_role(cur, role)
                have_url = bool(env.get(env_var))

                if args.reset:
                    if db_there:
                        _drop_db(cur, database)
                        report.append(f"database {database}: dropped")
                    password = secrets.token_urlsafe(24)
                    if role_there:
                        _rotate_role(cur, role, password)
                        report.append(f"role {role}: password rotated")
                    else:
                        _create_role(cur, role, password)
                        report.append(f"role {role}: created")
                    _create_db(cur, database, role)
                    report.append(f"database {database}: created, owner {role}")
                    updates[env_var] = _role_url(admin_url, role, password, database)
                else:
                    if not role_there:
                        if db_there:
                            sys.exit(f"db_setup: database {database} exists but role {role} does not; "
                                     "run with --reset")
                        password = secrets.token_urlsafe(24)
                        _create_role(cur, role, password)
                        _create_db(cur, database, role)
                        report.append(f"role {role}: created")
                        report.append(f"database {database}: created, owner {role}")
                        updates[env_var] = _role_url(admin_url, role, password, database)
                    else:
                        if not have_url:
                            sys.exit(f"db_setup: role {role} exists but {env_var} is not in .env "
                                     "(its password is unknown); run with --reset")
                        if not db_there:
                            _create_db(cur, database, role)
                            report.append(f"database {database}: created, owner {role}")
                        else:
                            report.append(f"database {database}: exists")
                        report.append(f"role {role}: exists")

                _lock_down(cur, database, role)

            if not env.get(SESSION_VAR):
                updates[SESSION_VAR] = secrets.token_urlsafe(32)
                report.append(f"{SESSION_VAR}: generated")
            else:
                report.append(f"{SESSION_VAR}: already set")
            for var in SERVICE_KEY_VARS:
                if not env.get(var):
                    updates[var] = secrets.token_urlsafe(32)
                    report.append(f"{var}: generated")
                else:
                    report.append(f"{var}: already set")
    except psycopg.Error as exc:
        sys.exit(f"db_setup: database operation failed: {type(exc).__name__}: {exc}")

    changed = _write_env(updates)
    for line in report:
        print(line)
    for name in changed:
        print(f".env {name}: written")
    if not changed:
        print(".env: no changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
