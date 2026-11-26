"""Shared log module: line format, daily files, audit file, retention, masking, failing writes."""
import logging
import re
from datetime import date

import pytest

from shared_common.common import log as logmod
from shared_common.common.log import audit, get_request_id, set_request_id, setup_logging

DB_URL = "postgresql://svc:hunter2@h/db"


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_RETENTION_DAYS", raising=False)
    yield tmp_path
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)
    logmod._request_id.set("-")


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def _today():
    return date.today().isoformat()


def test_line_format(logs):
    setup_logging("order")
    set_request_id("abc")
    logging.getLogger("services.order.saga").info("x=%s", 1)
    lines = _lines(logs / "order" / f"{_today()}.log")
    assert re.match(r"^\d\d:\d\d:\d\d INFO    \[order\] \[abc\] services\.order\.saga: x=1$", lines[-1])


def test_default_request_id_dash(logs):
    setup_logging("order")
    logging.getLogger("t").info("hello")
    assert "[-]" in _lines(logs / "order" / f"{_today()}.log")[-1]


def test_set_request_id_new_and_continue(logs):
    assert re.fullmatch(r"[0-9a-f]{12}", set_request_id())
    assert set_request_id("r1") == "r1"
    assert get_request_id() == "r1"


def test_audit_goes_to_both_files(logs):
    setup_logging("order")
    logging.getLogger("t").info("plain line")
    audit("order_placed", order="O-1", total="$12.00")
    audit_lines = _lines(logs / "audit" / f"order-{_today()}.log")
    assert len(audit_lines) == 1 and audit_lines[0].endswith("order_placed order=O-1 total=$12.00")
    service_lines = _lines(logs / "order" / f"{_today()}.log")
    assert any(line.endswith("order_placed order=O-1 total=$12.00") for line in service_lines)
    assert not any("plain line" in line for line in audit_lines)


def test_audit_quotes_values_with_spaces(logs):
    setup_logging("order")
    audit("note", text="two words")
    assert _lines(logs / "audit" / f"order-{_today()}.log")[0].endswith('note text="two words"')


def test_audit_ignores_level(logs, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    setup_logging("order")
    logging.getLogger("t").info("quiet")
    audit("order_placed", order="O-1")
    assert any("order_placed" in line for line in _lines(logs / "audit" / f"order-{_today()}.log"))
    assert not any("quiet" in line for line in _lines(logs / "order" / f"{_today()}.log"))


def _emit_with(handlers, name, *messages):
    logger = logging.getLogger(name)
    logger.propagate = False
    for handler in handlers:
        logger.addHandler(handler)
    try:
        for message in messages:
            logger.warning(message)
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()


def test_new_file_each_day(logs):
    days = [date(2026, 11, 26)]
    handler = logmod.DailyFileHandler(logs / "order", "{date}.log", 30, today=lambda: days[0])
    logger = logging.getLogger("daily-test")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.warning("one")
        days[0] = date(2026, 11, 27)
        logger.warning("two")
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert sorted(p.name for p in (logs / "order").iterdir()) == ["2026-11-26.log", "2026-11-27.log"]


def test_retention(logs):
    (logs / "order").mkdir()
    (logs / "audit").mkdir()
    for name in ("order/2026-10-01.log", "order/2026-11-20.log", "audit/order-2026-01-01.log"):
        (logs / name).write_text("old\n")
    today = lambda: date(2026, 11, 26)  # noqa: E731
    _emit_with(
        [logmod.DailyFileHandler(logs / "order", "{date}.log", 30, today=today),
         logmod.DailyFileHandler(logs / "audit", "order-{date}.log", 0, today=today)],
        "retention-test", "x")
    assert not (logs / "order" / "2026-10-01.log").exists()
    assert (logs / "order" / "2026-11-20.log").exists()
    assert (logs / "audit" / "order-2026-01-01.log").exists()


def test_masks_message(logs):
    setup_logging("order")
    logging.getLogger("t").info(
        "url=%s key=gsk_abcdefghijklmn password=p1 prompt_tokens=5 tokens=1200", DB_URL)
    text = (logs / "order" / f"{_today()}.log").read_text(encoding="utf-8")
    for expected in ("svc:***@", "gsk_***", "password=***", "prompt_tokens=5", "tokens=1200"):
        assert expected in text
    for leaked in ("hunter2", "abcdefghijklmn", "p1 "):
        assert leaked not in text


def test_masks_traceback(logs):
    setup_logging("order")
    try:
        raise RuntimeError(f"cannot connect to {DB_URL} with gsk_abcdefghijklmn")
    except RuntimeError:
        logging.getLogger("t").exception("failed")
    text = (logs / "order" / f"{_today()}.log").read_text(encoding="utf-8")
    assert "Traceback" in text and "hunter2" not in text and "abcdefghijklmn" not in text


def test_unwritable_dir_does_not_raise(logs, monkeypatch, capsys):
    blocker = logs / "blocked"
    blocker.write_text("i am a file")
    monkeypatch.setenv("LOG_DIR", str(blocker))
    setup_logging("order")
    logging.getLogger("t").info("still fine")
    assert capsys.readouterr().err.strip()
