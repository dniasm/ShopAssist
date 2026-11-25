import asyncio

import pytest

from orchestrator import checkout_flow
from orchestrator.agent.memory import MemoryReader, MemoryWorker, may_hold_fact
from orchestrator.errors import ApiError
from orchestrator.events import EventHub
from shopassist_proto import shopassist_pb2 as pb

_RESP = {"ListMessages": pb.ListMessagesResponse, "ListFacts": pb.ListFactsResponse, "ApplyFacts": pb.ListFactsResponse,
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
    assert got[2][:3] == ("forget", "permanent", "budget") and got[2][4] == []  # (empty forget value becomes "*", see below)
    assert stub.sent("ApplyFacts")[0].changes[2].value == "*"
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


def facts_reply(*facts):
    return pb.ListFactsResponse(facts=[pb.Fact(key=k, value=v, category=c, kind="permanent") for k, v, c in facts])


STORED = facts_reply(("budget", "150", ""), ("size_top", "L", ""), ("fit", "tight", "t-shirts"), ("fit", "loose", "hoodies"),
                     ("liked_colors", "navy", ""), ("liked_colors", "olive", ""))


def test_context_lists_names_without_values_with_one_list_facts_call():
    stub = Stub({"ListFacts": STORED, "GetConversation": pb.Conversation(summary="likes red"),
                 "ListMessages": pb.ListMessagesResponse(messages=[pb.ChatMessage(role="user", text="hi"),
                                                                   pb.ChatMessage(role="assistant", text="hey")])})
    block = asyncio.run(MemoryReader(FakeClients(stub)).context("u1", "c1", "any hoodies"))
    assert block["names"] == "budget, size_top, fit (t-shirts), fit (hoodies), liked_colors"
    assert "150" not in block["names"] and "navy" not in block["names"]
    assert len(stub.sent("ListFacts")) == 1 and not stub.sent("SearchFacts")
    assert len(block["all"]) == 6 and block["facts"] == []  # no word of the message names a fact
    assert block["summary"] == "likes red"
    assert block["latest"] == [{"role": "user", "text": "hi", "cards": []}, {"role": "assistant", "text": "hey", "cards": []}]
    assert stub.sent("ListMessages")[0].limit == 2


def test_keyword_pass_adds_matching_facts_with_values():
    stub = Stub({"ListFacts": STORED})
    block = asyncio.run(MemoryReader(FakeClients(stub)).context("u1", "c1", "what's my budget"))
    assert block["facts"] == ["budget: 150"]
    block = asyncio.run(MemoryReader(FakeClients(stub)).context("u1", "c1", "navy"))
    assert block["facts"] == ["liked_colors: navy"]


def test_keyword_pass_is_capped_at_five():
    stub = Stub({"ListFacts": facts_reply(*[(f"k{i}", "word", "") for i in range(8)])})
    block = asyncio.run(MemoryReader(FakeClients(stub)).context("u1", "c1", "word"))
    assert len(block["facts"]) == 5


def test_context_survives_service_failure():
    stub = Stub(fail={"ListFacts", "GetConversation", "ListMessages"})
    block = asyncio.run(MemoryReader(FakeClients(stub)).context("u1", "c1", "hi"))
    assert block == {"facts": [], "names": "", "all": [], "summary": "", "latest": []}


def test_parse_keeps_one_off_wishes_temporal_and_lasting_things_permanent():
    from orchestrator.agent.memory import _parse_changes
    got = _parse_changes({"changes": [
        {"op": "remember", "kind": "permanent", "key": "want", "value": "loose t-shirt", "category": "t-shirts"},
        {"op": "remember", "kind": "temporal", "key": "fit", "value": "loose", "category": "t-shirts"},
        {"op": "remember", "kind": "temporal", "key": "budget", "value": "150", "category": ""}]})
    assert [(c["key"], c["kind"]) for c in got] == [("want", "temporal"), ("fit", "permanent"), ("budget", "permanent")]


def test_extraction_prompt_teaches_lasting_versus_one_off_and_colour_lists():
    from orchestrator.agent.memory import FACTS_SYSTEM
    for must in ("liked_colors", "two liked_colors rows", "NEVER change the saved fit", "temporal", "per category"):
        assert must in FACTS_SYSTEM


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
    async def go():
        await checkout_flow.forward(hub, clients, set(), "u1", FakeCall(event), first, 1)
        await asyncio.gather(*checkout_flow._background)  # the facts are cleared after `done`, in the background
    asyncio.run(go())
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


def test_done_is_published_before_the_facts_are_cleared():
    order_seen = []

    class SlowStub(Stub):
        def __getattr__(self, name):
            async def rpc(request, **_):
                order_seen.append(("rpc", name))
                return _RESP[name]()
            return rpc

    class Hub(EventHub):
        def publish(self, user_id, channel, type_, data):
            order_seen.append(("event", type_))
            return super().publish(user_id, channel, type_, data)

    run_forward(FakeClients(SlowStub()), confirmed_event(), fresh_hub=Hub(10, 10))
    assert order_seen.index(("event", "done")) < order_seen.index(("rpc", "ClearTemporalFacts"))


class WatchCall:
    def __init__(self, events):
        self.events = events

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self.events:
            yield e
        await asyncio.sleep(3600)

    def cancel(self):
        pass


class WatchClients(FakeClients):
    def __init__(self, stub, events):
        super().__init__(stub)
        self.order = self
        self.events, self.gets = events, []

    def WatchOrders(self, request, **_):
        return WatchCall(self.events)

    async def GetOrder(self, request, **_):
        self.gets.append(request.order_id)
        return pb.Order(order_id=request.order_id, lines=[pb.OrderLine(category="shoes")])


def test_order_watcher_clears_temporal_facts_once_when_an_order_is_confirmed():
    from orchestrator.order_watch import OrderWatchers
    stub = Stub()
    events = [pb.OrderEvent(event_id=1, order_id="", status=0), pb.OrderEvent(event_id=2, order_id="O-1", status=pb.PENDING),
              pb.OrderEvent(event_id=3, order_id="O-1", status=pb.CONFIRMED),
              pb.OrderEvent(event_id=3, order_id="O-1", status=pb.CONFIRMED),  # a replay of the same order
              pb.OrderEvent(event_id=4, order_id="O-1", status=pb.PACKED)]
    clients = WatchClients(stub, events)

    async def go():
        watchers = OrderWatchers(clients, EventHub(10, 10), 30, 0.1)
        watchers.socket_opened("u1")
        await asyncio.sleep(0.3)
        await watchers.close()
    asyncio.run(go())
    sent = stub.sent("ClearTemporalFacts")
    assert len(sent) == 1 and list(sent[0].categories) == ["shoes"] and clients.gets == ["O-1"]


def test_order_watcher_survives_a_failing_clear():
    from orchestrator.order_watch import OrderWatchers
    events = [pb.OrderEvent(event_id=1, order_id="O-1", status=pb.CONFIRMED)]
    hub = EventHub(10, 10)

    async def go():
        watchers = OrderWatchers(WatchClients(Stub(fail={"ClearTemporalFacts"}), events), hub, 30, 0.1)
        watchers.socket_opened("u1")
        await asyncio.sleep(0.3)
        await watchers.close()
    asyncio.run(go())
    assert [e["data"]["status"] for e in hub.replay("u1", 0)[0]] == ["CONFIRMED"]


def test_worker_queue_is_bounded_and_close_does_not_wait_forever(monkeypatch):
    from orchestrator.agent import memory

    class Stuck(FakeLLM):
        async def complete_json(self, *a, **kw):
            await asyncio.sleep(3600)

    monkeypatch.setattr(memory, "QUEUE_MAX", 2)
    monkeypatch.setattr(memory, "CLOSE_WAIT_SECONDS", 0.2)

    async def go():
        w = MemoryWorker(FakeClients(), Stuck(), FakeEmbedder(), Settings())
        w.start()
        for i in range(6):
            w.submit("u1", "c1", "my size is M", "ok")
        await asyncio.wait_for(w.close(), 5)
        return w.dropped
    assert asyncio.run(go()) >= 3


def test_forget_without_a_value_is_sent_as_the_whole_key():
    from orchestrator.agent.memory import _parse_changes
    got = _parse_changes({"changes": [{"op": "forget", "kind": "permanent", "key": "budget", "value": "", "category": ""}]})
    assert got[0]["value"] == "*"
