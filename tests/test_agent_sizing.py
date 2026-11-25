"""Fit-based size suggestions and the fit precedence: pure functions, no services."""
import pytest

from orchestrator.agent.sizing import base_size, resolve_fit, suggest_sizes


@pytest.mark.parametrize("base, fit, category, steps, want", [
    ("L", "tight", "t-shirts", 1, ["L", "M"]),
    ("L", "loose", "hoodies", 1, ["L", "XL"]),
    ("L", "regular", "t-shirts", 1, ["L"]),
    ("XS", "tight", "t-shirts", 1, ["XS"]),
    ("XXL", "loose", "jackets", 1, ["XXL"]),
    ("32", "tight", "jeans", 1, ["32", "30"]),
    ("32", "loose", "jeans", 1, ["32", "34"]),
    ("43", "loose", "shoes", 1, ["43"]),
    ("L", "loose", "t-shirts", 2, ["L", "XL", "XXL"]),
    ("M", "tight", "t-shirts", 2, ["M", "S", "XS"]),
    ("32", "tight", "jeans", 2, ["32", "30", "28"]),
    ("S", "loose", "t-shirts", 3, ["S", "M", "L", "XL"]),
    ("S", "loose", "t-shirts", 9, ["S", "M", "L", "XL"]),  # capped at 3 steps
    ("S", "loose", "t-shirts", 0, ["S", "M"]),  # at least one step
    ("", "loose", "t-shirts", 1, []),
    ("43", "tight", "shoes", 3, ["43"]),
])
def test_suggest_sizes_table(base, fit, category, steps, want):
    assert suggest_sizes(base, fit, category, None, steps) == want


def test_suggest_sizes_keeps_only_available_ones():
    assert suggest_sizes("L", "loose", "t-shirts", ["S", "M", "L"]) == ["L"]
    assert suggest_sizes("L", "tight", "t-shirts", ["m", "l", "xl"]) == ["L", "M"]
    assert suggest_sizes("L", "loose", "t-shirts", []) == ["L", "XL"]  # nothing given: no filter


def fact(key, value, category="", kind="permanent"):
    return {"key": key, "value": value, "category": category, "kind": kind}


def test_base_size_per_category():
    facts = [fact("size_top", "L"), fact("size_bottom", "32"), fact("shoe_size", "43")]
    assert [base_size(facts, c) for c in ("t-shirts", "hoodies", "jackets", "jeans", "shoes", "watches")] == ["L", "L", "L", "32", "43", ""]


def test_fit_precedence():
    saved = [fact("fit", "tight", "t-shirts"), fact("fit", "loose")]
    want_loose = fact("want", "a loose fitting tee", "t-shirts", "temporal")
    assert resolve_fit("show me slim t-shirts", [*saved, want_loose], "t-shirts") == "tight"  # the message first
    assert resolve_fit("loose please", saved, "t-shirts") == "loose"  # beats the saved tight
    assert resolve_fit("any t-shirts", [*saved, want_loose], "t-shirts") == "loose"  # open want beats saved tight
    assert resolve_fit("any t-shirts", saved, "t-shirts") == "tight"  # saved for the category
    assert resolve_fit("any hoodies", saved, "hoodies") == "loose"  # saved for all categories
    assert resolve_fit("any hoodies", [saved[0]], "hoodies") == ""  # nothing says anything
    assert resolve_fit("a want for hoodies", [fact("want", "loose tee", "t-shirts", "temporal")], "hoodies") == ""
    for word, fit in (("fitted", "tight"), ("oversized", "loose"), ("relaxed", "loose"), ("baggy", "loose")):
        assert resolve_fit(f"{word} jackets", [], "jackets") == fit
