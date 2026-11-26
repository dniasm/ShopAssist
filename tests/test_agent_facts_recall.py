"""Recall by reference ("my favorite color", "my usual size") and rejection of vague remembered values."""
import pytest

from orchestrator.agent.memory import _parse_changes, is_vague_value, keyword_facts, recall_lines
from orchestrator.agent.prompt import SYSTEM_PROMPT, TOOLS
from services.assistant import facts as rules

FACTS = [{"key": "liked_colors", "value": "black", "category": "", "kind": "permanent"},
         {"key": "fit", "value": "loose", "category": "", "kind": "permanent"},
         {"key": "size_top", "value": "L", "category": "", "kind": "permanent"},
         {"key": "budget", "value": "150", "category": "", "kind": "permanent"},
         {"key": "preferred_name", "value": "Asmin", "category": "", "kind": "permanent"},
         {"key": "height", "value": "163cm", "category": "", "kind": "permanent"}]


def keys(query):
    return [f["key"] for f in keyword_facts(FACTS, query)]


@pytest.mark.parametrize("query", ["i'd like my favorite color", "my preferred colour", "yeah my favourite colours", "my usual color"])
def test_colour_references_find_liked_colors(query):
    assert "liked_colors" in keys(query)


def test_reference_puts_the_value_into_the_turn_facts():
    assert "liked_colors: black" in [f"{f['key']}: {f['value']}" for f in keyword_facts(FACTS, "i'd like my favorite color")]
    assert "liked_colors: black" in recall_lines(FACTS, [], "my favorite colour")


def test_other_references():
    assert "size_top" in keys("my usual size")
    assert "fit" in keys("my fit") and "budget" in keys("my budget") and "preferred_name" in keys("my name")


def test_unrelated_message_matches_nothing():
    assert keys("show me a red hoodie") == []


@pytest.mark.parametrize("value", ["favorite color", "my favourite colour", "preferred colors", "the same one", "my usual size", "colors", "  "])
def test_vague_values_are_dropped(value):
    assert is_vague_value("liked_colors", value) or is_vague_value("size_top", value)
    change = {"op": "remember", "kind": "permanent", "key": "liked_colors", "value": value, "category": ""}
    assert _parse_changes({"changes": [change]}) == []
    inserts, _ = rules.plan_changes([], [change])
    assert inserts == []


def test_real_values_and_forgets_survive():
    good = {"op": "remember", "kind": "permanent", "key": "liked_colors", "value": "navy", "category": ""}
    forget = {"op": "forget", "kind": "permanent", "key": "liked_colors", "value": "favorite color", "category": ""}
    assert len(_parse_changes({"changes": [good, forget]})) == 2
    assert [r["value"] for r in rules.plan_changes([], [good])[0]] == ["navy"]


def test_prompt_token_limits_and_rule():
    assert len(SYSTEM_PROMPT) // 4 < 600 and len(str(TOOLS)) // 4 <= 700
    assert "never ask for it again" in SYSTEM_PROMPT
