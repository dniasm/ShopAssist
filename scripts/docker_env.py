"""Generate the gitignored secrets for `docker compose` into .docker-env/ (one file per container, least privilege).

Usage (repo root):
    python scripts/docker_env.py            # creates the files; refuses to overwrite existing ones
    python scripts/docker_env.py --force    # regenerate (also needs `docker compose down -v`: the old Postgres volume keeps the old password)

Random values only; nothing is printed except file names.
"""

import argparse
import secrets
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / ".docker-env"
DBS = [("catalog", "svc_catalog", "CATALOG"), ("inventory", "svc_inventory", "INVENTORY"),
       ("orders", "svc_orders", "ORDER"), ("customer", "svc_customer", "CUSTOMER"),
       ("payment", "svc_payment", "PAYMENT"), ("assistant", "svc_assistant", "ASSISTANT")]


def token(n: int = 24) -> str:
    return secrets.token_urlsafe(n)  # URL-safe, so it can sit inside a database URL unescaped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    force = parser.parse_args().force
    if OUT.exists() and not force:
        sys.exit(f"{OUT.name}/ already exists; pass --force to regenerate")
    OUT.mkdir(exist_ok=True)

    pg_password = token()
    keys = f"SVC_KEY_ORCHESTRATOR={token(32)}\nSVC_KEY_ORDER={token(32)}\n"
    passwords = {db: token() for db, _, _ in DBS}

    files = {
        "postgres.env": f"POSTGRES_PASSWORD={pg_password}\n",
        "init.env": f"PG_ADMIN_URL=postgresql://postgres:{pg_password}@postgres:5432/postgres\n"
                    + "".join(f"{db.upper()}_DB_PASSWORD={pw}\n" for db, pw in passwords.items()),
        "orchestrator.env": f"SESSION_SECRET={token(32)}\n" + keys.splitlines()[0] + "\n",
    }
    for db, role, prefix in DBS:
        files[f"{prefix.lower()}.env"] = (
            f"{prefix}_DATABASE_URL=postgresql://{role}:{passwords[db]}@postgres:5432/{db}\n" + keys)
    for name, body in files.items():
        (OUT / name).write_text(body, encoding="utf-8", newline="\n")
        print(f".docker-env/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
