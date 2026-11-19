"""Unit tests for the eval scorer's matching rules (scripts/eval_agent.py). No planner, no LLM, no network."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("eval_agent", SCRIPTS / "eval_agent.py")
ev = importlib.util.module_from_spec(_spec)
sys.modules["eval_agent"] = ev
_spec.loader.exec_module(ev)


def act(tool, **args):
    return {"tool": tool, "args": args}


def exp(tool, **args):
    return {"tool": tool, "args": args}


# ---- exact tool
def test_exact_tool_mismatch_fails_with_both_names():
    ok, reasons = ev.action_matches(exp("search_products", category="hoodies"), act("get_product", product_id="P-1"))
    assert not ok
    assert any("'get_product'" in r and "'search_products'" in r for r in reasons)


def test_tool_is_case_sensitive():
    ok, _ = ev.action_matches(exp("get_stock"), act("Get_Stock"))
    assert not ok


def test_reply_expected_with_empty_args_passes_on_reply():
    ok, reasons = ev.action_matches(exp("reply"), act("reply"))
    assert ok and reasons == []


def test_reply_expected_fails_when_a_tool_is_chosen():
    ok, _ = ev.action_matches(exp("reply"), act("search_products", category="hoodies"))
    assert not ok


def test_missing_actual_action_fails_cleanly():
    ok, reasons = ev.action_matches(exp("get_cart"), None)
    assert not ok and reasons


# ---- arg subset
def test_extra_actual_args_are_allowed():
    ok, _ = ev.action_matches(exp("search_products", category="hoodies"),
                              act("search_products", category="hoodies", sort="price_asc", color="grey"))
    assert ok


def test_missing_expected_arg_fails():
    ok, reasons = ev.action_matches(exp("search_products", category="hoodies", color="navy"),
                                    act("search_products", category="hoodies"))
    assert not ok
    assert any("color missing" in r for r in reasons)


def test_wrong_expected_arg_value_fails():
    ok, reasons = ev.action_matches(exp("search_products", category="hoodies"),
                                    act("search_products", category="jackets"))
    assert not ok and any("hoodies" in r and "jackets" in r for r in reasons)


def test_expected_empty_args_match_any_args():
    ok, _ = ev.action_matches(exp("get_cart"), act("get_cart", anything=1))
    assert ok


# ---- strings case-insensitive
def test_string_args_are_case_insensitive():
    ok, _ = ev.action_matches(exp("search_products", color="navy", category="hoodies"),
                              act("search_products", color="Navy", category="HOODIES"))
    assert ok


def test_size_one_is_case_insensitive():
    ok, _ = ev.action_matches(exp("add_to_cart", product_id="P-1043", size="ONE", color="navy"),
                              act("add_to_cart", product_id="P-1043", size="one", color="navy"))
    assert ok


def test_string_mismatch_still_fails_after_lowercasing():
    ok, _ = ev.arg_matches("color", "navy", "blue")
    assert not ok


# ---- query token containment
def test_query_containment_actual_has_extra_words():
    ok, _ = ev.arg_matches("query", "waterproof", "waterproof jacket navy")
    assert ok


def test_query_containment_fails_when_an_expected_word_is_missing():
    ok, reason = ev.arg_matches("query", "waterproof jacket", "waterproof")
    assert not ok and "lacks" in reason


def test_query_containment_is_case_insensitive():
    ok, _ = ev.arg_matches("query", "RFID", "rfid blocking")
    assert ok


def test_query_containment_word_order_does_not_matter():
    ok, _ = ev.arg_matches("query", "leather strap", "strap in leather")
    assert ok


def test_query_missing_entirely_fails():
    ok, reasons = ev.action_matches(exp("search_products", query="laptop"), act("search_products", category="backpacks"))
    assert not ok and any("query missing" in r for r in reasons)


# ---- plural strip
def test_plural_strip_on_query_tokens():
    assert ev.query_matches("leather strap", "leather straps")
    assert ev.query_matches("belt", "belts")
    assert ev.query_matches("hoodie", "hoodies")


def test_plural_strip_applies_to_expected_too():
    assert ev.query_matches("hoodies", "a hoodie")


def test_plural_strip_leaves_short_words_alone():
    assert ev.strip_plural("bus") == "bus"
    assert ev.strip_plural("hoodies") == "hoodie"
    assert ev.strip_plural("jeans") == "jean"


def test_plural_strip_does_not_make_unrelated_words_match():
    assert not ev.query_matches("bag", "bagel")


# ---- integers exact (money)
def test_money_integer_exact_match():
    ok, _ = ev.arg_matches("max_price_cents", 10000, 10000)
    assert ok


def test_money_integer_off_by_one_fails():
    ok, reason = ev.arg_matches("max_price_cents", 10000, 10001)
    assert not ok and "expected 10000" in reason


def test_money_numeric_string_equal_value_passes():
    ok, _ = ev.arg_matches("max_price_cents", 10000, "10000")
    assert ok


def test_money_numeric_string_different_value_fails():
    ok, _ = ev.arg_matches("max_price_cents", 10000, "9999")
    assert not ok


def test_money_float_is_not_exact():
    ok, _ = ev.arg_matches("max_price_cents", 10000, 10000.0)
    assert not ok


def test_quantity_integer_exact():
    ok, _ = ev.arg_matches("quantity", 2, 1)
    assert not ok


def test_bool_is_not_an_integer():
    ok, _ = ev.arg_matches("max_price_cents", 1, True)
    assert not ok


# ---- aggregation, selection, pool
def test_summarise_overall_per_tag_and_per_tool():
    results = [
        {"tags": ["basic"], "expected": {"tool": "search_products"}, "passed": True},
        {"tags": ["basic", "typo"], "expected": {"tool": "search_products"}, "passed": False},
        {"tags": ["typo"], "expected": {"tool": "reply"}, "passed": True},
    ]
    s = ev.summarise(results)
    assert s["overall"] == {"passed": 2, "total": 3, "accuracy": 0.6667}
    assert s["per_tag"]["basic"] == {"passed": 1, "total": 2, "accuracy": 0.5}
    assert s["per_tag"]["typo"] == {"passed": 1, "total": 2, "accuracy": 0.5}
    assert s["per_tool"]["search_products"] == {"passed": 1, "total": 2, "accuracy": 0.5}
    assert s["per_tool"]["reply"] == {"passed": 1, "total": 1, "accuracy": 1.0}


def test_summarise_empty_has_no_accuracy():
    assert ev.summarise([])["overall"]["accuracy"] is None


def test_select_lines_by_tag_and_limit():
    lines = [{"id": "a", "tags": ["basic"]}, {"id": "b", "tags": ["typo"]}, {"id": "c", "tags": ["basic", "typo"]}]
    assert [l["id"] for l in ev.select_lines(lines, {"typo"}, None)] == ["b", "c"]
    assert [l["id"] for l in ev.select_lines(lines, None, 2)] == ["a", "b"]


def test_pool_usable_needs_one_ok_key():
    assert ev.pool_usable([{"state": "cooling"}, {"state": "ok"}])
    assert not ev.pool_usable([{"state": "cooling"}, {"state": "disabled"}])
    assert not ev.pool_usable([])


def test_enrich_context_adds_product_summary_only_when_known():
    products = {"P-1": {"product_id": "P-1", "name": "N", "category": "hoodies", "price_cents": 100,
                        "colors": ["navy"], "sizes": ["M"], "description": "not copied"}}
    ctx = ev.enrich_context({"path": "/p/P-1", "product_id": "P-1", "signed_in": False}, products)
    assert ctx["product"] == {"product_id": "P-1", "name": "N", "category": "hoodies", "price_cents": 100,
                              "colors": ["navy"], "sizes": ["M"]}
    assert "product" not in ev.enrich_context({"path": "/shop", "signed_in": False}, products)


# ---- planner missing
def test_missing_planner_fails_with_a_clear_message():
    with pytest.raises(SystemExit) as exc:
        ev.load_planner("orchestrator.agent.no_such_planner_xyz")
    assert "planner" in str(exc.value) and "not built yet" in str(exc.value)
