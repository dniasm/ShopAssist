"""Voyage embedder with the HTTP layer mocked. No network, no key."""
import asyncio
import json

import httpx

from orchestrator.embed import Embedder
from orchestrator.settings import Settings

KEY = "pa-FAKE_voyage_key_123456"


def embedder(handler, key=KEY) -> Embedder:
    return Embedder(Settings(voyage_api_key=key), transport=httpx.MockTransport(handler))


def test_embed_returns_vectors_and_sends_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(url=str(request.url), auth=request.headers["authorization"], body=json.loads(request.content))
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]})

    out = asyncio.run(embedder(handler).embed(["a", "b"], "document"))
    assert out == [[0.1, 0.2], [0.3, 0.4]]
    assert seen["url"] == "https://api.voyageai.com/v1/embeddings" and seen["auth"] == f"Bearer {KEY}"
    assert seen["body"] == {"input": ["a", "b"], "model": "voyage-4-lite", "input_type": "document"}


def test_embed_none_without_key():
    def handler(request):
        raise AssertionError("no request expected")

    assert asyncio.run(embedder(handler, key="").embed(["a"], "query")) is None


def test_embed_none_on_error():
    assert asyncio.run(embedder(lambda r: httpx.Response(500)).embed(["a"], "query")) is None
    assert asyncio.run(embedder(lambda r: httpx.Response(200, json={"oops": 1})).embed(["a"], "query")) is None

    def boom(request):
        raise httpx.ConnectTimeout("slow")

    assert asyncio.run(embedder(boom).embed(["a"], "query")) is None
