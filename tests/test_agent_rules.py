"""The rule-based planner (no LLM, no network): what it decides for representative shop questions, and how well it does
on the whole labelled eval set (tests/eval/queries.jsonl)."""
import asyncio
import json
from pathlib import Path

import pytest

from orchestrator.agent.planner import plan_next_action

EVAL = Path(__file__).parent / "eval" / "queries.jsonl"
SHOP = {"path": "/shop", "signed_in": False}
IN = {"path": "/shop", "signed_in": True, "user_id": "U-01"}


def plan(query, history=(), context=None, tool_results=()):
    return asyncio.run(plan_next_action(query, list(history), context or SHOP, llm=None, tool_results=list(tool_results)))


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
    """Same comparison the scorer uses: tool exact, args as a subset (strings case-insensitive, query by token containment)."""
    def ok(expected, actual):
        for key, want in expected.items():
            got = actual.get(key)
            if key == "query":
                strip = lambda t: {w.rstrip("s") for w in str(t).lower().replace("-", " ").split()}  # noqa: E731
                if not strip(want) <= strip(got or ""):
                    return False
            elif isinstance(want, str):
                if str(got).lower() != want.lower():
                    return False
            elif got != want:
                return False
        return True

    rows = [json.loads(line) for line in EVAL.read_text(encoding="utf-8").splitlines() if line.strip()]
    passed, failed = 0, []
    for q in rows:
        action = plan(q["query"], q["history"], q["context"])
        if action["tool"] == q["expect"]["tool"] and ok(q["expect"]["args"], action["args"]):
            passed += 1
        else:
            failed.append(q["id"])
    print(f"\nRULES EVAL: {passed}/{len(rows)} = {passed / len(rows):.0%}; failed: {failed}")
    assert passed / len(rows) >= 0.85
