"""The assistant's audit trail: one replayable line per turn, and the life of a proposal."""
import json
import logging
import re
import time

import pytest

from orchestrator.llm import LLMError
from tests.test_agent_chat import FakeLLM, make_shop, open_chat, say, says, turn, uses  # noqa: F401  (make_shop: fixture)
from tests.test_api_shop import make_shop as shop_factory  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def audit_records(caplog):
    caplog.set_level(logging.INFO, logger="audit")


def audit_lines(caplog, event: str, wait: float = 2.0) -> list[str]:
    """The audit messages that start with `event`; the turn line may be written just after the HTTP reply."""
    end = time.monotonic() + wait
    while True:
        found = [r.getMessage() for r in caplog.records if r.name == "audit" and r.getMessage().split(" ", 1)[0] == event]
        if found or time.monotonic() > end:
            return found
        time.sleep(0.05)


def parsed(line: str) -> dict:
    """Fields with quoted values (spaces) decoded; split on key= boundaries so spaces inside quotes survive."""
    out = {}
    for m in re.finditer(r'(\w+)=("(?:[^"\\]|\\.)*"|\S*)', line.split(" ", 1)[1]):
        v = m.group(2)
        out[m.group(1)] = json.loads(v) if v.startswith('"') else v
    return out


def run_turn(make_shop, *responses, message="hello"):
    shop = make_shop(llm=FakeLLM(*responses))
    headers, ws = open_chat(shop)
    say(shop, headers, message)
    turn(ws)
    return shop, headers, ws


def test_turn_line_has_tools_and_reply(make_shop, caplog):
    run_turn(make_shop, uses(("search", {"category": "headphones", "brand": "Mirelle"})), says("Here you go"), message="Mirelle headphones")
    lines = audit_lines(caplog, "assistant_turn")
    assert len(lines) == 1
    f = parsed(lines[0])
    tools = json.loads(f["tools"])
    assert tools[0]["tool"] == "search" and tools[0]["args"] == {"category": "headphones", "brand": "Mirelle"}
    assert f["reply"] == "Here you go" and f["llm_calls"] == "2" and f["outcome"] == "ok"
    assert f["user"] and f["message"] == "Mirelle headphones" and f["turn"].startswith("T-") and f["conversation"]


def test_turn_line_llm_down(make_shop, caplog):
    run_turn(make_shop, LLMError("down"), message="show me hoodies")
    f = parsed(audit_lines(caplog, "assistant_turn")[0])
    assert f["outcome"] == "llm_down" and f["llm_calls"] == "0"
    assert json.loads(f["tools"])[0]["tool"]  # the rules planner's lookup is traced too


def test_result_cut_to_300(make_shop, caplog):
    from orchestrator.agent.tools import AgentTools
    original = AgentTools.run

    async def long_result(self, *a, **kw):
        out = await original(self, *a, **kw)
        return {**out, "text": "x" * 1000, "final": None}

    AgentTools.run = long_result
    try:
        run_turn(make_shop, uses(("search", {"category": "hoodies"})), says("ok"))
    finally:
        AgentTools.run = original
    trace = json.loads(parsed(audit_lines(caplog, "assistant_turn")[0])["tools"])
    assert 0 < len(trace[0]["result"]) <= 300 and trace[0]["error"] is False and isinstance(trace[0]["ms"], int)


def test_repeated_call_is_marked(make_shop, caplog):
    call = ("search", {"category": "hoodies"})
    run_turn(make_shop, uses(call), uses(call), says("done"))
    trace = json.loads(parsed(audit_lines(caplog, "assistant_turn")[0])["tools"])
    assert [t["result"] for t in trace][-1] == "repeated"


def test_proposal_audit(make_shop, caplog):
    from tests.test_agent_chat import sku_row
    shop = make_shop(llm=FakeLLM(uses(("checkout", {})), uses(("checkout", {}))))
    headers, ws = open_chat(shop)
    shop.put(headers, sku_row()[3], 1)
    say(shop, headers, "checkout")
    pid = next(m for m in turn(ws) if m["type"] == "proposal")["data"]["proposal_id"]
    created = parsed(audit_lines(caplog, "proposal_created")[0])
    assert created["proposal"] == pid and created["action"] == "checkout_redirect" and created["user"]
    assert shop.client.post(f"/api/chat/proposals/{pid}/confirm", headers=headers).status_code == 202
    confirmed = parsed(audit_lines(caplog, "proposal_confirmed")[0])
    assert confirmed["proposal"] == pid and confirmed["user"] == created["user"]
    ws.until("message")  # the confirmation's own events
    say(shop, headers, "checkout again")
    pid2 = next(m for m in turn(ws) if m["type"] == "proposal")["data"]["proposal_id"]
    assert shop.client.post(f"/api/chat/proposals/{pid2}/dismiss", headers=headers).status_code == 200
    d = parsed(audit_lines(caplog, "proposal_dismissed")[0])
    assert d["proposal"] == pid2 and d["action"] == "checkout_redirect" and d["user"] == created["user"]


def test_a_turn_that_raises_writes_one_error_line(make_shop, caplog):
    from orchestrator.agent.tools import AgentTools
    original = AgentTools.run
    n = {"calls": 0}

    async def boom(self, *a, **kw):
        n["calls"] += 1
        if n["calls"] == 2:
            raise RuntimeError("tool exploded")
        return await original(self, *a, **kw)

    AgentTools.run = boom
    try:
        run_turn(make_shop, uses(("search", {"category": "hoodies"})), uses(("search", {"category": "shoes"})), says("never"))
    finally:
        AgentTools.run = original
    lines = audit_lines(caplog, "assistant_turn")
    time.sleep(0.3)
    lines = audit_lines(caplog, "assistant_turn", wait=0)
    assert len(lines) == 1
    f = parsed(lines[0])
    trace = json.loads(f["tools"])
    assert f["outcome"] == "error" and f["error"] == "RuntimeError" and f["llm_calls"] == "2"
    assert [t["args"] for t in trace] == [{"category": "hoodies"}, {"category": "shoes"}] and trace[-1]["error"] is True


def test_outcome_rules_without_a_model(make_shop, caplog):
    shop = make_shop()
    headers, ws = open_chat(shop)
    say(shop, headers, "show me hoodies")
    turn(ws)
    f = parsed(audit_lines(caplog, "assistant_turn")[0])
    assert f["outcome"] == "rules" and f["llm_calls"] == "0"


def test_the_turn_line_is_capped(make_shop, caplog):
    big = {"category": "hoodies", "brand": "b" * 800}
    calls = [("search", {**big, "n": i}) for i in range(25)]
    from orchestrator.agent import loop
    shop = make_shop(llm=FakeLLM(uses(*calls[:3]), says("r" * 2000)))
    headers, ws = open_chat(shop)
    say(shop, headers, "m" * 450)
    turn(ws)
    f = parsed(audit_lines(caplog, "assistant_turn")[0])
    assert len(f["reply"]) <= 1000 and len(f["message"]) <= 450
    tools = json.loads(f["tools"])
    assert all(isinstance(t["args"], str) and len(t["args"]) <= loop.LINE_ARGS_CHARS for t in tools)
    saved = {"trace": [{"tool": "t", "args": {}, "result": "", "ms": 0, "error": False}] * 25}
    caplog.clear()
    loop.AgentLoop.audit_turn(saved, "T-x", "C-x", "m" * 2000, "r", "ok")
    g = parsed(audit_lines(caplog, "assistant_turn")[0])
    assert len(g["message"]) == 1000 and len(json.loads(g["tools"])) == 20 and g["tools_dropped"] == "5"


def test_login_rate_limited_is_audited(make_shop, caplog):
    from orchestrator.login_limits import MAX_FAILURES
    from tests.fixtures import EMAIL_OF
    shop = make_shop()
    for _ in range(MAX_FAILURES):
        assert shop.client.post("/api/session", json={"email": EMAIL_OF["U-04"], "password": "guess-guess"}).status_code == 401
    assert shop.client.post("/api/session", json={"email": EMAIL_OF["U-04"], "password": "guess-guess"}).status_code == 429
    assert parsed(audit_lines(caplog, "login_rate_limited")[0])["email"] == EMAIL_OF["U-04"]
