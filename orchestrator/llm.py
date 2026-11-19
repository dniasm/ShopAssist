"""Optional LLM client for the shopping agent (Groq gpt-oss-120b by default) over an OpenAI-compatible /chat/completions API.

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

from orchestrator.keypool import KeyPool, Limits
from orchestrator.settings import Settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


def _failed_generation(resp: httpx.Response) -> dict | None:
    """The model's own JSON from a 400 `json_validate_failed` error body, or None."""
    try:
        text = resp.json()["error"]["failed_generation"]
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
        return data if isinstance(data, dict) else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


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

    def _body(self, text: str, system: str, schema_name: str, schema: dict) -> dict:
        s = self._settings
        return {
            "model": s.llm_model,
            "temperature": 0,
            "max_completion_tokens": s.llm_max_tokens,
            "reasoning_effort": s.llm_reasoning_effort,
            "include_reasoning": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }

    async def complete_json(self, system: str, user: str, schema_name: str, schema: dict, parse: Callable[[dict], object],
                            max_tokens: int | None = None):
        """One strict-JSON completion through the key pool; `parse` validates the decoded object (raise LLMError to reject).

        The agent planner is the only caller. LLM_TIMEOUT is the total budget across all attempts and keys.
        """
        body = self._body(user, system, schema_name, schema)
        if max_tokens:  # a short answer: ask for (and budget for) less, so the key pool can serve more calls per minute
            body["max_completion_tokens"] = max_tokens
        est_tokens = sum(len(m["content"]) for m in body["messages"]) // 4 + (max_tokens or self._settings.llm_max_tokens)
        try:
            async with asyncio.timeout(self._timeout):
                return await self._rotate(body, est_tokens, parse)
        except TimeoutError:
            raise LLMError(f"LLM budget of {self._timeout:g}s exhausted") from None

    async def _rotate(self, body: dict, est_tokens: int, parse: Callable[[dict], object]):
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

                if status == 400 and (salvaged := _failed_generation(payload)) is not None:
                    # Groq's strict mode rejects a reply that merely omits a nullable key (HTTP 400 json_validate_failed) but
                    # returns what the model wrote; our own parse() checks it properly, so use it rather than lose the answer.
                    await self.pool.record(lease.index, status, headers)
                    return parse(salvaged)

                content = None
                if status == 200:
                    try:
                        data = payload.json()
                        tokens = (data.get("usage") or {}).get("total_tokens") or est_tokens
                        await self.pool.record(lease.index, status, headers, tokens)
                        content = data["choices"][0]["message"]["content"]
                        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
                        return parse(json.loads(content))
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
