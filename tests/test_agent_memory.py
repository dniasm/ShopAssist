import asyncio

import pytest

from orchestrator import checkout_flow
from orchestrator.agent.memory import MemoryReader, MemoryWorker, may_hold_fact
from orchestrator.errors import ApiError
from orchestrator.events import EventHub
from shopassist_proto import shopassist_pb2 as pb

_RESP = {"ListMessages": pb.ListMessagesResponse, "SearchFacts": pb.ListFactsResponse, "ApplyFacts": pb.ListFactsResponse,
         "GetConversation": pb.Conversation, "SetSummary": pb.Conversation, "ClearTemporalFacts": pb.ClearTemporalFactsResponse}


class Stub:
    """Records every assistant RPC; replies come from `replies` (name -> response) or are empty."""
    def __init__(self, replies=None, fail=()):
        self.calls, self.replies, self.fail = [], replies or {}, set(fail)

    def __getattr__(self, name):
        async def rpc(request, **_):
            self.calls.append((name, request))
            if name in self.fail:
                raise ApiError(503, "unavailable", "down")
            return self.replies.get(name) or _RESP[name]()
        return rpc

    def sent(self, name):
        return [r for n, r in self.calls if n == name]


class FakeClients:
    def __init__(self, stub=None):
        self.assistant = stub or Stub()
        self.customer = self

    async def ClearCart(self, request, **_):
        return pb.Cart()

    async def call(self, service, rpc, request, user_id=None):
        self.last_user = user_id
        return await rpc(request)


class FakeEmbedder:
    def __init__(self, vectors=True):
        self.vectors, self.seen = vectors, []

    async def embed(self, texts, kind):
        self.seen.append((list(texts), kind))
        return [[0.5, 0.25] for _ in texts] if self.vectors else None


class FakeLLM:
    def __init__(self, facts=None, summary="s"):
        self.facts, self.summary, self.calls = facts, summary, []

    async def complete_json(self, system, user, schema_name, schema, parse, max_tokens=None, model=None):
        self.calls.append((schema_name, model, user))
        return parse({"changes": self.facts or []} if schema_name == "facts" else {"summary": self.summary})


class Settings:
    memory_model = "mem-model"


@pytest.mark.parametrize("text,expected", [
    ("remember that I wear size M", True), ("please forget my budget", True), ("call me Sam", True),
    ("from now on show me cheap stuff", True), ("I'm looking for a black hoodie", True),
    ("my shoe size is 43", True), ("I need a jacket for rain", True), ("I want something warm", True),
    ("Looking for sunglasses under $50", True), ("hi, I'm a runner. what do you have?", True),
    ("do you have hoodies?", False), ("what size do I need?", False), ("show me backpacks", False),
    ("Can you show me my cart?", False), ("thanks!", False), ("", False),
])
def test_may_hold_fact_cases(text, expected):
    assert may_hold_fact(text) is expected


def run_worker(clients, llm, embedder, jobs):
    async def go():
        w = MemoryWorker(clients, llm, embedder, Settings())
        w.start()
        for j in jobs:
            w.submit(*j)
        await w.close()
    asyncio.run(go())


def test_worker_saves_permanent_and_temporal():
    stub = Stub()
    llm = FakeLLM(facts=[
        {"op": "remember", "kind": "permanent", "key": "shoe_size", "value": "43", "category": "shoes"},
        {"op": "remember", "kind": "temporal", "key": "need", "value": "rain jacket", "category": "jackets"},
        {"op": "remember", "kind": "temporal", "key": "need", "value": "x", "category": "furniture"},
        {"op": "forget", "kind": "permanent", "key": "budget", "value": "", "category": ""},
    ])
    emb = FakeEmbedder()
    run_worker(FakeClients(stub), llm, emb, [("u1", "c1", "my shoe size is 43, I need a rain jacket", "ok")])
    got = [(c.op, c.kind, c.key, c.category, list(c.embedding)) for c in stub.sent("ApplyFacts")[0].changes]
    assert len(got) == 3  # the invalid category is dropped
    assert got[0] == ("remember", "permanent", "shoe_size", "shoes", [0.5, 0.25])
    assert got[1][:4] == ("remember", "temporal", "need", "jackets")
    assert got[2][:3] == ("forget", "permanent", "budget") and got[2][4] == []
    assert emb.seen[0] == (["shoe_size: 43", "need: rain jacket"], "document")
    assert llm.calls[0][:2] == ("facts", "mem-model")


def test_worker_skips_fact_extraction_for_questions_but_still_summarises():
    stub = Stub()
    llm = FakeLLM()
    run_worker(FakeClients(stub), llm, FakeEmbedder(), [("u1", "c1", "do you have hoodies?", "yes")])
    assert [c[0] for c in llm.calls] == ["summary"]
    assert [n for n, _ in stub.calls] == ["ListMessages", "SetSummary"]


def test_summary_capped_200_words():
    stub = Stub()
    run_worker(FakeClients(stub), FakeLLM(summary=" ".join(f"w{i}" for i in range(500))), FakeEmbedder(), [("u1", "c1", "hello", "hi")])
    assert len(stub.sent("SetSummary")[0].summary.split()) == 200


def exchanges(n):
    msgs = []
    for i in range(n):
        msgs += [pb.ChatMessage(role="user", text=f"q{i}"), pb.ChatMessage(role="assistant", text=f"a{i} " + "x" * 700)]
    return pb.ListMessagesResponse(messages=msgs)


def test_summary_uses_recent_exchanges_only():
    # the service returns the latest `limit` messages; emulate it over 7 exchanges
    full = exchanges(7)
    class Recent(Stub):
        def __getattr__(self, name):
            inner = super().__getattr__(name)
            async def rpc(request, **kw):
                reply = await inner(request, **kw)
                if name == "ListMessages":
                    return pb.ListMessagesResponse(messages=full.messages[-request.limit:])
                return reply
            return rpc
    stub = Recent({"GetConversation": pb.Conversation(summary="OLD SUMMARY")})
    llm = FakeLLM()
    run_worker(FakeClients(stub), llm, FakeEmbedder(), [("u1", "c1", "hello", "hi")])
    assert stub.sent("ListMessages")[0].limit == 12
    prompt = llm.calls[-1][2]
    assert "User: q1" in prompt and "User: q6" in prompt and "q0" not in prompt and "OLD SUMMARY" not in prompt
    assert "x" * 601 not in prompt and "Assistant: a6 " in prompt


def test_summary_with_a_single_exchange():
    stub = Stub({"ListMessages": exchanges(1)})
    llm = FakeLLM(summary="short")
    run_worker(FakeClients(stub), llm, FakeEmbedder(), [("u1", "c1", "hello", "hi")])
    assert stub.sent("SetSummary")[0].summary == "short" and "User: q0" in llm.calls[-1][2]


def test_worker_without_llm_or_with_failures_does_not_crash():
    stub = Stub(fail={"ApplyFacts", "ListMessages"})
    run_worker(FakeClients(stub), None, FakeEmbedder(), [("u1", "c1", "I need shoes", "ok")])
    llm = FakeLLM(facts=[{"op": "remember", "kind": "permanent", "key": "k", "value": "v", "category": ""}])
    run_worker(FakeClients(stub), llm, FakeEmbedder(), [("u1", "c1", "I need shoes", "ok"), ("u1", "c1", "I want more", "ok")])
    assert len(llm.calls) == 2  # the second job ran after the first failed


def fact_rows(n):
    return pb.ListFactsResponse(facts=[pb.Fact(key=f"k{i}", value=f"v{i}") for i in range(n)])


def test_context_top5_only():
    stub = Stub({"SearchFacts": fact_rows(5), "GetConversation": pb.Conversation(summary="likes red"),
                 "ListMessages": pb.ListMessagesResponse(messages=[pb.ChatMessage(role="user", text="hi"),
                                                                   pb.ChatMessage(role="assistant", text="hey")])})
    emb = FakeEmbedder()
    block = asyncio.run(MemoryReader(FakeClients(stub), emb).context("u1", "c1", "any hoodies"))
    search = stub.sent("SearchFacts")[0]
    assert (search.limit, search.min_score) == (5, 0.45) and list(search.query_embedding) == [0.5, 0.25]
    assert block["facts"] == [f"k{i}: v{i}" for i in range(5)]
    assert block["summary"] == "likes red"
    assert block["latest"] == [{"role": "user", "text": "hi", "cards": []}, {"role": "assistant", "text": "hey", "cards": []}]
    assert emb.seen == [(["any hoodies likes red"], "query")]
    assert stub.sent("ListMessages")[0].limit == 2


def test_context_without_voyage_uses_keywords():
    stub = Stub({"SearchFacts": fact_rows(1)})
    block = asyncio.run(MemoryReader(FakeClients(stub), FakeEmbedder(vectors=False)).context("u1", "c1", "size"))
    search = stub.sent("SearchFacts")[0]
    assert list(search.query_embedding) == [] and search.query_text.startswith("size")
    assert block["facts"] == ["k0: v0"]


def test_context_survives_service_failure():
    stub = Stub(fail={"SearchFacts", "GetConversation", "ListMessages"})
    block = asyncio.run(MemoryReader(FakeClients(stub), FakeEmbedder()).context("u1", "c1", "hi"))
    assert block == {"facts": [], "summary": "", "latest": []}


def confirmed_event():
    order = pb.Order(order_id="O-1", lines=[pb.OrderLine(category="shoes"), pb.OrderLine(category="jackets"), pb.OrderLine(category="shoes")])
    return pb.CheckoutEvent(order_id="O-1", step=pb.FINISHED, state=pb.STEP_SUCCEEDED, order=order)


class FakeCall:
    def __init__(self, final):
        self.final = final

    async def read(self):
        return self.final

    def cancel(self):
        pass


def run_forward(clients, event, fresh_hub=None):
    hub = fresh_hub or EventHub(10, 10)
    first = pb.CheckoutEvent(order_id="O-1", step=pb.CHARGING, state=pb.STEP_STARTED)
    asyncio.run(checkout_flow.forward(hub, clients, set(), "u1", FakeCall(event), first, 1))
    return hub


def test_confirmed_order_clears_temporal_category():
    stub = Stub()
    clients = FakeClients(stub)
    run_forward(clients, confirmed_event())
    sent = stub.sent("ClearTemporalFacts")
    assert len(sent) == 1 and sorted(sent[0].categories) == ["jackets", "shoes"] and clients.last_user == "u1"


def test_failed_order_clears_nothing():
    stub = Stub()
    event = confirmed_event()
    event.state = pb.STEP_FAILED
    run_forward(FakeClients(stub), event)
    assert stub.sent("ClearTemporalFacts") == []


def test_clear_failure_does_not_break_checkout():
    hub = run_forward(FakeClients(Stub(fail={"ClearTemporalFacts"})), confirmed_event())
    events, _ = hub.replay("u1", 0)
    assert [e["type"] for e in events][-1] == "done"
