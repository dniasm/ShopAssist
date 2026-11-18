"""Relaxed product search: a keyword that matches nothing is dropped once, if another filter remains.

Dispatch-level tests with a recording fake gRPC client, so every SearchProducts call can be checked exactly.
"""
import asyncio

from orchestrator.dispatch import run_query
from orchestrator.store import QueryRecord
from shopassist_proto import shopassist_pb2 as pb

PHONES = [pb.Product(product_id="P-1002", name="Budget Earbuds", category="headphones", color="white", price=39.99),
          pb.Product(product_id="P-1001", name="Wireless Headphones", category="headphones", color="black", price=89.99)]
HOODIE = pb.Product(product_id="P-1004", name="Classic Hoodie", category="hoodies", color="blue", price=49.5)


class FakeClients:
    """search(text) -> list of products; records every SearchProducts call."""

    def __init__(self, search):
        self._search = search
        self.search_calls: list[dict] = []

    async def search_products(self, text="", category="", max_price=0.0, limit=0):
        self.search_calls.append({"text": text, "category": category, "max_price": max_price, "limit": limit})
        return self._search(text)

    async def get_stock(self, product_id, size=""):
        return [pb.StockLevel(product_id=product_id, size=size or "M", quantity=4)]


def run(text, search):
    clients = FakeClients(search)
    rec = QueryRecord(text)
    asyncio.run(run_query(rec, clients, None))
    return rec, clients


def types(rec):
    return [e["type"] for e in rec.events]


def search_calls(rec):
    return [e["data"]["method"] for e in rec.events if e["type"] == "service_call"]


def test_relaxed_retry_drops_keywords_and_says_so():
    # "cans" matches nothing; without it the category + price filters match two headphones
    rec, clients = run("Show me cans under $100 headphones", lambda text: [] if "cans" in text else PHONES)
    assert rec.entities == {"category": "headphones", "max_price": 100.0, "keywords": ["cans"]}
    assert clients.search_calls == [
        {"text": "cans", "category": "headphones", "max_price": 100.0, "limit": 0},
        {"text": "", "category": "headphones", "max_price": 100.0, "limit": 0},
    ]
    assert search_calls(rec) == ["SearchProducts", "SearchProducts"]  # the retry is visible in the event stream
    assert types(rec) == ["intent_detected", "service_call", "service_call", "partial_result", "final_answer"]  # run_query; "accepted" is emitted by POST
    assert rec.status == "done" and rec.data["relaxed"] is True and len(rec.data["products"]) == 2
    assert rec.answer.splitlines()[0] == "No exact match for 'cans', but here are headphones up to $100.00:"
    assert "Budget Earbuds (P-1002)" in rec.answer


def test_relaxed_retry_keeps_color_as_a_filter():
    rec, clients = run("show me blue cans", lambda text: [] if "cans" in text else PHONES)
    assert [c["text"] for c in clients.search_calls] == ["blue cans", "blue"]
    assert rec.answer.startswith("No exact match for 'cans', but here are blue products:")


def test_no_retry_when_first_search_found_results():
    rec, clients = run("Show me wireless headphones under $100", lambda text: PHONES)
    assert len(clients.search_calls) == 1 and search_calls(rec) == ["SearchProducts"]
    assert "relaxed" not in rec.data
    assert rec.answer.startswith("I found 2 products up to $100.00:")


def test_no_relax_when_keywords_are_the_only_filter():
    rec, clients = run("Show me unicorns", lambda text: [])
    assert rec.entities == {"keywords": ["unicorns"]}
    assert len(clients.search_calls) == 1 and search_calls(rec) == ["SearchProducts"]
    assert "relaxed" not in rec.data
    assert "couldn't find any products" in rec.answer


def test_relaxed_search_also_empty_is_reported_as_not_found():
    rec, clients = run("Show me cans under $10 headphones", lambda text: [])
    assert len(clients.search_calls) == 2 and search_calls(rec) == ["SearchProducts", "SearchProducts"]
    assert "relaxed" not in rec.data and "couldn't find any products" in rec.answer


def test_no_retry_without_keywords():
    rec, clients = run("Show me headphones under $5", lambda text: [])
    assert len(clients.search_calls) == 1 and "couldn't find any products" in rec.answer


def test_stock_check_branch_relaxes_too():
    rec, clients = run("Is the blue hoodie zzz in stock in size M?", lambda text: [] if "zzz" in text else [HOODIE])
    assert rec.intent == "stock_check" and rec.entities["keywords"] == ["zzz"]
    assert [c["text"] for c in clients.search_calls] == ["blue zzz", "blue"]
    assert all(c["limit"] == 5 for c in clients.search_calls)
    assert search_calls(rec) == ["SearchProducts", "SearchProducts", "GetStock"]
    assert rec.data["relaxed"] is True
    assert rec.answer.startswith("No exact match for 'zzz', but here is what I found:")
    assert "Classic Hoodie (P-1004): size M: 4 in stock" in rec.answer


def test_stock_check_no_relax_when_first_search_hits():
    rec, clients = run("Is the blue hoodie in stock in size M?", lambda text: [HOODIE])
    assert len(clients.search_calls) == 1 and "relaxed" not in rec.data
