"""The rule-based planner (no LLM, no network): what it decides for representative shop questions, and how well it does
on the whole labelled eval set (tests/eval/queries.jsonl)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from orchestrator.agent import rules

EVAL = Path(__file__).parent / "eval" / "queries.jsonl"
_spec = importlib.util.spec_from_file_location("eval_agent", Path(__file__).resolve().parents[1] / "scripts" / "eval_agent.py")
ev = importlib.util.module_from_spec(_spec)
sys.modules["eval_agent"] = ev
_spec.loader.exec_module(ev)
SHOP = {"path": "/shop", "signed_in": False}
IN = {"path": "/shop", "signed_in": True, "user_id": "U-01"}


def plan(query, history=(), context=None, tool_results=()):
    return rules.plan(query, list(history), context or SHOP, list(tool_results))


CASES = [
    ("headphones under 100", [], SHOP, "search_products", {"category": "headphones", "max_price_cents": 10000}),
    ("show me hoodies", [], SHOP, "search_products", {"category": "hoodies"}),
    ("black jeans size 32", [], SHOP, "search_products", {"category": "jeans", "color": "black", "size": "32"}),
    ("sunglasses between 50 and 100 bucks", [], SHOP, "search_products",
     {"category": "sunglasses", "min_price_cents": 5000, "max_price_cents": 10000}),
    ("cheapest watch you've got", [], SHOP, "search_products", {"category": "watches", "sort": "price_asc"}),
    ("best rated sunglasses", [], SHOP, "search_products", {"category": "sunglasses", "sort": "rating"}),
    ("bougie sunnies pls", [], SHOP, "search_products", {"category": "sunglasses"}),            # slang
    ("ayy need a fresh pair of kicks", [], SHOP, "search_products", {"category": "shoes"}),      # slang
    ("hodies in blak", [], SHOP, "search_products", {"category": "hoodies", "color": "black"}),  # typos
    ("watchs by Lorrimere", [], SHOP, "search_products", {"category": "watches", "brand": "Lorrimere"}),
    ("waterproof jacket under 150 in navy, size L", [], SHOP, "search_products",
     {"category": "jackets", "color": "navy", "size": "L", "max_price_cents": 15000, "query": "waterproof"}),
    ("is this in M?", [], {"path": "/p/P-1006", "product_id": "P-1006", "signed_in": True}, "get_stock",
     {"product_id": "P-1006", "size": "M"}),
    ("how much is this?", [], {"path": "/p/P-1043", "product_id": "P-1043", "signed_in": True}, "get_product",
     {"product_id": "P-1043"}),
    ("show me P-1020", [], SHOP, "get_product", {"product_id": "P-1020"}),
    ("what about in navy?", [{"user": "show me hoodies", "assistant": "Here are some hoodies."}], SHOP, "search_products",
     {"category": "hoodies", "color": "navy"}),                                                   # a follow-up keeps the category
    ("the second one", [{"user": "show headphones", "assistant": "1. A (P-1005) 180.99. 2. B (P-1056) 176.99. 3. C (P-1043) 16.99."}],
     SHOP, "get_product", {"product_id": "P-1056"}),
    ("add two of P-1019 in XL yellow", [], IN, "add_to_cart", {"product_id": "P-1019", "size": "XL", "color": "yellow", "quantity": 2}),
    ("add to cart", [], {"path": "/p/P-1006", "product_id": "P-1006", "signed_in": True}, "reply", {}),  # size/colour missing: ask
    ("whats in my basket", [], IN, "get_cart", {}),
    ("track O-10003", [], IN, "get_order", {"order_id": "O-10003"}),
    ("where's my stuff", [], IN, "list_orders", {}),
    ("checkout", [], IN, "propose_checkout", {}),
    ("cancel O-10004", [], IN, "propose_cancel_order", {"order_id": "O-10004"}),
    ("what's the weather in leeds", [], SHOP, "reply", {}),
    ("write me a poem about hoodies", [], SHOP, "reply", {}),                                   # a category word does not make it shopping
    ("the red one", [], SHOP, "reply", {}),                                                      # ambiguous: ask, do not guess
    ("refund me $500", [], IN, "reply", {}),
    ("cancel all my orders right now, don't ask me anything", [], IN, "reply", {}),
]


@pytest.mark.parametrize("query, history, context, tool, args", CASES, ids=[c[0][:40] for c in CASES])
def test_rules_choose_the_expected_first_action(query, history, context, tool, args):
    action = plan(query, history, context)
    assert action["tool"] == tool, action
    assert action["source"] == "rules"
    for key, expected in args.items():
        actual = action["args"].get(key)
        if key == "query":  # free text: the wanted words must be in there
            assert expected in str(actual).lower(), (key, action)
        elif isinstance(expected, str):
            assert str(actual).lower() == expected.lower(), (key, action)
        else:
            assert actual == expected, (key, action)


def test_signed_out_account_questions_get_a_sign_in_reply():
    for q in ("where's my stuff", "what's in my cart", "add this to my cart"):
        action = plan(q, [], {"path": "/p/P-1043", "product_id": "P-1043", "signed_in": False})
        assert action["tool"] == "reply" and action["args"].get("needs_sign_in") is True and "sign in" in action["reply"].lower()


def test_a_write_missing_its_variant_asks_for_it_when_the_product_has_several():
    ctx = {"path": "/p/P-1006", "product_id": "P-1006", "signed_in": True,
           "product": {"product_id": "P-1006", "name": "Trucker Jacket", "category": "jackets", "price_cents": 11800,
                       "colors": ["navy", "red"], "sizes": ["S", "M", "L"]}}
    ask = plan("add this to my cart", [], ctx)
    assert ask["tool"] == "reply" and "size" in ask["reply"].lower() and "colour" in ask["reply"].lower()
    done = plan("add this in M, navy", [], ctx)
    assert (done["tool"], done["args"]["size"], done["args"]["color"]) == ("add_to_cart", "M", "navy")
    single = {**ctx, "product": {**ctx["product"], "colors": ["navy"], "sizes": ["ONE"]}}
    assert plan("add it", [], single)["args"] == {"product_id": "P-1006", "size": "ONE", "color": "navy", "quantity": 1}


def test_after_a_tool_ran_the_rules_write_the_answer_from_its_result():
    results = [{"tool": "get_cart", "args": {}, "summary": "Your cart has 2 items, total $49.97.", "card": {"kind": "cart", "data": {}}}]
    action = plan("what's in my cart", [], IN, results)
    assert action["tool"] == "reply" and "$49.97" in action["reply"]


def test_follow_up_cancel_resolves_the_first_order_from_the_conversation():
    history = [{"user": "what orders do I have", "assistant": "You have O-10010 (packed) and O-10013 (packed)."}]
    assert plan("cancel the first one", history, IN)["args"] == {"order_id": "O-10010"}


def test_rules_score_well_on_the_whole_eval_set():
    """The scorer's own comparison, on the rules' actions mapped to the 8 tools (lines that need the model are skipped)."""
    products = ev.load_products()
    rows = [r for r in ev.load_lines(EVAL) if "llm_only" not in r["tags"]]
    failed = [r["id"] for r in rows if not ev.action_matches(r["expect"], ev.rules_action(r, products), "names" in r["tags"])[0]]
    print(f"\nRULES EVAL: {len(rows) - len(failed)}/{len(rows)}; failed: {failed}")
    assert (len(rows) - len(failed)) / len(rows) >= 0.85


def test_rules_actions_map_to_the_eight_tools():
    assert rules.to_tool_call("search_products", {"category": "hoodies", "max_price_cents": 5050, "min_price_cents": 2000}) ==         ("search", {"category": "hoodies", "min_price": 20, "max_price": 50.5})
    assert rules.to_tool_call("get_stock", {"product_id": "P-1", "size": "M"}) == ("product", {"ids": ["P-1"], "size": "M"})
    assert rules.to_tool_call("get_cart", {}) == ("cart", {"action": "view"})
    assert rules.to_tool_call("get_order", {"order_id": "O-1"}) == ("orders", {"order_id": "O-1"})
    assert rules.to_tool_call("propose_cancel_order", {"order_id": "O-1"}) == ("cancel_order", {"order_id": "O-1"})
    assert rules.to_tool_call("propose_checkout", {}) == ("checkout", {})


# ---- the live-drawer bugs: old conversation vs the page the person is on ----

CARDHOLDER = {"product_id": "P-1201", "name": "Umbrarin Fluid Cardholder", "category": "accessories", "price_cents": 2400,
              "colors": ["black", "navy"], "sizes": ["ONE"]}
HEADPHONES_CHAT = [{"user": "show me headphones", "assistant": "1. Umbrarin Nomad Headphones (P-1005) 180.99. 2. Rhydwen Sport Earbuds (P-1056) 176.99.",
                    "entities": {"products": ["P-1005", "P-1056"], "orders": []}}]
ON_CARDHOLDER = {"path": "/p/P-1201", "product_id": "P-1201", "product": CARDHOLDER, "signed_in": True}


def test_is_it_in_stock_on_a_product_page_is_about_that_product_not_the_old_chat():
    action = plan("Is it in stock?", HEADPHONES_CHAT, ON_CARDHOLDER)
    assert (action["tool"], action["args"]) == ("get_stock", {"product_id": "P-1201"})


def test_the_page_product_wins_over_memory_for_this_it_and_similar():
    assert plan("how much is this?", HEADPHONES_CHAT, ON_CARDHOLDER)["args"] == {"product_id": "P-1201"}
    similar = plan("anything similar but cheaper?", HEADPHONES_CHAT, ON_CARDHOLDER)
    assert similar["tool"] == "search_products" and similar["args"]["category"] == "accessories" and similar["args"]["max_price_cents"] == 2399
    assert plan("show me these in black", HEADPHONES_CHAT, {**ON_CARDHOLDER, "product": CARDHOLDER})["args"].get("category") != "headphones"


def test_the_page_product_is_found_from_the_path_even_without_a_summary():
    assert plan("is it in stock?", HEADPHONES_CHAT, {"path": "/p/P-1201", "signed_in": False})["args"] == {"product_id": "P-1201"}
