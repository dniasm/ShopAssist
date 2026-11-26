"""Start the whole ShopAssist stack inside ONE container (Render free web service + one free Postgres).

Usage: python scripts/render_start.py      (Render: dockerCommand)

Reads from the environment: DATABASE_URL (the one Render database), SVC_KEY_ORCHESTRATOR, SVC_KEY_ORDER,
SESSION_SECRET, PORT (default 10000), and the optional LLM_API_KEYS / VOYAGE_API_KEY / SIGNUP_CODE.

1. Creates one schema per service in that database (idempotent) plus pg_trgm in schema public.
2. Starts the six gRPC services on 127.0.0.1, each with its own <X>_DATABASE_URL = DATABASE_URL + search_path=<schema>,public.
3. Starts the orchestrator on 0.0.0.0:$PORT once every service is SERVING.
If any child dies, everything is stopped and this exits non-zero (Render restarts the service). SIGTERM stops everything.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Mapping
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_all  # noqa: E402  (helpers: start_child, wait_ready, stop_children; its behaviour is unchanged)

# service env var -> schema in the single Render database
SCHEMAS = {
    "CATALOG_DATABASE_URL": "catalog",
    "INVENTORY_DATABASE_URL": "inventory",
    "ORDER_DATABASE_URL": "orders",
    "CUSTOMER_DATABASE_URL": "customer",
    "PAYMENT_DATABASE_URL": "payment",
    "ASSISTANT_DATABASE_URL": "assistant",
}
GRPC_PORTS = {"CATALOG_ADDR": 50051, "INVENTORY_ADDR": 50052, "ORDER_ADDR": 50053,
              "CUSTOMER_ADDR": 50054, "PAYMENT_ADDR": 50055, "ASSISTANT_ADDR": 50056}
REQUIRED = ("DATABASE_URL", "SVC_KEY_ORCHESTRATOR", "SVC_KEY_ORDER", "SESSION_SECRET")
READY_TIMEOUT_SECONDS = 120.0  # free instances are slow: migrations + seeding on first start


def normalize_url(url: str) -> str:
    """libpq wants postgresql://; some providers hand out postgres://."""
    return "postgresql://" + url[len("postgres://"):] if url.startswith("postgres://") else url


def schema_url(database_url: str, schema: str) -> str:
    """The database URL with libpq `options=-c search_path=<schema>,public` (public: pg_trgm lives there)."""
    parts = urlsplit(normalize_url(database_url))
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "options"]
    query.append(("options", f"-c search_path={schema},public"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, quote_via=quote), ""))


def build_env(base: Mapping[str, str]) -> dict[str, str]:
    """The environment every child inherits."""
    env = dict(base)
    for var, schema in SCHEMAS.items():
        env[var] = schema_url(base["DATABASE_URL"], schema)
    for var, port in GRPC_PORTS.items():
        env[var] = f"127.0.0.1:{port}"
    env["GRPC_LISTEN_HOST"] = "127.0.0.1"
    env["ORCHESTRATOR_HOST"] = "0.0.0.0"
    env["ORCHESTRATOR_PORT"] = base.get("PORT") or "10000"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def prepare_database(database_url: str) -> None:
    """Create the schemas and pg_trgm (in public) as the database owner. Safe to repeat."""
    import psycopg
    from psycopg import sql

    with psycopg.connect(normalize_url(database_url), autocommit=True) as conn:
        for schema in SCHEMAS.values():
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public")


def say(line: str) -> None:
    run_all.say(f"{run_all.format_tag('render', None)} {line}")


def main() -> int:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        say(f"missing environment variables: {', '.join(missing)}")
        return 1
    env = build_env(os.environ)
    try:
        prepare_database(os.environ["DATABASE_URL"])
    except Exception as exc:  # never print the URL: it holds the password
        say(f"database setup failed: {type(exc).__name__}")
        return 1
    say("schemas ready")

    stop = run_all._stop_requested
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    plan, _ = run_all.build_plan(env, run_all.module_exists, include_web=False, web_available=False)
    children: list[run_all.Child] = []
    code = 0
    try:
        for proc in plan:
            value = run_all.configured_value(proc, env)
            port = run_all.port_of(value)
            popen = run_all.start_child(proc, env, None)
            child = run_all.Child(proc, popen, port, None)
            children.append(child)
            if proc.kind == "grpc":
                check = lambda v=value: run_all.grpc_serving(v)  # noqa: E731
            else:
                check = lambda u=f"http://127.0.0.1:{port}{proc.ready_path}": run_all.http_ok(u)  # noqa: E731
            ms = run_all.wait_ready(check, READY_TIMEOUT_SECONDS, lambda c=child: c.alive() and not stop.is_set())
            if stop.is_set():
                say("stop requested during startup")
                return 0
            if ms is None:
                say(f"{proc.name} did not become ready; stopping everything")
                return 1
            say(run_all.ready_line(proc.name, port, ms))
        say(f"{len(children)} processes ready")
        while not stop.is_set():
            for child in children:
                rc = child.popen.poll()
                if rc is not None:
                    say(f"{child.name} exited (code {rc}); stopping the rest")
                    return rc or 1
            time.sleep(run_all.POLL_SECONDS)
        say("SIGTERM received; stopping")
        return code
    finally:
        run_all.stop_children(children)


if __name__ == "__main__":
    sys.exit(main())
