"""scripts/logs.py: merge by time, filters, audit-only, turn replay, tracebacks, damaged lines."""
import importlib.util
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("logs_viewer", Path(__file__).resolve().parents[1] / "scripts" / "logs.py")
logs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(logs)

DAY = date(2026, 11, 26)
PREV = date(2026, 11, 25)


def line(time, level, service, rid, name, msg):
    return f"{time} {level:<7} [{service}] [{rid}] {name}: {msg}"


def put(root, rel, *lines):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


TURN_TOOLS = json.dumps([{"tool": "search_products", "args": {"q": "tea"}, "result": "3 found", "ms": 12, "error": ""}])


def audit_line(time, service, rid, event, **fields):
    parts = [event]
    for key, value in fields.items():
        text = str(value)
        parts.append(f"{key}={json.dumps(text) if (not text or ' ' in text or chr(34) in text) else text}")
    return line(time, "INFO", service, rid, "audit", " ".join(parts))


@pytest.fixture
def tree(tmp_path):
    placed = audit_line("10:00:03", "order", "r1", "order_placed", user="u1", order="o1")
    cancelled = audit_line("10:05:00", "order", "r2", "order_cancelled", user="u2", order="o2", reason="cancelled by customer")
    turn = audit_line("10:06:00", "orchestrator", "r3", "assistant_turn", conversation="c1", turn="t-42", message="find a tea",
                      tools=TURN_TOOLS, reply="Here is tea", llm_calls=1, tokens=77, ms=900, outcome="llm")
    put(tmp_path, f"catalog/{DAY}.log",
        line("10:00:01", "INFO", "catalog", "r1", "services.catalog", "listed"),
        line("10:00:05", "WARNING", "catalog", "r2", "services.catalog", "slow query"))
    put(tmp_path, f"order/{DAY}.log",
        line("10:00:02", "DEBUG", "order", "r1", "services.order", "start"), placed, cancelled)
    put(tmp_path, f"orchestrator/{DAY}.log",
        line("10:05:59", "INFO", "orchestrator", "r3", "agent", "turn begins"), turn,
        line("10:06:01", "INFO", "orchestrator", "r3", "agent", "turn done"))
    put(tmp_path, f"order/{PREV}.log", line("23:59:59", "INFO", "order", "r0", "services.order", "yesterday"))
    put(tmp_path, f"audit/order-{DAY}.log", placed, cancelled)
    return tmp_path


def run(capsys, tree, *args):
    code = logs.main(["--dir", str(tree), "--day", DAY.isoformat(), *args])
    return code, capsys.readouterr().out.splitlines()


def test_merges_by_time(capsys, tree):
    code, out = run(capsys, tree)
    assert code == 0
    times = [l.split()[1] for l in out if l.startswith("2026-")]
    assert times == sorted(times) and len(times) == 8  # audit lines shown once, not twice
    assert out[0] == "2026-11-26 10:00:01 INFO    [catalog] [r1] services.catalog: listed"


def test_filter_request_and_level(capsys, tree):
    _, out = run(capsys, tree, "--request", "r1")
    assert len(out) == 3 and all("[r1]" in l for l in out)
    _, out = run(capsys, tree, "--level", "WARNING")
    assert len(out) == 1 and "slow query" in out[0]
    _, out = run(capsys, tree, "--service", "catalog", "--grep", "SLOW")
    assert len(out) == 1


def test_filter_user_and_order(capsys, tree):
    _, out = run(capsys, tree, "--user", "u1")
    assert len(out) == 1 and "order_placed" in out[0]
    _, out = run(capsys, tree, "--order", "o2")
    assert len(out) == 1 and "order_cancelled" in out[0]
    _, out = run(capsys, tree, "--user", "u")
    assert out == []        # exact match, not a prefix


def test_since_relative(capsys, tree, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 26, 10, 10, 0)
    monkeypatch.setattr(logs, "datetime", Clock)
    _, out = run(capsys, tree, "--since", "6m")           # from 10:04:00
    assert [l.split()[1] for l in out] == ["10:05:00", "10:05:59", "10:06:00", "10:06:01"]
    _, out = run(capsys, tree, "--since", "1d")           # spans yesterday's file too
    assert any("yesterday" in l for l in out)
    _, out = run(capsys, tree, "--since", "2026-11-26T10:06:00")
    assert len(out) == 2


def test_audit_only(capsys, tree):
    _, out = run(capsys, tree, "--audit")
    assert len(out) == 2 and all(" audit: " in l for l in out)


def test_turn_block(capsys, tree):
    code, out = run(capsys, tree, "--turn", "t-42")
    text = "\n".join(out)
    assert code == 0
    assert "find a tea" in text and "search_products(" in text and "-> 3 found" in text
    assert "Here is tea" in text and "tokens" in text and "77" in text
    assert text.count("[r3]") == 3                        # every line of the turn's request
    assert "listed" not in text
    code, out = run(capsys, tree, "--turn", "nope")
    assert code == 1


def test_traceback_attached(tree):
    rec = logs.parse(line("10:00:00", "ERROR", "order", "r9", "services.order", "boom"), DAY, "order")
    assert rec["level"] == "ERROR" and rec["request_id"] == "r9" and rec["logger"] == "services.order"
    assert rec["ts"] == datetime(2026, 11, 26, 10, 0, 0)
    put(tree, f"order/{DAY}.log",
        line("10:00:00", "ERROR", "order", "r9", "services.order", "boom"),
        "Traceback (most recent call last):", '  File "x.py", line 1, in f', "ValueError: bad",
        line("10:00:01", "INFO", "order", "r9", "services.order", "next"))
    recs = [r for r in logs.load(tree, [DAY], False) if r["service"] == "order"]
    assert len(recs) == 2 and "ValueError: bad" in recs[0]["message"] and "Traceback" in recs[0]["message"]
    assert "ValueError" not in recs[1]["message"]


def test_skips_bad_lines(capsys, tree):
    assert logs.parse("garbage", DAY, "x") is None
    path = tree / "catalog" / f"{DAY}.log"
    good = line("10:00:01", "INFO", "catalog", "r1", "services.catalog", "listed")
    path.write_bytes(b"foreign line from another tool\n" + good.encode() + b"\n10:00:0\xff\xfe broken")  # cut off mid-write
    code, out = run(capsys, tree, "--service", "catalog")
    assert code == 0
    assert len(out) == 1 and "listed" in out[0]


def test_missing_dir_is_not_an_error(capsys, tmp_path):
    assert logs.main(["--dir", str(tmp_path / "none")]) == 0
