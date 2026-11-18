"""Check the ShopAssist v2 seed data. Exits 1 and lists every violation if any check fails.

Usage (from the repo root):
    python scripts/validate_data.py [--dir data/seed/v2]

On success prints a short summary: products per category, price ranges, stock buckets.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = REPO_ROOT / "data" / "seed" / "v2"

# The product types (materials, uses, waterproof flag) live with the generator.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_data import TYPES  # noqa: E402

# A description sentence must contain one of these verbs (a guard against template regressions).
VERBS = {"is", "are", "has", "have", "features", "feature", "keeps", "keep", "suits", "suit",
         "works", "work", "offers", "offer", "comes", "come", "stays", "stay", "handles",
         "handle", "pairs", "pair"}
ADJECTIVE_MAX_SHARE = 0.06

CATEGORY_SIZE_KIND = {
    "headphones": "one", "hoodies": "top", "t-shirts": "top", "jackets": "top",
    "jeans": "jeans", "shoes": "shoes", "backpacks": "one", "watches": "one",
    "sunglasses": "one", "accessories": "one",
}
SIZE_SETS = {
    "top": {"XS", "S", "M", "L", "XL", "XXL"},
    "jeans": {"28", "30", "32", "34", "36"},
    "shoes": {"6", "7", "8", "9", "10", "11", "12"},
    "one": {"ONE"},
}
PALETTE = {"black", "white", "grey", "navy", "blue", "green", "olive", "red",
           "burgundy", "beige", "brown", "pink", "yellow", "orange"}
STATUS_PATHS = {
    "CONFIRMED": ["CONFIRMED"],
    "PACKED": ["CONFIRMED", "PACKED"],
    "SHIPPED": ["CONFIRMED", "PACKED", "SHIPPED"],
    "DELIVERED": ["CONFIRMED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"],
    "CANCELLED": ["CONFIRMED", "CANCELLED"],
}
TODAY = datetime(2026, 11, 18, tzinfo=timezone.utc)
HISTORY_START = datetime(2025, 5, 18, tzinfo=timezone.utc)

# Required counts ("about 400" products etc.).
PRODUCTS_EXPECTED = 400
USERS_EXPECTED = 6
BRANDS_RANGE = (20, 30)
HEADPHONES_MIN = 25
HEADPHONES_UNDER_10000_MIN = 10
BLUE_NAVY_HOODIES_MIN = 5
WATERPROOF_DESCRIPTIONS_MIN = 5
ORDERS_PER_USER = (2, 6)
ZERO_STOCK_SHARE = (0.03, 0.07)   # about 5%
LOW_STOCK_SHARE = (0.07, 0.13)    # about 10%

errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def load(directory: Path, name: str):
    path = directory / name
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        sys.exit(f"validate: missing {path} (run scripts/gen_data.py first)")
    except json.JSONDecodeError as exc:
        sys.exit(f"validate: {path} is not valid JSON: {exc}")


def parse_ts(value: str, where: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        fail(f"{where}: timestamp {value!r} is not ISO-8601 UTC (YYYY-MM-DDTHH:MM:SSZ)")
        return None


def split_sentences(desc: str) -> list[str]:
    return [s for s in re.split(r"(?<=\.)\s+", desc.strip()) if s]


def product_parts(p: dict) -> tuple[str, str] | None:
    """(adjective, noun) from the name. The name is brand + adjective + noun, optionally ' N' suffix."""
    brand, name = p.get("brand", ""), p.get("name", "")
    if not name.startswith(brand + " "):
        return None
    adjective, _, noun = name[len(brand) + 1:].partition(" ")
    return adjective, re.sub(r" \d+$", "", noun)


def check_text(products: list[dict]) -> Counter:
    """Each product's material, use case and waterproof claim must come from its own type."""
    adjectives: Counter = Counter()
    unique([p["name"] for p in products], "product name")
    for p in products:
        where = f"product {p['product_id']}"
        parts = product_parts(p)
        if parts is None:
            fail(f"{where}: name {p['name']!r} does not start with brand {p['brand']!r}")
            continue
        adjective, noun = parts
        adjectives[adjective] += 1
        category = p["category"]
        if noun not in TYPES.get(category, {}):
            fail(f"{where}: name noun {noun!r} is not a {category} type")
            continue
        own = TYPES[category][noun]
        desc = p["description"]
        all_materials = {m for t in TYPES[category].values() for m in t["materials"]}
        all_uses = {u for t in TYPES[category].values() for u in t["uses"]}
        found_m = {m for m in all_materials if m in desc}
        found_u = {u for u in all_uses if u in desc}
        if not found_m & set(own["materials"]):
            fail(f"{where} ({noun}): description has none of its materials {own['materials']}")
        for stray in sorted(found_m - set(own["materials"])):
            fail(f"{where} ({noun}): material {stray!r} belongs to another type")
        if not found_u & set(own["uses"]):
            fail(f"{where} ({noun}): description has none of its use cases {own['uses']}")
        for stray in sorted(found_u - set(own["uses"])):
            fail(f"{where} ({noun}): use case {stray!r} belongs to another type")
        if "waterproof" in desc.lower() and not own["waterproof"]:
            fail(f"{where} ({noun}): claims waterproof but this type is not waterproof")
    limit = ADJECTIVE_MAX_SHARE * len(products)
    for adjective, n in adjectives.items():
        if n > limit:
            fail(f"names: adjective {adjective!r} on {n} products ({n / len(products):.1%}); max {ADJECTIVE_MAX_SHARE:.0%}")
    return adjectives


def unique(values, label: str) -> None:
    seen: set = set()
    for v in values:
        if v in seen:
            fail(f"duplicate {label}: {v}")
        seen.add(v)


def pct(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def check_catalog(catalog: dict) -> tuple[dict, dict]:
    products = catalog.get("products", [])
    skus = catalog.get("skus", [])
    if len(products) != PRODUCTS_EXPECTED:
        fail(f"catalog: expected {PRODUCTS_EXPECTED} products, found {len(products)}")
    unique([p["product_id"] for p in products], "product_id")
    unique([s["sku_id"] for s in skus], "sku_id")

    by_id = {p["product_id"]: p for p in products}
    cats = Counter(p["category"] for p in products)
    if set(cats) != set(CATEGORY_SIZE_KIND):
        fail(f"catalog: categories must be exactly {sorted(CATEGORY_SIZE_KIND)}, found {sorted(cats)}")

    for p in products:
        pid = p["product_id"]
        where = f"product {pid}"
        if not p.get("name"):
            fail(f"{where}: empty name")
        if p.get("category") not in CATEGORY_SIZE_KIND:
            fail(f"{where}: unknown category {p.get('category')!r}")
        if not p.get("brand"):
            fail(f"{where}: empty brand")
        desc = p.get("description", "")
        sentences = split_sentences(desc)
        if not 1 <= len(sentences) <= 3:
            fail(f"{where}: description must be 1-3 sentences, found {len(sentences)}")
        for s in sentences:
            if not s.endswith("."):
                fail(f"{where}: sentence does not end with a full stop: {s!r}")
            if not set(re.findall(r"[a-z]+", s.lower())) & VERBS:
                fail(f"{where}: sentence has no verb from the allowed list: {s!r}")
        price = p.get("price_cents")
        if not isinstance(price, int) or price <= 0 or price % 100 not in (0, 99):
            fail(f"{where}: price_cents {price!r} must be a positive integer ending in 99 or 00")
        rating = p.get("rating")
        if not isinstance(rating, (int, float)) or not 2.8 <= rating <= 4.9 or round(rating, 1) != rating:
            fail(f"{where}: rating {rating!r} must be 2.8-4.9 with one decimal")
        rc = p.get("rating_count")
        if not isinstance(rc, int) or not 0 <= rc <= 2500:
            fail(f"{where}: rating_count {rc!r} must be an integer 0-2500")
        colors = p.get("colors", [])
        if not 1 <= len(colors) <= 4 or len(set(colors)) != len(colors):
            fail(f"{where}: colors must be 1-4 distinct values, found {colors}")
        for c in colors:
            if c not in PALETTE:
                fail(f"{where}: colour {c!r} is not in the palette")
        kind = CATEGORY_SIZE_KIND.get(p.get("category"))
        if kind and set(p.get("sizes", [])) - SIZE_SETS[kind]:
            fail(f"{where}: sizes {p.get('sizes')} not allowed for {p['category']}")
        if not p.get("sizes"):
            fail(f"{where}: no sizes")
        created = parse_ts(p.get("created_at"), where)
        if created and not (HISTORY_START <= created <= TODAY):
            fail(f"{where}: created_at {p['created_at']} outside the last 18 months")

    sku_ids_by_product: Counter = Counter()
    seen_combo: set = set()
    for s in skus:
        where = f"sku {s['sku_id']}"
        p = by_id.get(s.get("product_id"))
        if p is None:
            fail(f"{where}: product {s.get('product_id')} does not exist")
            continue
        if s.get("size") not in p["sizes"]:
            fail(f"{where}: size {s.get('size')!r} not in product sizes {p['sizes']}")
        if s.get("color") not in p["colors"]:
            fail(f"{where}: colour {s.get('color')!r} not in product colours {p['colors']}")
        combo = (s.get("product_id"), s.get("size"), s.get("color"))
        if combo in seen_combo:
            fail(f"{where}: duplicate size x colour for {s.get('product_id')}")
        seen_combo.add(combo)
        sku_ids_by_product[s["product_id"]] += 1

    for p in products:
        expected = len(p["sizes"]) * len(p["colors"])
        got = sku_ids_by_product.get(p["product_id"], 0)
        if got == 0:
            fail(f"product {p['product_id']}: has no SKUs")
        elif got != expected:
            fail(f"product {p['product_id']}: expected {expected} SKUs (sizes x colours), found {got}")

    # Required counts from the Phase 5 brief.
    brands = {p["brand"] for p in products}
    if not BRANDS_RANGE[0] <= len(brands) <= BRANDS_RANGE[1]:
        fail(f"catalog: expected about 25 brands, found {len(brands)}")
    headphones = [p for p in products if p["category"] == "headphones"]
    if len(headphones) < HEADPHONES_MIN:
        fail(f"catalog: need at least {HEADPHONES_MIN} headphones, found {len(headphones)}")
    cheap = [p for p in headphones if p["price_cents"] < 10000]
    if len(cheap) < HEADPHONES_UNDER_10000_MIN:
        fail(f"catalog: need at least {HEADPHONES_UNDER_10000_MIN} headphones under 10000 cents, found {len(cheap)}")
    blue_hoodies = [p for p in products if p["category"] == "hoodies"
                    and {"navy", "blue"} & set(p["colors"])]
    if len(blue_hoodies) < BLUE_NAVY_HOODIES_MIN:
        fail(f"catalog: need at least {BLUE_NAVY_HOODIES_MIN} blue/navy hoodies, found {len(blue_hoodies)}")
    waterproof = [p for p in products if "waterproof" in p.get("description", "").lower()]
    if len(waterproof) < WATERPROOF_DESCRIPTIONS_MIN:
        fail(f"catalog: need at least {WATERPROOF_DESCRIPTIONS_MIN} descriptions mentioning waterproof, found {len(waterproof)}")
    adjectives = check_text(products)
    return by_id, {"cats": cats, "brands": brands, "waterproof": len(waterproof), "adjectives": adjectives}


def check_inventory(inventory: list, skus: list, by_id: dict) -> Counter:
    sku_map = {s["sku_id"]: s for s in skus}
    seen = Counter(row["sku_id"] for row in inventory)
    for sku_id in sku_map:
        if seen.get(sku_id, 0) != 1:
            fail(f"inventory: sku {sku_id} appears {seen.get(sku_id, 0)} times (need exactly 1)")
    for sku_id in seen:
        if sku_id not in sku_map:
            fail(f"inventory: sku {sku_id} is not in the catalog")
    buckets: Counter = Counter()
    for row in inventory:
        where = f"inventory {row.get('sku_id')}"
        on_hand = row.get("on_hand")
        if not isinstance(on_hand, int) or on_hand < 0:
            fail(f"{where}: on_hand {on_hand!r} must be an integer >= 0")
            continue
        sku = sku_map.get(row.get("sku_id"))
        if sku and row.get("product_id") != sku["product_id"]:
            fail(f"{where}: product_id does not match the SKU's product")
        if on_hand == 0:
            buckets["zero"] += 1
        elif on_hand <= 4:
            buckets["1-4"] += 1
        else:
            buckets["5-60"] += 1
    total = len(inventory)
    if not ZERO_STOCK_SHARE[0] <= pct(buckets["zero"], total) <= ZERO_STOCK_SHARE[1]:
        fail(f"inventory: zero-stock share {pct(buckets['zero'], total):.1%} should be about 5%")
    if not LOW_STOCK_SHARE[0] <= pct(buckets["1-4"], total) <= LOW_STOCK_SHARE[1]:
        fail(f"inventory: 1-4 stock share {pct(buckets['1-4'], total):.1%} should be about 10%")
    return buckets


def check_customers(customers: dict) -> dict:
    users = customers.get("users", [])
    if len(users) != USERS_EXPECTED:
        fail(f"customers: expected {USERS_EXPECTED} users, found {len(users)}")
    unique([u["user_id"] for u in users], "user_id")
    unique([u["email"] for u in users], "user email")
    for u in users:
        if not u.get("email", "").endswith("@example.com"):
            fail(f"user {u.get('user_id')}: email must be on example.com")
    return {u["user_id"]: u for u in users}


def check_orders(orders: list, payments: list, by_id: dict, skus: list, users: dict) -> Counter:
    sku_map = {s["sku_id"]: s for s in skus}
    unique([o["order_id"] for o in orders], "order_id")
    unique([p["payment_id"] for p in payments], "payment_id")
    per_user: Counter = Counter()
    status_counts: Counter = Counter()
    for o in orders:
        oid = o["order_id"]
        where = f"order {oid}"
        if o.get("user_id") not in users:
            fail(f"{where}: unknown user {o.get('user_id')}")
        per_user[o.get("user_id")] += 1
        status = o.get("status")
        status_counts[status] += 1
        if status not in STATUS_PATHS:
            fail(f"{where}: status {status!r} is not one of {sorted(STATUS_PATHS)}")
            continue

        lines = o.get("lines", [])
        if not lines:
            fail(f"{where}: no lines")
        seen_skus = set()
        for line in lines:
            sku = sku_map.get(line.get("sku_id"))
            if sku is None:
                fail(f"{where}: line references unknown sku {line.get('sku_id')}")
                continue
            if sku["sku_id"] in seen_skus:
                fail(f"{where}: sku {sku['sku_id']} appears twice")
            seen_skus.add(sku["sku_id"])
            product = by_id[sku["product_id"]]
            if line.get("product_id") != product["product_id"]:
                fail(f"{where}: line product_id does not match its sku")
            if line.get("name") != product["name"]:
                fail(f"{where}: line name {line.get('name')!r} != product name {product['name']!r}")
            if line.get("unit_price_cents") != product["price_cents"]:
                fail(f"{where}: unit_price_cents {line.get('unit_price_cents')} != product price {product['price_cents']}")
            qty = line.get("quantity")
            if not isinstance(qty, int) or qty < 1:
                fail(f"{where}: quantity {qty!r} must be >= 1")
                continue
            if line.get("line_total_cents") != line.get("unit_price_cents", 0) * qty:
                fail(f"{where}: line_total_cents does not equal unit price x quantity")

        subtotal = sum(line.get("line_total_cents", 0) for line in lines)
        if o.get("subtotal_cents") != subtotal:
            fail(f"{where}: subtotal_cents {o.get('subtotal_cents')} != sum of lines {subtotal}")
        expected_ship = 0 if subtotal >= 5000 else 499
        if o.get("shipping_cents") != expected_ship:
            fail(f"{where}: shipping_cents {o.get('shipping_cents')} should be {expected_ship}")
        if o.get("total_cents") != subtotal + expected_ship:
            fail(f"{where}: total_cents {o.get('total_cents')} != subtotal + shipping")

        events = o.get("events", [])
        walked = [e.get("status") for e in events]
        if walked != STATUS_PATHS.get(status):
            fail(f"{where}: event statuses {walked} do not walk the path for {status}")
        stamps = [parse_ts(e.get("timestamp"), f"{where} event") for e in events]
        if any(s is None for s in stamps):
            continue
        if any(b <= a for a, b in zip(stamps, stamps[1:])):
            fail(f"{where}: event timestamps are not strictly increasing")
        created = parse_ts(o.get("created_at"), where)
        if created and stamps and created != stamps[0]:
            fail(f"{where}: created_at must equal the first event timestamp")
        updated = parse_ts(o.get("updated_at"), where)
        if updated and stamps and updated != stamps[-1]:
            fail(f"{where}: updated_at must equal the last event timestamp")
        if stamps and stamps[-1] > TODAY:
            fail(f"{where}: events run past today")

    for uid in users:
        n = per_user.get(uid, 0)
        if not ORDERS_PER_USER[0] <= n <= ORDERS_PER_USER[1]:
            fail(f"orders: user {uid} has {n} orders, need {ORDERS_PER_USER[0]}-{ORDERS_PER_USER[1]}")
    for s in STATUS_PATHS:
        if status_counts.get(s, 0) == 0:
            fail(f"orders: no order with status {s}")

    order_map = {o["order_id"]: o for o in orders}
    paid_orders: Counter = Counter()
    for p in payments:
        where = f"payment {p['payment_id']}"
        order = order_map.get(p.get("order_id"))
        if order is None:
            fail(f"{where}: references unknown order {p.get('order_id')}")
            continue
        paid_orders[p["order_id"]] += 1
        if p.get("amount_cents") != order["total_cents"]:
            fail(f"{where}: amount_cents {p.get('amount_cents')} != order total {order['total_cents']}")
        want = "PAYMENT_REFUNDED" if order["status"] == "CANCELLED" else "PAYMENT_SUCCEEDED"
        if p.get("status") != want:
            fail(f"{where}: status {p.get('status')} should be {want} for a {order['status']} order")
    for oid in order_map:
        if paid_orders.get(oid, 0) != 1:
            fail(f"payments: order {oid} has {paid_orders.get(oid, 0)} payments (need exactly 1)")
    return status_counts


def summary(by_id: dict, cat_info: dict, buckets: Counter, inventory: list, skus: list,
            users: dict, orders: list, status_counts: Counter) -> None:
    print("OK: seed data in data/seed/v2 passes every check")
    print(f"products: {len(by_id)}   brands: {len(cat_info['brands'])}   "
          f"skus: {len(skus)}   waterproof descriptions: {cat_info['waterproof']}")
    print("category        products  price range (USD)")
    per_cat: dict[str, list[int]] = defaultdict(list)
    for p in by_id.values():
        per_cat[p["category"]].append(p["price_cents"])
    for cat in CATEGORY_SIZE_KIND:
        prices = per_cat.get(cat, [])
        if prices:
            print(f"{cat:<15} {len(prices):>8}  {min(prices) / 100:.2f} - {max(prices) / 100:.2f}")
    total = len(inventory)
    print(f"stock buckets over {total} SKUs: "
          f"zero {buckets['zero']} ({pct(buckets['zero'], total):.1%}), "
          f"1-4 {buckets['1-4']} ({pct(buckets['1-4'], total):.1%}), "
          f"5-60 {buckets['5-60']} ({pct(buckets['5-60'], total):.1%})")
    top = cat_info["adjectives"].most_common(10)
    print("top 10 name adjectives (count, share of products): " +
          ", ".join(f"{a} {n} ({n / len(by_id):.1%})" for a, n in top))
    print(f"customers: {len(users)}   orders: {len(orders)}   payments: {len(orders)}")
    print("orders by status: " + ", ".join(f"{s} {status_counts[s]}" for s in STATUS_PATHS))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the ShopAssist v2 seed data.")
    parser.add_argument("--dir", default=str(DEFAULT_DIR), help="seed directory (default data/seed/v2)")
    args = parser.parse_args()
    directory = Path(args.dir)

    catalog = load(directory, "catalog.json")
    inventory = load(directory, "inventory.json")
    customers = load(directory, "customers.json")
    orders = load(directory, "orders.json")
    payments = load(directory, "payments.json")

    by_id, cat_info = check_catalog(catalog)
    skus = catalog.get("skus", [])
    buckets = check_inventory(inventory, skus, by_id)
    users = check_customers(customers)
    status_counts = check_orders(orders, payments, by_id, skus, users)

    if errors:
        print(f"FAILED: {len(errors)} violation(s)")
        for msg in errors:
            print(f"  - {msg}")
        return 1
    summary(by_id, cat_info, buckets, inventory, skus, users, orders, status_counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
