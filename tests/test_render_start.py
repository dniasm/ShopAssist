"""Unit tests for scripts/render_start.py: how the single Render DATABASE_URL becomes six per-schema URLs."""

import importlib.util
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from psycopg.conninfo import conninfo_to_dict

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("render_start", SCRIPTS / "render_start.py")
rs = importlib.util.module_from_spec(_spec)
sys.modules["render_start"] = rs
_spec.loader.exec_module(rs)

BASE = "postgresql://owner:p%40ss@dpg-abc.oregon-postgres.render.com:5432/shop"


def test_schema_url_keeps_credentials_and_adds_search_path():
    url = rs.schema_url(BASE, "orders")
    parts = urlsplit(url)
    assert parts.netloc == urlsplit(BASE).netloc and parts.path == "/shop"
    assert parse_qs(parts.query)["options"] == ["-c search_path=orders,public"]
    info = conninfo_to_dict(url)  # what libpq will actually see
    assert info["password"] == "p@ss" and info["options"] == "-c search_path=orders,public"


def test_schema_url_replaces_options_and_keeps_other_query():
    url = rs.schema_url(BASE + "?sslmode=require&options=-c%20x%3D1", "catalog")
    info = conninfo_to_dict(url)
    assert info["sslmode"] == "require" and info["options"] == "-c search_path=catalog,public"


def test_postgres_scheme_is_normalised():
    assert rs.schema_url("postgres://u:p@h/db", "customer").startswith("postgresql://u:p@h/db?")


def test_build_env():
    env = rs.build_env({"DATABASE_URL": BASE, "PORT": "10001"})
    assert env["ORCHESTRATOR_PORT"] == "10001" and env["ORCHESTRATOR_HOST"] == "0.0.0.0"
    assert env["GRPC_LISTEN_HOST"] == "127.0.0.1" and env["CATALOG_ADDR"] == "127.0.0.1:50051"
    assert env["ASSISTANT_ADDR"] == "127.0.0.1:50056"
    schemas = {conninfo_to_dict(env[v])["options"] for v in rs.SCHEMAS}
    assert len(schemas) == 6
    assert rs.build_env({"DATABASE_URL": BASE})["ORCHESTRATOR_PORT"] == "10000"
