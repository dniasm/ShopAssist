"""The pure fact rules: key normalisation, change planning and ranking (no service needed)."""
from datetime import datetime, timedelta, timezone

from services.assistant import facts

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def row(fact_id, key, value, kind="permanent", embedding=None, expires_at=None):
    return {"fact_id": fact_id, "kind": kind, "key": key, "value": value, "category": "", "embedding": embedding, "expires_at": expires_at}


def test_normalise_key():
    assert facts.normalise_key(" Favourite Colour ") == "favorite_colour"
    assert facts.normalise_key("size") == "size_top"
    assert facts.normalise_key("Name") == "preferred_name"
    assert facts.normalise_key("Liked-Brands") == "liked_brands"


def test_is_single():
    assert facts.is_single("size_top") and facts.is_single("favorite_team") and facts.is_single("preferred_store")
    assert not facts.is_single("liked_brands")


def test_plan_changes_replace_add_and_forget():
    existing = [row("F-1", "size_top", "M"), row("F-2", "liked_brands", "Nike")]
    changes = [{"op": "remember", "kind": "permanent", "key": "size_top", "value": "L"},
               {"op": "remember", "kind": "permanent", "key": "liked_brands", "value": "NIKE"},
               {"op": "remember", "kind": "temporal", "key": "trip", "value": "x", "category": "Travel"},
               {"op": "forget", "key": "liked_brands", "value": "*"}]
    inserts, deletes = facts.plan_changes(existing, changes, now=NOW)
    assert sorted(deletes) == ["F-1", "F-2"]
    assert sorted((r["key"], r["value"]) for r in inserts) == [("size_top", "L"), ("trip", "x")]
    trip = next(r for r in inserts if r["key"] == "trip")
    assert trip["expires_at"] == NOW + timedelta(days=30) and trip["category"] == "travel"


def test_plan_changes_forget_removes_a_fact_added_earlier_in_the_same_batch():
    changes = [{"op": "remember", "kind": "permanent", "key": "liked_brands", "value": "Nike"},
               {"op": "forget", "key": "liked_brands", "value": "nike"}]
    assert facts.plan_changes([], changes, now=NOW) == ([], [])


def test_rank_drops_expired_and_orders_by_score():
    rows = [row("a", "k", "v", "temporal", [1.0, 0.0], NOW - timedelta(seconds=1)),
            row("b", "k", "v", embedding=[1.0, 1.0]),
            row("c", "k", "v", embedding=[1.0, 0.0])]
    got = facts.rank(rows, "", [1.0, 0.0], 5, 0.1, now=NOW)
    assert [f["fact_id"] for f in got] == ["c", "b"]
