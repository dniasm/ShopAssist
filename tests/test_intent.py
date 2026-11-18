"""Rule parser: every intent in the PLAN table, plus entity extraction and edge cases."""
import pytest

from orchestrator.intent import parse_rules


@pytest.mark.parametrize("text, intent, entities", [
    ("Where is my order 1042?", "order_status", {"order_id": "1042"}),
    ("what's the status of order #1044", "order_status", {"order_id": "1044"}),
    ("Track order 1043 live", "track_order", {"order_id": "1043"}),
    ("track 1043", "track_order", {"order_id": "1043"}),
    ("Show me headphones under $100", "product_search", {"category": "headphones", "max_price": 100.0}),
    ("find red sneakers below 80 dollars", "product_search", {"category": "shoes", "color": "red", "max_price": 80.0}),
    ("Do you have the blue hoodie in size M?", "stock_check", {"category": "hoodies", "color": "blue", "size": "M"}),
    ("Is the blue hoodie in stock?", "stock_check", {"category": "hoodies", "color": "blue"}),
    ("Is P-1005 in stock in size L?", "stock_check", {"product_id": "P-1005", "size": "L"}),
    ("Tell me about P-1003", "product_info", {"product_id": "P-1003"}),
    ("details for p1003 please", "product_info", {"product_id": "P-1003"}),
    ("What's the weather?", "unknown", {}),
    ("hello", "unknown", {}),
    ("", "unknown", {}),
])
def test_rules(text, intent, entities):
    got_intent, got_entities = parse_rules(text)
    assert got_intent == intent
    assert {k: v for k, v in got_entities.items() if k != "keywords"} == entities


def test_price_is_not_mistaken_for_order_id():
    intent, ent = parse_rules("headphones under $100")
    assert intent == "product_search" and "order_id" not in ent


def test_do_you_have_without_size_or_stock_word_is_a_search():
    assert parse_rules("Do you have headphones under $100?")[0] == "product_search"


def test_apostrophe_s_is_not_a_size():
    assert "size" not in parse_rules("what's the weather")[1]


def test_keywords_extracted():
    intent, ent = parse_rules("show me wireless headphones")
    assert intent == "product_search" and ent["keywords"] == ["wireless"]


def test_order_intent_without_number():
    assert parse_rules("where is my order?") == ("order_status", {})
    assert parse_rules("track my package")[0] == "track_order"


def test_filler_words_do_not_become_keywords():
    intent, ent = parse_rules("show me something cheap")
    assert intent == "product_search" and "keywords" not in ent
    intent, ent = parse_rules("hoodies between 40 and 50")
    assert intent == "product_search" and ent == {"category": "hoodies"}  # range prices are out of scope
    for text in ("show me anything", "find items", "list products", "any stuff"):
        assert "keywords" not in parse_rules(text)[1], text


def test_need_or_want_alone_is_not_a_product_search():
    for text in ("I need help", "I want a refund", "I want to talk to a human"):
        assert parse_rules(text) == ("unknown", {}), text
    assert parse_rules("I want a black backpack")[0] == "product_search"  # a product word still wins
