import json
from pathlib import Path

from orchestrator.agent.compact import cart_line, cart_summary, order_line, product_line
from orchestrator.agent.tags import tags

CATALOG = Path(__file__).resolve().parents[1] / "data/seed/v2/catalog.json"
PACK = {"product_id": "P-1001", "name": "Trail Pack", "price_cents": 7900, "colors": ["black", "olive"],
        "sizes": ["S", "M", "L", "XL"], "in_stock": True,
        "description": "Made from TPU-coated fabric, this roll-top backpack features hip-belt straps. "
                       "It offers reliable performance for cycling commutes."}


def test_product_line_exact():
    assert product_line(1, PACK) == ("1. Trail Pack [P-1001] $79 · black, olive · S-XL · in stock · "
                                     "tpu-coated, roll-top, hip-belt, cycling")


def test_product_line_variants():
    p = {**PACK, "price_cents": 7950, "sizes": ["28", "30", "32"], "in_stock": False}
    line = product_line(2, p)
    assert "$79.50 · black, olive · 28, 30, 32 · out of stock" in line
    assert "· one size ·" in product_line(1, {**PACK, "sizes": ["ONE"]}) or "one size" in product_line(1, {**PACK, "sizes": ["ONE"]})
    assert "S, L" in product_line(1, {**PACK, "sizes": ["S", "L"]})


def test_cart_and_order_lines():
    line = {"sku_id": "SKU-9", "product_id": "P-1004", "name": "Cozy Hoodie", "size": "M", "color": "navy",
            "quantity": 1, "line_total_cents": 4900}
    assert cart_line(line) == "[SKU-9] Cozy Hoodie [P-1004] M navy ×1 $49"
    assert cart_summary({"lines": [line], "total_cents": 4900}).splitlines() == [cart_line(line), "Total $49"]
    assert cart_summary({"lines": [], "total_cents": 0}) == "Cart is empty."
    order = {"order_id": "O-5001", "status": "SHIPPED", "total_cents": 9800, "created_at": "2026-11-20T10:00:00Z",
             "lines": [{"quantity": 1}, {"quantity": 1}]}
    assert order_line(order) == "O-5001 shipped $98 · 2 items · 2026-11-20"


def test_tags_from_seed_descriptions():
    products = json.loads(CATALOG.read_text())["products"]
    for p in products:
        t = tags(p["description"])
        assert 3 <= len(t) <= 5, p["description"]
        assert all(x == x.lower() for x in t)
    assert {"tpu-coated", "roll-top", "cycling"} <= set(tags(products[0]["description"]))


def test_tags_unknown_pattern():
    assert len(tags("Something entirely different about marvellous gadgets")) <= 4


def test_six_product_lines_under_600_tokens():
    products = json.loads(CATALOG.read_text())["products"][:6]
    text = "\n".join(product_line(i, {**p, "in_stock": True}) for i, p in enumerate(products, 1))
    assert len(text) // 4 < 600
