"""AssistantService: per-user chat history, fact memory rules and fact search. Real service, real Postgres."""
from datetime import datetime, timedelta

import grpc
import psycopg
import pytest

from shopassist_proto import shopassist_pb2 as pb


def as_user(user_id):
    return (("x-user-id", user_id),)


def code_of(fn):
    with pytest.raises(grpc.RpcError) as e:
        fn()
    return e.value.code()


def remember(a, user, key, value, kind="permanent", category="", embedding=()):
    return a.ApplyFacts(pb.ApplyFactsRequest(changes=[pb.FactChange(
        op="remember", kind=kind, key=key, value=value, category=category, embedding=embedding)]), metadata=as_user(user))


def forget(a, user, key, value="*"):
    return a.ApplyFacts(pb.ApplyFactsRequest(changes=[pb.FactChange(op="forget", key=key, value=value)]), metadata=as_user(user))


def facts(a, user):
    return a.ListFacts(pb.ListFactsRequest(), metadata=as_user(user)).facts


# ---------------- conversations ----------------

def test_conversation_isolated_per_user(assistant):
    conv = assistant.CreateConversation(pb.CreateConversationRequest(title="Mine"), metadata=as_user("U-01"))
    cid = conv.conversation_id
    assistant.AppendMessage(pb.AppendMessageRequest(conversation_id=cid, role="user", text="hi"), metadata=as_user("U-01"))
    b = as_user("U-02")
    nf = grpc.StatusCode.NOT_FOUND
    assert code_of(lambda: assistant.GetConversation(pb.GetConversationRequest(conversation_id=cid), metadata=b)) == nf
    assert code_of(lambda: assistant.RenameConversation(pb.RenameConversationRequest(conversation_id=cid, title="x"), metadata=b)) == nf
    assert code_of(lambda: assistant.ListMessages(pb.ListMessagesRequest(conversation_id=cid), metadata=b)) == nf
    assert code_of(lambda: assistant.AppendMessage(pb.AppendMessageRequest(conversation_id=cid, role="user", text="x"), metadata=b)) == nf
    assert code_of(lambda: assistant.SetSummary(pb.SetSummaryRequest(conversation_id=cid, summary="x"), metadata=b)) == nf
    assert code_of(lambda: assistant.DeleteConversation(pb.DeleteConversationRequest(conversation_id=cid), metadata=b)) == nf
    assert list(assistant.ListConversations(pb.ListConversationsRequest(), metadata=b).conversations) == []
    again = assistant.GetConversation(pb.GetConversationRequest(conversation_id=cid), metadata=as_user("U-01"))
    assert again.title == "Mine"
    texts = [m.text for m in assistant.ListMessages(pb.ListMessagesRequest(conversation_id=cid), metadata=as_user("U-01")).messages]
    assert texts == ["hi"]


def test_calls_need_a_user(assistant):
    assert code_of(lambda: assistant.ListConversations(pb.ListConversationsRequest())) == grpc.StatusCode.UNAUTHENTICATED


def test_delete_conversation_deletes_messages(assistant, assistant_running):
    u = as_user("U-01")
    conv = assistant.CreateConversation(pb.CreateConversationRequest(title="t"), metadata=u)
    for text in ("a", "b"):
        assistant.AppendMessage(pb.AppendMessageRequest(conversation_id=conv.conversation_id, role="user", text=text), metadata=u)
    assistant.DeleteConversation(pb.DeleteConversationRequest(conversation_id=conv.conversation_id), metadata=u)
    with psycopg.connect(assistant_running.url) as c:
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0


def test_list_conversations_newest_first(assistant):
    u = as_user("U-01")
    first = assistant.CreateConversation(pb.CreateConversationRequest(title="first"), metadata=u)
    second = assistant.CreateConversation(pb.CreateConversationRequest(title="second"), metadata=u)

    def ids():
        return [c.conversation_id for c in assistant.ListConversations(pb.ListConversationsRequest(), metadata=u).conversations]

    assert ids() == [second.conversation_id, first.conversation_id]
    assistant.AppendMessage(pb.AppendMessageRequest(conversation_id=first.conversation_id, role="user", text="bump"), metadata=u)
    assert ids() == [first.conversation_id, second.conversation_id]


def test_messages_summary_and_validation(assistant):
    u = as_user("U-01")
    cid = assistant.CreateConversation(pb.CreateConversationRequest(), metadata=u).conversation_id
    m1 = assistant.AppendMessage(pb.AppendMessageRequest(conversation_id=cid, role="user", text="one"), metadata=u)
    m2 = assistant.AppendMessage(pb.AppendMessageRequest(conversation_id=cid, role="assistant", text="two", cards_json='[{"a": 1}]'), metadata=u)
    assert m2.message_id > m1.message_id and m2.created_at
    msgs = assistant.ListMessages(pb.ListMessagesRequest(conversation_id=cid), metadata=u).messages
    assert [(m.role, m.text) for m in msgs] == [("user", "one"), ("assistant", "two")]
    assert msgs[0].cards_json == "" and '"a"' in msgs[1].cards_json
    last = assistant.ListMessages(pb.ListMessagesRequest(conversation_id=cid, limit=1), metadata=u).messages
    assert [m.text for m in last] == ["two"]
    assert assistant.SetSummary(pb.SetSummaryRequest(conversation_id=cid, summary="s"), metadata=u).summary == "s"
    assert assistant.RenameConversation(pb.RenameConversationRequest(conversation_id=cid, title="New"), metadata=u).title == "New"
    bad = grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(lambda: assistant.AppendMessage(pb.AppendMessageRequest(conversation_id=cid, role="bot", text="x"), metadata=u)) == bad
    assert code_of(lambda: assistant.AppendMessage(
        pb.AppendMessageRequest(conversation_id=cid, role="user", text="x", cards_json="{nope"), metadata=u)) == bad
    assert code_of(lambda: assistant.RenameConversation(pb.RenameConversationRequest(conversation_id=cid, title="  "), metadata=u)) == bad


# ---------------- facts ----------------

def test_permanent_single_key_replaced(assistant):
    remember(assistant, "U-01", "size_top", "M")
    remember(assistant, "U-01", "size_top", "L")
    got = facts(assistant, "U-01")
    assert [(f.key, f.value, f.kind) for f in got] == [("size_top", "L", "permanent")]
    assert got[0].fact_id.startswith("F-")


def test_key_aliases_and_favorite_prefix_are_single_valued(assistant):
    remember(assistant, "U-01", "Size", "M")
    remember(assistant, "U-01", "favourite_colour", "red")
    remember(assistant, "U-01", "favorite_colour", "blue")
    assert sorted((f.key, f.value) for f in facts(assistant, "U-01")) == [("favorite_colour", "blue"), ("size_top", "M")]


def test_list_key_collects_without_duplicates(assistant):
    for brand in ("Nike", "nike", "Adidas"):
        remember(assistant, "U-01", "liked_brands", brand)
    assert sorted(f.value for f in facts(assistant, "U-01")) == ["Adidas", "Nike"]


def test_facts_are_per_user(assistant):
    remember(assistant, "U-01", "shoe_size", "42")
    assert list(facts(assistant, "U-02")) == []


def test_forget_star_removes_key(assistant):
    for brand in ("Nike", "Adidas"):
        remember(assistant, "U-01", "liked_brands", brand)
    remember(assistant, "U-01", "shoe_size", "42")
    left = forget(assistant, "U-01", "liked_brands").facts
    assert [f.key for f in left] == ["shoe_size"]
    left = forget(assistant, "U-01", "shoe_size", "43").facts  # a value that is not stored removes nothing
    assert [f.key for f in left] == ["shoe_size"]


def test_temporal_expires_after_30_days(assistant, assistant_clock):
    remember(assistant, "U-01", "trip", "Berlin in May", kind="temporal")
    remember(assistant, "U-01", "shoe_size", "42")
    (trip,) = [f for f in facts(assistant, "U-01") if f.key == "trip"]
    assert datetime.fromisoformat(trip.expires_at) == assistant_clock.now() + timedelta(days=30)
    assistant_clock.advance(days=29)
    assert len(facts(assistant, "U-01")) == 2
    assistant_clock.advance(days=2)
    assert [f.key for f in facts(assistant, "U-01")] == ["shoe_size"]


def test_clear_temporal_by_category(assistant):
    remember(assistant, "U-01", "wants", "red tee", kind="temporal", category="t-shirts")
    remember(assistant, "U-01", "wants_shoes", "boots", kind="temporal", category="shoes")
    remember(assistant, "U-01", "liked_brands", "Nike", category="t-shirts")
    r = assistant.ClearTemporalFacts(pb.ClearTemporalFactsRequest(categories=["t-shirts"]), metadata=as_user("U-01"))
    assert r.removed == 1
    assert sorted(f.key for f in facts(assistant, "U-01")) == ["liked_brands", "wants_shoes"]


def test_clear_temporal_without_categories_removes_nothing(assistant):
    remember(assistant, "U-01", "wants", "red tee", kind="temporal", category="t-shirts")
    r = assistant.ClearTemporalFacts(pb.ClearTemporalFactsRequest(), metadata=as_user("U-01"))
    assert r.removed == 0 and len(facts(assistant, "U-01")) == 1


def test_invalid_fact_changes_are_rejected(assistant):
    bad = grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(lambda: remember(assistant, "U-01", "", "x")) == bad
    assert code_of(lambda: remember(assistant, "U-01", "k", "")) == bad
    assert code_of(lambda: remember(assistant, "U-01", "k", "v", kind="forever")) == bad
    assert code_of(lambda: assistant.ApplyFacts(pb.ApplyFactsRequest(changes=[pb.FactChange(op="x", key="k")]), metadata=as_user("U-01"))) == bad


# ---------------- search ----------------

def search(a, user, text="", embedding=(), limit=0, min_score=0.0):
    return a.SearchFacts(pb.SearchFactsRequest(query_text=text, query_embedding=embedding, limit=limit, min_score=min_score),
                         metadata=as_user(user)).facts


def test_search_top5_min_score(assistant):
    for i in range(8):  # similarity falls as i grows
        remember(assistant, "U-01", f"item_{i}", f"v{i}", embedding=[1.0, i * 0.2, 0.0])
    remember(assistant, "U-01", "far", "away", embedding=[0.0, 0.0, 1.0])  # orthogonal: score 0
    got = search(assistant, "U-01", embedding=[1.0, 0.0, 0.0], min_score=0.2)
    assert [f.key for f in got] == [f"item_{i}" for i in range(5)]
    assert all(a.score >= b.score for a, b in zip(got, got[1:])) and got[0].score == pytest.approx(1.0)
    strict = search(assistant, "U-01", embedding=[1.0, 0.0, 0.0], min_score=0.99)
    assert [f.key for f in strict] == ["item_0"]
    assert [f.key for f in search(assistant, "U-01", embedding=[1.0, 0.0, 0.0], limit=2)] == ["item_0", "item_1"]
    assert list(search(assistant, "U-02", embedding=[1.0, 0.0, 0.0])) == []


def test_search_keyword_fallback_without_embeddings(assistant):
    remember(assistant, "U-01", "size_top", "M")
    remember(assistant, "U-01", "liked_brands", "Nike")
    got = search(assistant, "U-01", text="what size am i", min_score=0.45)
    assert [f.key for f in got] == ["size_top"]
    assert list(search(assistant, "U-01", text="what is it", min_score=0.45)) == []
