"""LLM intent path with the HTTP call mocked (no network, no key). Success and every fallback."""
import asyncio
import json

import httpx
import pytest

from orchestrator.intent import detect_intent
from orchestrator.llm import LLMClient, LLMError, validate
from orchestrator.settings import Settings

SETTINGS = Settings(llm_api_key="test-key", llm_base_url="http://llm.invalid/v1", llm_model="m", llm_timeout=1.0)


def _client(handler) -> LLMClient:
    return LLMClient(SETTINGS, transport=httpx.MockTransport(handler))


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_llm_success_and_request_shape():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _reply('```json\n{"intent": "order_status", "entities": {"order_id": 1042}}\n```')

    intent, entities, source = asyncio.run(detect_intent("where is my parcel", _client(handler)))
    assert (intent, entities, source) == ("order_status", {"order_id": "1042"}, "llm")
    assert seen["url"] == "http://llm.invalid/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "m" and seen["body"]["messages"][-1]["content"] == "where is my parcel"


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, text="boom"),                               # HTTP error
    lambda r: _reply("not json at all"),                                      # invalid JSON
    lambda r: _reply('{"intent": "make_coffee", "entities": {}}'),            # unknown intent
    lambda r: _reply('{"intent": "order_status", "entities": {}}'),           # missing required entity
    lambda r: _reply('{"intent": "stock_check", "entities": {"size": "Q"}}'), # bad size
    lambda r: httpx.Response(200, json={"unexpected": True}),                 # wrong envelope
    lambda r: (_ for _ in ()).throw(httpx.ConnectTimeout("slow")),            # timeout / network
], ids=["http500", "badjson", "badintent", "noorderid", "badsize", "envelope", "timeout"])
def test_llm_failures_fall_back_to_rules(handler):
    intent, entities, source = asyncio.run(detect_intent("Where is my order 1042?", _client(handler)))
    assert (intent, entities, source) == ("order_status", {"order_id": "1042"}, "rules")


def test_no_llm_uses_rules():
    assert asyncio.run(detect_intent("Track order 1043 live", None))[2] == "rules"


def test_validate_normalises_entities():
    intent, ent = validate({"intent": "stock_check", "entities": {
        "product_id": "p-1004", "size": "m", "max_price": "50", "category": " Hoodies ", "keywords": ["Soft", ""]}})
    assert ent == {"product_id": "P-1004", "size": "M", "max_price": 50.0, "category": "hoodies", "keywords": ["soft"]}
    with pytest.raises(LLMError):
        validate(["not", "an", "object"])
