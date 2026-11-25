"""LLM client (Groq gpt-oss-120b, multi-key rotation) with the HTTP layer mocked by httpx.MockTransport. No network, no keys.

The tests drive `LLMClient.complete_json` with a small intent-shaped schema; the agent uses the same call."""
import asyncio
import json
import logging
import time

import httpx
import pytest
from tests.conftest import AppClient

from orchestrator.app import create_app
from orchestrator.llm import LLMClient, LLMError
from orchestrator.settings import Settings, parse_keys

K1, K2 = "gsk_FAKEKEY_one_1234567890abcd", "gsk_FAKEKEY_two_1234567890wxyz"
ENTITY_KEYS = ["order_id", "product_id", "category", "color", "size", "max_price", "keywords"]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def settings(keys=(K1, K2), **kw) -> Settings:
    return Settings(llm_api_keys=tuple(keys), llm_base_url="http://llm.invalid/v1", llm_timeout=kw.pop("llm_timeout", 5.0), **kw)


def ok(intent="order_status", entities=None, tokens=120, headers=None) -> httpx.Response:
    ents = dict.fromkeys(ENTITY_KEYS)  # strict schema: every entity present, absent ones null
    ents.update(entities if entities is not None else {"order_id": "1042"})
    content = json.dumps({"intent": intent, "entities": ents})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": tokens}},
                          headers=headers or {})


def rate_limited(retry_after="30") -> httpx.Response:
    return httpx.Response(429, json={"error": {"message": "rate limit"}}, headers={"retry-after": retry_after})


class Upstream:
    """Scripted fake of the provider. `script[key]` is a list consumed per call (last item repeats)."""

    def __init__(self, script):
        self.script = script
        self.calls: list[str] = []   # which key each POST used, in order
        self.bodies: list[dict] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        key = request.headers["authorization"].removeprefix("Bearer ")
        self.calls.append(key)
        self.bodies.append(json.loads(request.content))
        items = self.script[key]
        n = self.calls.count(key) - 1
        item = items[min(n, len(items) - 1)]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return await item()
        return item

    def client(self, s=None, clock=None) -> LLMClient:
        return LLMClient(s or settings(), transport=httpx.MockTransport(self), now=clock or Clock())


INTENTS = ("order_status", "product_search", "stock_check", "unknown")
SIZES = {"XS", "S", "M", "L", "XL", "XXL"}
RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["intent", "entities"],
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "entities": {"type": "object", "additionalProperties": False, "required": ENTITY_KEYS,
                     "properties": {k: {"type": ["string", "null"]} for k in ENTITY_KEYS}},
    },
}


def validate(payload):
    """Test-local parser: the same job the agent's parse() does (reject unusable output with LLMError, treat null as absent)."""
    if not isinstance(payload, dict) or payload.get("intent") not in INTENTS:
        raise LLMError("unusable response")
    entities = {k: v for k, v in (payload.get("entities") or {}).items() if v not in (None, "")}
    if payload["intent"] == "order_status" and "order_id" not in entities:
        raise LLMError("order intent without order_id")
    if "size" in entities and entities["size"] not in SIZES:
        raise LLMError("bad size")
    return payload["intent"], entities


def ask(client, text="Where is my order 1042?"):
    return asyncio.run(client.complete_json("Classify the message.", text, "intent_result", RESPONSE_SCHEMA, validate))


def detect(client, text="Where is my order 1042?"):
    """(intent, entities, "llm") on success, (None, {}, "failed") when the client raised LLMError."""
    try:
        return (*ask(client, text), "llm")
    except LLMError:
        return None, {}, "failed"


# ---------------- request shape ----------------

def test_request_body_and_headers():
    up = Upstream({K1: [ok()], K2: [ok()]})
    client = up.client(settings(llm_reasoning_effort="medium", llm_max_tokens=777))
    intent, entities, source = detect(client, "where is my parcel")
    assert (intent, entities, source) == ("order_status", {"order_id": "1042"}, "llm")
    body = up.bodies[0]
    assert body["model"] == "openai/gpt-oss-120b" and body["temperature"] == 0
    assert body["max_completion_tokens"] == 777 and "max_tokens" not in body
    assert body["reasoning_effort"] == "medium" and body["include_reasoning"] is False
    assert "reasoning_format" not in body  # unsupported on gpt-oss
    assert body["messages"][-1] == {"role": "user", "content": "where is my parcel"}
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == RESPONSE_SCHEMA


def test_default_url_and_first_key_used():
    up = Upstream({K1: [ok()], K2: [ok()]})
    seen = {}

    async def handler(request):
        seen["url"] = str(request.url)
        return await up(request)

    ask(LLMClient(settings(), transport=httpx.MockTransport(handler), now=Clock()))
    assert seen["url"] == "http://llm.invalid/v1/chat/completions" and up.calls == [K1]
    assert Settings().llm_base_url == "https://api.groq.com/openai/v1" and Settings().llm_model == "openai/gpt-oss-120b"


def test_code_fences_are_stripped():
    fenced = httpx.Response(200, json={"choices": [{"message": {"content":
        '```json\n{"intent": "unknown", "entities": {}}\n```'}}]})
    up = Upstream({K1: [fenced], K2: [fenced]})
    assert detect(up.client())[0:3:2] == ("unknown", "llm")


# ---------------- validate: nulls ----------------

def test_validate_treats_null_entities_as_absent():
    intent, ent = validate({"intent": "product_search", "entities": dict.fromkeys(ENTITY_KEYS)})
    assert (intent, ent) == ("product_search", {})
    intent, ent = validate({"intent": "stock_check", "entities": {**dict.fromkeys(ENTITY_KEYS), "product_id": "P-1004", "size": "M"}})
    assert ent == {"product_id": "P-1004", "size": "M"}
    with pytest.raises(LLMError):
        validate({"intent": "order_status", "entities": dict.fromkeys(ENTITY_KEYS)})  # required id is null
    with pytest.raises(LLMError):
        validate(["not", "an", "object"])


# ---------------- rotation ----------------

def test_low_token_headroom_moves_next_call_to_key2_without_429():
    low = {"x-ratelimit-remaining-tokens": "100", "x-ratelimit-reset-tokens": "2m59.56s",
           "x-ratelimit-remaining-requests": "999", "x-ratelimit-reset-requests": "7.66s"}
    up = Upstream({K1: [ok(headers=low)], K2: [ok()]})
    client = up.client()
    assert detect(client)[2] == "llm"
    assert detect(client)[2] == "llm"
    assert up.calls == [K1, K2]   # second request avoided K1 proactively; nobody was ever rate limited
    status = asyncio.run(client.pool.status())
    assert status[0]["tpm_left"] == 100 and status[0]["rpd_left"] == 999 and status[0]["state"] == "ok"


def test_single_key_without_token_headroom_is_not_called():
    low = {"x-ratelimit-remaining-tokens": "100", "x-ratelimit-reset-tokens": "30s"}
    up = Upstream({K1: [ok(headers=low)]})
    client = up.client(settings(keys=(K1,)))
    assert detect(client)[2] == "llm"
    assert detect(client)[2] == "failed" and len(up.calls) == 1   # 100 tokens left < the request's estimate


def test_local_rpm_limit_skips_key_until_clock_advances():
    clock = Clock()
    up = Upstream({K1: [ok()]})
    client = up.client(settings(keys=(K1,), llm_limit_rpm=2), clock)
    assert [detect(client)[2] for _ in range(2)] == ["llm", "llm"]
    assert detect(client)[2] == "failed" and len(up.calls) == 2   # no headroom: not even attempted
    clock.advance(61)
    assert detect(client)[2] == "llm" and len(up.calls) == 3


def test_tpd_counter_fed_by_usage_total_tokens():
    up = Upstream({K1: [ok(tokens=1500)]})
    client = up.client(settings(keys=(K1,), llm_limit_tpd=4000, llm_max_tokens=1024))
    assert detect(client)[2] == "llm"
    assert asyncio.run(client.pool.status())[0]["tpd_left"] == 2500
    # est ~ (prompt/4 + 1024) is ~1.3k, so a 2nd call fits (2500 left) but a 3rd would not (1000 left)
    assert detect(client)[2] == "llm"
    assert detect(client)[2] == "failed" and len(up.calls) == 2


def test_429_cools_key1_and_same_request_succeeds_on_key2():
    up = Upstream({K1: [rate_limited("30")], K2: [ok()]})
    clock = Clock()
    client = up.client(clock=clock)
    assert detect(client) == ("order_status", {"order_id": "1042"}, "llm")
    assert up.calls == [K1, K2]                         # both POSTs observed, in that order
    st = asyncio.run(client.pool.status())
    assert st[0]["state"] == "cooling" and st[1]["state"] == "ok"
    assert detect(client)[2] == "llm" and up.calls == [K1, K2, K2]   # K1 is skipped while cooling
    clock.advance(31)
    assert asyncio.run(client.pool.status())[0]["state"] == "ok"


def test_401_disables_key_permanently():
    clock = Clock()
    up = Upstream({K1: [httpx.Response(401, json={"error": "invalid_api_key"})], K2: [ok()]})
    client = up.client(clock=clock)
    assert detect(client)[2] == "llm" and up.calls == [K1, K2]
    clock.advance(30 * 86400)
    assert detect(client)[2] == "llm" and up.calls == [K1, K2, K2]
    assert asyncio.run(client.pool.status())[0]["state"] == "disabled"


def test_all_keys_rate_limited_is_reported_as_an_error():
    up = Upstream({K1: [rate_limited()], K2: [rate_limited()]})
    intent, entities, source = detect(up.client())
    assert (intent, entities, source) == (None, {}, "failed")
    assert up.calls == [K1, K2]  # each key tried once, then LLMError


def test_all_keys_disabled_or_cooling_is_reported_as_an_error_without_http():
    up = Upstream({K1: [httpx.Response(403)], K2: [rate_limited("60")]})
    client = up.client()
    assert detect(client)[2] == "failed"
    calls_after_first = len(up.calls)
    assert detect(client)[2] == "failed" and len(up.calls) == calls_after_first


def test_soft_failure_retries_once_on_another_key_without_blaming_the_key():
    up = Upstream({K1: [httpx.Response(500, text="oops")], K2: [ok()]})
    client = up.client()
    assert detect(client)[2] == "llm" and up.calls == [K1, K2]
    assert [k["state"] for k in asyncio.run(client.pool.status())] == ["ok", "ok"]


@pytest.mark.parametrize("first", [
    httpx.Response(502),
    httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}),
    httpx.Response(200, json={"unexpected": True}),
    httpx.ConnectTimeout("slow"),
], ids=["5xx", "badjson", "envelope", "network"])
def test_soft_failures_get_exactly_one_retry(first):
    up = Upstream({K1: [first], K2: [first]})
    assert detect(up.client())[2] == "failed"
    assert len(up.calls) == 2  # original + one retry, then LLMError
    up = Upstream({K1: [first], K2: [ok()]})
    assert detect(up.client())[2] == "llm"


def test_other_4xx_is_not_retried():
    up = Upstream({K1: [httpx.Response(400, json={"error": "bad request"})], K2: [ok()]})
    assert detect(up.client())[2] == "failed" and up.calls == [K1]


@pytest.mark.parametrize("reply", [
    ok(intent="make_coffee"),
    ok(intent="order_status", entities={}),
    ok(intent="stock_check", entities={"size": "Q"}),
], ids=["badintent", "noorderid", "badsize"])
def test_invalid_model_output_is_reported_as_an_error_without_retry(reply):
    up = Upstream({K1: [reply], K2: [reply]})
    assert detect(up.client())[2] == "failed" and len(up.calls) == 1


def test_no_keys_means_empty_pool():
    assert asyncio.run(LLMClient(settings(keys=())).pool.status()) == []


# ---------------- timeout budget ----------------

def test_timeout_is_a_total_budget_across_attempts():
    async def slow_500():
        await asyncio.sleep(0.3)
        return httpx.Response(500)

    async def hangs():
        await asyncio.sleep(30)
        return ok()

    up = Upstream({K1: [slow_500], K2: [hangs]})
    client = up.client(settings(llm_timeout=0.6))
    t0 = time.perf_counter()
    result = detect(client)
    elapsed = time.perf_counter() - t0
    assert result[2] == "failed" and up.calls == [K1, K2]
    assert elapsed < 1.5, f"budget is 0.6s total but the query waited {elapsed:.2f}s"  # not 0.3 + 0.6, not 30
    with pytest.raises(LLMError, match="budget"):
        ask(client, "anything")


# ---------------- settings: key merging ----------------

def test_parse_keys_merges_and_dedupes():
    assert parse_keys("a, b\n c ,,", "b", None, " 'd' ") == ("a", "b", "c", "d")
    assert parse_keys(None, "") == ()


def test_legacy_llm_api_key_is_merged_with_llm_api_keys(monkeypatch):
    for name in ("LLM_API_KEYS", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert Settings.from_env().llm_api_keys == ()
    monkeypatch.setenv("LLM_API_KEY", "legacy")
    assert Settings.from_env().llm_api_keys == ("legacy",)
    monkeypatch.setenv("LLM_API_KEYS", "k1,k2, legacy ,k1")
    s = Settings.from_env()
    assert s.llm_api_keys == ("k1", "k2", "legacy")  # duplicates removed, legacy merged once
    assert (s.llm_limit_rpm, s.llm_limit_rpd, s.llm_limit_tpm, s.llm_limit_tpd) == (30, 1000, 8000, 200000)
    assert (s.llm_reasoning_effort, s.llm_max_tokens, s.llm_timeout) == ("low", 1024, 10.0)
    monkeypatch.setenv("LLM_LIMIT_RPM", "5")
    monkeypatch.setenv("LLM_MAX_TOKENS", "256")
    assert (Settings.from_env().llm_limit_rpm, Settings.from_env().llm_max_tokens) == (5, 256)


# ---------------- app: /api/llm/status ----------------

def test_status_without_keys():
    with AppClient(create_app(Settings())) as c:
        assert c.get("/api/llm/status").json() == {"enabled": False, "model": "openai/gpt-oss-120b", "keys": [],
                                                  "limits": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000}}


def test_status_and_logs_never_contain_a_full_key(caplog):
    caplog.set_level(logging.DEBUG)
    up = Upstream({K1: [rate_limited("30")], K2: [ok(intent="unknown", entities={})]})
    s = settings()
    client = up.client(s)
    intent, _entities = ask(client, "What's the weather?")  # K1 is rate limited, so K2 answers
    assert intent == "unknown" and up.calls == [K1, K2]
    with AppClient(create_app(s, llm=client)) as c:
        status = c.get("/api/llm/status")
    body = status.json()
    assert body["enabled"] is True and body["model"] == "openai/gpt-oss-120b"
    assert [k["key"] for k in body["keys"]] == ["key#1 …abcd", "key#2 …wxyz"]
    assert [k["state"] for k in body["keys"]] == ["cooling", "ok"]
    assert body["keys"][0]["cooling_until"] is not None and body["keys"][1]["cooling_until"] is None
    assert set(body["keys"][0]) == {"key", "state", "rpm_left", "rpd_left", "tpm_left", "tpd_left", "cooling_until"}
    for key in (K1, K2):
        assert key not in status.text and key not in caplog.text
    # error messages raised on failure carry no key either
    failing = Upstream({K1: [httpx.Response(401)], K2: [httpx.Response(401)]}).client(s)
    with pytest.raises(LLMError) as err:
        ask(failing, "x")
    assert K1 not in str(err.value) and K2 not in str(err.value)


# ---------------- tool calling (chat) ----------------

TOOLS = [{"type": "function", "function": {"name": "search", "parameters": {"type": "object", "properties": {}}}}]
MSGS = [{"role": "system", "content": "You are a shop assistant."}, {"role": "user", "content": "red hoodie"}]


def tool_reply(*calls, content=None, usage=None) -> httpx.Response:
    tcs = [{"id": i, "type": "function", "function": {"name": n, "arguments": a}} for i, n, a in calls]
    return httpx.Response(200, json={"choices": [{"message": {"content": content, "tool_calls": tcs}}],
                                     "usage": usage or {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100}})


def test_chat_returns_tool_calls():
    up = Upstream({K1: [tool_reply(("c1", "search", '{"q": "red hoodie"}'))], K2: [ok()]})
    out = asyncio.run(up.client().chat(MSGS, TOOLS))
    assert out == {"content": None, "tool_calls": [{"id": "c1", "name": "search", "args": {"q": "red hoodie"}}], "usage": 100}
    body = up.bodies[0]
    assert body["tools"] == TOOLS and body["tool_choice"] == "auto" and body["messages"] == MSGS
    assert body["temperature"] == 0 and body["max_completion_tokens"] == 700 and body["include_reasoning"] is False


def test_chat_text_reply_has_no_tool_calls():
    resp = httpx.Response(200, json={"choices": [{"message": {"content": "Hi!"}}], "usage": {"total_tokens": 7}})
    out = asyncio.run(Upstream({K1: [resp], K2: [resp]}).client().chat(MSGS, TOOLS))
    assert out == {"content": "Hi!", "tool_calls": [], "usage": 7}


def test_chat_bad_arguments_marked():
    up = Upstream({K1: [tool_reply(("a", "search", "{not json"), ("b", "search", '{"q": "x"}'))], K2: [ok()]})
    out = asyncio.run(up.client().chat(MSGS, TOOLS))
    assert out["tool_calls"][0]["args"] == {"_error": "bad arguments"}
    assert out["tool_calls"][1]["args"] == {"q": "x"}


def test_chat_rotates_on_429():
    up = Upstream({K1: [rate_limited()], K2: [tool_reply(("c1", "search", "{}"))]})
    out = asyncio.run(up.client().chat(MSGS, TOOLS))
    assert out["tool_calls"][0]["id"] == "c1" and up.calls == [K1, K2]


def test_chat_raises_llmerror_on_failure():
    up = Upstream({K1: [httpx.Response(400, json={})], K2: [httpx.Response(400, json={})]})
    with pytest.raises(LLMError):
        asyncio.run(up.client().chat(MSGS, TOOLS))


def test_model_override_sent():
    up = Upstream({K1: [tool_reply(), ok()], K2: [ok()]})
    client = up.client()
    asyncio.run(client.chat(MSGS, TOOLS, model="openai/gpt-oss-20b", max_tokens=50))
    asyncio.run(client.complete_json("s", "u", "intent_result", RESPONSE_SCHEMA, validate, model="m2"))
    assert up.bodies[0]["model"] == "openai/gpt-oss-20b" and up.bodies[0]["max_completion_tokens"] == 50
    assert up.bodies[1]["model"] == "m2"


def test_call_logged_without_key_or_content(caplog):
    up = Upstream({K1: [tool_reply(("c", "search", "{}"))], K2: [ok()]})
    with caplog.at_level(logging.INFO, logger="orchestrator.llm"):
        asyncio.run(up.client().chat(MSGS, TOOLS))
    text = caplog.text
    assert "llm call model=openai/gpt-oss-120b prompt=80 completion=20" in text
    assert K1 not in text and "red hoodie" not in text
