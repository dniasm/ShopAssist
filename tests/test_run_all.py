"""Unit tests for scripts/run_all.py helpers. No processes are started and no fixed ports are used:
every port test takes a free port from the OS (bind to port 0) or a closed one."""

import importlib.util
import socket
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("run_all", SCRIPTS / "run_all.py")
ra = importlib.util.module_from_spec(_spec)
sys.modules["run_all"] = ra
_spec.loader.exec_module(ra)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_port_of():
    assert ra.port_of("localhost:50051") == 50051
    assert ra.port_of("8011") == 8011


def test_configured_value_prefers_env_then_default():
    p = ra.PROCESSES[0]
    assert ra.configured_value(p, {"CATALOG_ADDR": "localhost:6000"}) == "localhost:6000"
    assert ra.configured_value(p, {}) == "localhost:50051"


def test_build_plan_skips_missing_optional_modules(tmp_path, monkeypatch):
    _fake_repo(tmp_path, monkeypatch, with_order=False)
    plan, skipped = ra.build_plan({}, ra.module_exists, include_web=False, web_available=False)
    assert "order" not in [p.name for p in plan]
    assert any(s.startswith("order:") and "optional, skipped" in s for s in skipped)


def test_build_plan_includes_optional_when_present_and_web_when_asked():
    plan, skipped = ra.build_plan({}, lambda m: True, include_web=True, web_available=True)
    assert [p.name for p in plan] == ["catalog", "inventory", "order", "customer", "payment", "orchestrator", "web"]
    assert skipped == []
    plan, skipped = ra.build_plan({}, lambda m: True, include_web=True, web_available=False)
    assert "web" not in [p.name for p in plan] and "web/package.json" in skipped[0]


def test_module_exists_real_tree():
    assert ra.module_exists("services.catalog.server")
    assert not ra.module_exists("services.never_created_xyz.server")
    assert not ra.module_exists("nonexistent_pkg.sub.mod")


def _fake_repo(tmp_path, monkeypatch, with_order: bool):
    """A temp repo root with (optionally) services/order/server.py; REPO_ROOT patched to it."""
    root = tmp_path / "repo"
    (root / "services" / "order").mkdir(parents=True)
    if with_order:
        (root / "services" / "order" / "server.py").write_text("# created later\n", encoding="utf-8")
    monkeypatch.setattr(ra, "REPO_ROOT", root)
    return root


def test_optional_module_missing_is_skipped(tmp_path, monkeypatch):
    _fake_repo(tmp_path, monkeypatch, with_order=False)
    assert ra.module_exists("services.order.server") is False
    plan, skipped = ra.build_plan({}, ra.module_exists, include_web=False, web_available=False)
    assert "order" not in [p.name for p in plan]
    assert any(s.startswith("order:") and "optional, skipped" in s for s in skipped)


def test_optional_module_present_is_planned(tmp_path, monkeypatch):
    _fake_repo(tmp_path, monkeypatch, with_order=True)
    assert ra.module_exists("services.order.server") is True
    plan, skipped = ra.build_plan({}, ra.module_exists, include_web=False, web_available=False)
    assert "order" in [p.name for p in plan]
    assert not any(s.startswith("order:") for s in skipped)


def test_package_main_counts_as_present(tmp_path, monkeypatch):
    root = _fake_repo(tmp_path, monkeypatch, with_order=False)
    pkg = root / "services" / "order" / "server"
    pkg.mkdir()
    (pkg / "__main__.py").write_text("", encoding="utf-8")
    assert ra.module_exists("services.order.server") is True


def test_port_in_use_detects_ipv4_listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    port = s.getsockname()[1]
    try:
        assert ra.port_in_use(port)
    finally:
        s.close()
    time.sleep(0.05)
    assert not ra.port_in_use(port)


def test_port_in_use_detects_ipv6_only_listener():
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        pytest.skip("no IPv6 on this host")
    try:
        s.bind(("::1", 0))
    except OSError:
        s.close()
        pytest.skip("cannot bind ::1 on this host")
    s.listen()
    port = s.getsockname()[1]
    try:
        assert ra.port_in_use(port)
    finally:
        s.close()


def test_free_port_is_not_in_use():
    assert not ra.port_in_use(_free_port())


def test_wait_ready_timeout_and_death():
    t0 = time.monotonic()
    assert ra.wait_ready(lambda: False, timeout=0.5, alive=lambda: True) is None
    assert 0.4 < time.monotonic() - t0 < 2
    assert ra.wait_ready(lambda: True, timeout=5, alive=lambda: True) is not None
    assert ra.wait_ready(lambda: False, timeout=5, alive=lambda: False) is None


def test_tags_and_ready_line():
    assert ra.format_tag("catalog", None) == "[catalog     ]"
    assert ra.format_tag("orchestrator", None) == "[orchestrator]"
    assert ra.format_tag("catalog", "36").startswith("\x1b[36m[catalog")
    assert ra.ready_line("catalog", 50051, 412.4) == "catalog      port 50051  ready in 412 ms"


def test_probes_negative_on_closed_port():
    port = _free_port()  # nothing listens on it now
    assert not ra.grpc_serving(f"127.0.0.1:{port}", timeout=0.5)
    assert not ra.http_ok(f"http://127.0.0.1:{port}/api/health", timeout=0.5)
