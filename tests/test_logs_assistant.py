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
    assert f["message"] == "Mirelle headphones" and f["turn"].startswith("T-") and f["conversation"]


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
    assert created["proposal"] == pid and created["action"] == "checkout_redirect"
    assert shop.client.post(f"/api/chat/proposals/{pid}/confirm", headers=headers).status_code == 202
    assert parsed(audit_lines(caplog, "proposal_confirmed")[0])["proposal"] == pid
    ws.until("message")  # the confirmation's own events
    say(shop, headers, "checkout again")
    pid2 = next(m for m in turn(ws) if m["type"] == "proposal")["data"]["proposal_id"]
    assert shop.client.post(f"/api/chat/proposals/{pid2}/dismiss", headers=headers).status_code == 200
    d = parsed(audit_lines(caplog, "proposal_dismissed")[0])
    assert d["proposal"] == pid2 and d["action"] == "checkout_redirect"
