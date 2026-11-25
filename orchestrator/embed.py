"""Voyage embeddings client for fact memory. Never raises: returns None when there is no key, on timeout, or on any error."""
import logging
from typing import Literal

import httpx

from orchestrator.settings import Settings

log = logging.getLogger(__name__)

URL = "https://api.voyageai.com/v1/embeddings"


class Embedder:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._key = settings.voyage_api_key
        self._model = settings.voyage_model
        self._transport = transport  # tests inject httpx.MockTransport

    async def embed(self, texts: list[str], kind: Literal["query", "document"]) -> list[list[float]] | None:
        if not self._key or not texts:
            return None
        try:
            async with httpx.AsyncClient(timeout=3.0, transport=self._transport) as http:
                resp = await http.post(URL, headers={"Authorization": f"Bearer {self._key}"},
                                       json={"input": texts, "model": self._model, "input_type": kind})
            resp.raise_for_status()
            vectors = [d["embedding"] for d in resp.json()["data"]]
            return vectors if len(vectors) == len(texts) else None
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            log.warning("embedding failed (%s)", type(e).__name__)
            return None
