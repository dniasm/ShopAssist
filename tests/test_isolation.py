"""Isolation: the orchestrator has no database access at all, and each service role can reach only its own database."""
import ast
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
SERVICES_DIR = ROOT / "services"
DB_MODULES = {"psycopg", "psycopg2", "psycopg_pool", "asyncpg", "sqlite3", "sqlalchemy", "aiosqlite", "databases", "peewee"}

# service package -> (env var holding its URL, database name)
SERVICE_DBS = {
    "catalog": ("CATALOG_DATABASE_URL", "catalog"),
    "inventory": ("INVENTORY_DATABASE_URL", "inventory"),
    "customer": ("CUSTOMER_DATABASE_URL", "customer"),
    "order": ("ORDER_DATABASE_URL", "orders"),
    "payment": ("PAYMENT_DATABASE_URL", "payment"),
}


def imported_modules(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_orchestrator_imports_no_database_driver_and_no_service_code():
    sources = list(ORCH.glob("*.py"))
    assert len(sources) > 5
    for path in sources:
        modules = imported_modules(path)
        drivers = {m for m in modules if m.split(".")[0] in DB_MODULES}
        assert not drivers, f"{path.name} imports a database driver: {drivers}"
        assert not {m for m in modules if m.split(".")[0] == "services"}, f"{path.name} imports service internals"
        text = path.read_text(encoding="utf-8")
        assert "_DATABASE_URL" not in text and "PG_ADMIN_URL" not in text, f"{path.name} touches database settings"


def test_importing_the_orchestrator_loads_no_database_driver_at_runtime():
    """Static scans miss indirect imports, so import the real app in a clean interpreter and look at sys.modules."""
    code = (
        "import sys, orchestrator.app, orchestrator.main;"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in %r))" % sorted(DB_MODULES)
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", out.stdout


@pytest.mark.parametrize("service", ["catalog", "inventory", "customer", "order", "payment"])
def test_a_service_reads_only_its_own_database_url(service):
    own_var = SERVICE_DBS[service][0]
    for path in (SERVICES_DIR / service).rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        others = [var for name, (var, _) in SERVICE_DBS.items() if name != service and var in text]
        assert not others, f"{path.relative_to(ROOT)} reads {others}"
        other_pkgs = {m for m in imported_modules(path)
                      if m.startswith("services.") and m.split(".")[1] not in (service, "common")}
        assert not other_pkgs, f"{path.relative_to(ROOT)} imports another service: {other_pkgs}"
    assert own_var in (SERVICES_DIR / service / "server.py").read_text(encoding="utf-8")


def _role_urls():
    urls = {name: os.environ.get(var) for name, (var, _) in SERVICE_DBS.items()}
    missing = [SERVICE_DBS[n][0] for n, u in urls.items() if not u]
    if missing:
        pytest.skip(f"{', '.join(missing)} not set: run `python scripts/db_setup.py` to create the per-service roles")
    return urls


def test_each_service_role_connects_to_its_own_database_only(admin_url):
    urls = _role_urls()
    for name, url in urls.items():
        own_db = SERVICE_DBS[name][1]
        assert conninfo_to_dict(url)["dbname"] == own_db
        with psycopg.connect(url, connect_timeout=5) as conn:  # positive control: the role works at home
            assert conn.execute("SELECT current_database()").fetchone()[0] == own_db
        for other, (_, other_db) in SERVICE_DBS.items():
            if other == name:
                continue
            with pytest.raises(psycopg.OperationalError, match="permission denied|not allowed|CONNECT"):
                psycopg.connect(make_conninfo(url, dbname=other_db), connect_timeout=5).close()


def test_service_roles_are_not_superusers(admin_url):
    for name, url in _role_urls().items():
        with psycopg.connect(url, connect_timeout=5) as conn:
            assert conn.execute("SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname = current_user").fetchone()[0] is False
