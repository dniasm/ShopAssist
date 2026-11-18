"""Optional LLM intent detection via an OpenAI-compatible /chat/completions endpoint.

Any failure (timeout, HTTP error, bad JSON, invalid shape) raises LLMError; the caller falls back to rules.
"""
import json
import re

import httpx

from orchestrator.intent import INTENTS
from orchestrator.settings import Settings

SYSTEM_PROMPT = """You classify messages sent to an online store assistant.
Reply with ONLY a JSON object: {"intent": <intent>, "entities": {...}}.
Intents: order_status (where is / status of an order), track_order (follow an order live),
product_search (find products), stock_check (is a product in stock, optionally a size),
product_info (details of one product), unknown (anything else).
Entities (include only those present): order_id (digits as a string), product_id (like "P-1003"),
category (one of headphones, hoodies, shoes, backpacks), color (lowercase), size (S, M, L, XL),
max_price (number), keywords (list of other search words)."""

_SIZES = {"XS", "S", "M", "L", "XL", "XXL"}


class LLMError(Exception):
    pass


def validate(payload: object) -> tuple[str, dict]:
    """Check the model's JSON and return (intent, clean_entities); raise LLMError if unusable."""
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
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        self._model = settings.llm_model
        self._headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
        self._timeout = settings.llm_timeout
        self._transport = transport  # tests inject httpx.MockTransport

    async def detect(self, text: str) -> tuple[str, dict]:
        body = {
            "model": self._model,
            "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                resp = await http.post(self._url, json=body, headers=self._headers)
                resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
            return validate(json.loads(content))
        except LLMError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e
