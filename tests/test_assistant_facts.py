"""The pure fact rules: key normalisation, change planning and ranking (no service needed)."""
from datetime import datetime, timedelta, timezone

from services.assistant import facts

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def row(fact_id, key, value, kind="permanent", embedding=None, expires_at=None):
    return {"fact_id": fact_id, "kind": kind, "key": key, "value": value, "category": "", "embedding": embedding, "expires_at": expires_at}


def test_normalise_key():
    assert facts.normalise_key(" Favourite Colour ") == "liked_colors"
    assert facts.normalise_key("favorite_colors") == "liked_colors"
    assert facts.normalise_key("Liked Colour") == "liked_colors"
    assert facts.normalise_key("size") == "size_top"
    assert facts.normalise_key("Name") == "preferred_name"
    assert facts.normalise_key("Liked-Brands") == "liked_brands"


def test_is_single():
    for key in ("preferred_name", "height", "weight", "budget", "size_top", "size_bottom", "shoe_size"):
        assert facts.is_single(key)
    for key in ("liked_colors", "disliked_colors", "liked_brands", "styles", "need", "want", "fit", "favorite_team", "preferred_store"):
        assert not facts.is_single(key)


def remember(key, value, category="", kind="permanent"):
    return {"op": "remember", "kind": kind, "key": key, "value": value, "category": category}


def plan(existing, *changes):
    inserted, deleted = facts.plan_changes(existing, list(changes), now=NOW)
    return sorted((r["key"], r["value"], r["category"], r["kind"]) for r in inserted), sorted(deleted)


def test_colours_are_collected_not_replaced():
    inserted, deleted = plan([], remember("liked_colors", "navy"), remember("liked_colors", "olive"), remember("liked_colors", "NAVY"))
    assert [(k, v) for k, v, *_ in inserted] == [("liked_colors", "navy"), ("liked_colors", "olive")] and deleted == []


def test_fit_is_single_per_category():
    inserted, _ = plan([], remember("fit", "tight", "t-shirts"), remember("fit", "loose", "hoodies"))
    assert [(v, c) for _, v, c, _ in inserted] == [("loose", "hoodies"), ("tight", "t-shirts")]  # both kept
    inserted, deleted = plan([row("F-1", "fit", "tight") | {"category": "t-shirts"}, row("F-2", "fit", "loose") | {"category": "hoodies"}],
                             remember("fit", "loose", "t-shirts"))
    assert deleted == ["F-1"] and [(v, c) for _, v, c, _ in inserted] == [("loose", "t-shirts")]  # replaced only for that category
    _, deleted = plan([row("F-3", "fit", "regular")], remember("fit", "tight"))
    assert deleted == ["F-3"]  # category "" replaces category ""


def test_a_temporal_want_leaves_the_permanent_fit_alone_and_expires():
    existing = [row("F-1", "fit", "tight") | {"category": "t-shirts"}]
    inserted, deleted = facts.plan_changes(existing, [remember("want", "loose t-shirt", "t-shirts", kind="permanent")], now=NOW)
    assert deleted == [] and inserted[0]["kind"] == "temporal" and inserted[0]["expires_at"] == NOW + timedelta(days=30)


def test_no_wildcard_single_keys():
    existing = [row("F-1", "favorite_team", "a")]
    _, deleted = plan(existing, remember("favorite_team", "b"))
    assert deleted == []


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
