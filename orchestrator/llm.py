"""Optional LLM intent detection (Groq gpt-oss-120b by default) over an OpenAI-compatible /chat/completions API.

Calls are spread over a pool of API keys (see keypool.py). Any failure raises LLMError and the caller
falls back to the rule-based parser. Error messages never contain an API key.
"""
import asyncio
import json
import logging
import re
import time
from collections.abc import Callable

import httpx

from orchestrator.intent import CATEGORY_WORDS, INTENTS
from orchestrator.keypool import KeyPool, Limits
from orchestrator.settings import Settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You classify messages sent to an online store assistant.
Reply with a JSON object: {"intent": <intent>, "entities": {...}}.
Intents: order_status (where is / status of an order), track_order (follow an order live),
product_search (find products), stock_check (is a product in stock, optionally a size),
product_info (details of one product), unknown (anything else).
Entities (use null for any that are absent): order_id (digits as a string), product_id (like "P-1003"),
category (one of headphones, hoodies, shoes, backpacks, accessories), color (lowercase), size (S, M, L, XL),
max_price (number), keywords (list of other search words)."""

CATEGORIES = sorted(set(CATEGORY_WORDS.values()))
_SIZES = {"XS", "S", "M", "L", "XL", "XXL"}


def _nullable(*types: str, **extra) -> dict:
    return {"type": [*types, "null"], **extra}


# Strict structured output: every property is required, optional ones are nullable, no extra properties.
RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "entities"],
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "entities": {
            "type": "object",
            "additionalProperties": False,
            "required": ["order_id", "product_id", "category", "color", "size", "max_price", "keywords"],
            "properties": {
                "order_id": _nullable("string"),
                "product_id": _nullable("string"),
                "category": _nullable("string", enum=[*CATEGORIES, None]),
                "color": _nullable("string"),
                "size": _nullable("string"),
                "max_price": _nullable("number"),
                "keywords": _nullable("array", items={"type": "string"}),
            },
        },
    },
}


class LLMError(Exception):
    pass


def validate(payload: object) -> tuple[str, dict]:
    """Check the model's JSON and return (intent, clean_entities); raise LLMError if unusable. null means absent."""
    if not isinstance(payload, dict):
        raise LLMError("response is not a JSON object")
    intent = payload.get("intent")
    if intent not in INTENTS:
        raise LLMError(f"unknown intent {intent!r}")
    raw = payload.get("entities") or {}
    if not isinstance(raw, dict):
        raise LLMError("entities is not an object")

    entities: dict = {}
    try:
        if raw.get("order_id") not in (None, ""):
            order_id = str(raw["order_id"]).strip().lstrip("#")
            if not order_id.isdigit():
                raise LLMError(f"bad order_id {raw['order_id']!r}")
            entities["order_id"] = order_id
        if raw.get("product_id") not in (None, ""):
            m = re.fullmatch(r"p-?(\d+)", str(raw["product_id"]).strip(), re.I)
            if not m:
                raise LLMError(f"bad product_id {raw['product_id']!r}")
            entities["product_id"] = f"P-{m.group(1)}"
        if raw.get("size") not in (None, ""):
            size = str(raw["size"]).strip().upper()
            if size not in _SIZES:
                raise LLMError(f"bad size {raw['size']!r}")
            entities["size"] = size
        if raw.get("max_price") not in (None, ""):
            entities["max_price"] = float(raw["max_price"])
    except (TypeError, ValueError) as e:
        raise LLMError(f"bad entity value: {e}") from e
    for key in ("category", "color"):
        if isinstance(raw.get(key), str) and raw[key].strip():
            entities[key] = raw[key].strip().lower()
    if isinstance(raw.get("keywords"), list):
        words = [str(w).strip().lower() for w in raw["keywords"] if str(w).strip()]
        if words:
            entities["keywords"] = words

    if intent in ("order_status", "track_order") and "order_id" not in entities:
        raise LLMError("order intent without order_id")
    if intent == "product_info" and "product_id" not in entities:
        raise LLMError("product_info without product_id")
    return intent, entities


class LLMClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                 now: Callable[[], float] = time.monotonic):
        self._url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        self._settings = settings
        self._timeout = settings.llm_timeout
        self._transport = transport  # tests inject httpx.MockTransport
        self.pool = KeyPool(
            settings.llm_api_keys,
            Limits(settings.llm_limit_rpm, settings.llm_limit_rpd, settings.llm_limit_tpm, settings.llm_limit_tpd),
            now=now,
        )

    def _body(self, text: str) -> dict:
        s = self._settings
        return {
            "model": s.llm_model,
            "temperature": 0,
            "max_completion_tokens": s.llm_max_tokens,
            "reasoning_effort": s.llm_reasoning_effort,
            "include_reasoning": False,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "intent_result", "strict": True, "schema": RESPONSE_SCHEMA},
            },
        }

    async def detect(self, text: str) -> tuple[str, dict]:
        body = self._body(text)
        # Estimated tokens for this request: prompt chars / 4 plus the completion allowance.
        est_tokens = sum(len(m["content"]) for m in body["messages"]) // 4 + self._settings.llm_max_tokens
        try:
            # LLM_TIMEOUT is the total budget for the whole detection, not per attempt.
            async with asyncio.timeout(self._timeout):
                return await self._rotate(body, est_tokens)
        except TimeoutError:
            raise LLMError(f"LLM budget of {self._timeout:g}s exhausted") from None

    async def _rotate(self, body: dict, est_tokens: int) -> tuple[str, dict]:
        tried: set[int] = set()   # keys that failed softly for this request
        soft_retry_left = True    # timeout / 5xx / bad JSON: at most one retry, on another key
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
            while True:
                lease = await self.pool.acquire(est_tokens, exclude=tried)
                if lease is None:
                    raise LLMError("no API key has rate-limit headroom")
                try:
                    resp = await http.post(self._url, json=body, headers={"Authorization": f"Bearer {lease.key}"})
                except httpx.HTTPError as e:
                    reason = f"request failed ({type(e).__name__})"
                    status, headers, payload = None, {}, None
                else:
                    status, headers, payload = resp.status_code, resp.headers, resp
                    reason = f"HTTP {status}"

                if status in (429, 401, 403):  # the key is cooled / disabled by record(); retry on the next one
                    await self.pool.record(lease.index, status, headers)
                    continue

                content = None
                if status == 200:
                    try:
                        data = payload.json()
                        tokens = (data.get("usage") or {}).get("total_tokens") or est_tokens
                        await self.pool.record(lease.index, status, headers, tokens)
                        content = data["choices"][0]["message"]["content"]
                        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
                        return validate(json.loads(content))
                    except LLMError:
                        raise
                    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                        reason = "unusable response body"
                elif status is not None:
                    await self.pool.record(lease.index, status, headers)
                    if status < 500:
                        raise LLMError(reason)  # other 4xx: the request itself is wrong, another key won't help

                # Soft failure (timeout, 5xx, bad JSON): don't blame the key; one retry elsewhere, else give up.
                if not soft_retry_left:
                    raise LLMError(reason)
                soft_retry_left = False
                tried.add(lease.index)
                log.warning("LLM attempt on key#%d failed (%s); retrying on another key", lease.index + 1, reason)
