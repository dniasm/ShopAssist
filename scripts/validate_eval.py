"""Check tests/eval/queries.jsonl against the seed data and the agent tool table. Exits 1 on any problem.

Usage (from the repo root):
    python scripts/validate_eval.py [--file tests/eval/queries.jsonl]

Checks, per line: shape of the JSON object; tool name and allowed/required args from the tool table;
enum values (sort, category, colour, size, brand); money as integers; every id exists in data/seed/v2;
sizes and colours are ones the product has; searches match at least one product; signed-in lines name
an existing customer and every order they mention belongs to that customer; signed-out lines never use
cart or order tools; a cancel with an explicit order id expects propose_cancel_order; a query word in
expect.args appears in the shopper's text (or is a plural or near-typo of a word there); a follow-up
search keeps the category the history named; tags from the allowed list with exactly one category tag;
write and proposal tools carry their tag; follow-ups have history; context products exist and match the
path; reason is one sentence.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "data" / "seed" / "v2"
DEFAULT_FILE = REPO / "tests" / "eval" / "queries.jsonl"

CATEGORIES = {"headphones", "hoodies", "t-shirts", "jackets", "jeans", "shoes", "backpacks",
              "watches", "sunglasses", "accessories"}
# How a shopper names a category in text (singular stems); used to check that follow-ups keep it.
CATEGORY_WORDS = {"headphones": "headphone", "hoodies": "hoodie", "t-shirts": "t-shirt", "jackets": "jacket",
                  "jeans": "jean", "shoes": "shoe", "backpacks": "backpack", "watches": "watch",
                  "sunglasses": "sunglass", "accessories": "accessor"}
PALETTE = {"black", "white", "grey", "navy", "blue", "green", "olive", "red", "burgundy", "beige",
           "brown", "pink", "yellow", "orange"}
SIZES = {"XS", "S", "M", "L", "XL", "XXL", "28", "30", "32", "34", "36", "6", "7", "8", "9", "10", "11", "12", "ONE"}
SORTS = {"relevance", "price_asc", "price_desc", "rating", "newest"}
ORDER_ID = re.compile(r"O-\d{5}")

TOOLS = {
    "search_products": {"required": set(), "allowed": {"query", "category", "color", "size", "brand",
                        "min_price_cents", "max_price_cents", "sort"}, "kind": "read"},
    "get_product": {"required": {"product_id"}, "allowed": {"product_id"}, "kind": "read"},
    "get_stock": {"required": {"product_id"}, "allowed": {"product_id", "size", "color"}, "kind": "read"},
    "get_cart": {"required": set(), "allowed": set(), "kind": "read"},
    "add_to_cart": {"required": {"product_id", "size", "color"}, "allowed": {"product_id", "size", "color", "quantity"},
                    "kind": "write"},
    "remove_from_cart": {"required": {"product_id"}, "allowed": {"product_id", "size", "color"}, "kind": "write"},
    "list_orders": {"required": set(), "allowed": set(), "kind": "read"},
    "get_order": {"required": {"order_id"}, "allowed": {"order_id"}, "kind": "read"},
    "propose_checkout": {"required": set(), "allowed": {"payment_method"}, "kind": "proposal"},
    "propose_cancel_order": {"required": {"order_id"}, "allowed": {"order_id"}, "kind": "proposal"},
    "reply": {"required": set(), "allowed": set(), "kind": "reply"},
}
# Tools that act for a particular shopper; a signed-out line must expect reply instead.
SIGNED_IN_TOOLS = {"get_cart", "add_to_cart", "remove_from_cart", "list_orders", "get_order",
                   "propose_checkout", "propose_cancel_order"}
CATEGORY_TAGS = {"basic", "slang", "typo", "context", "follow_up", "multi_constraint", "out_of_scope",
                 "ambiguous", "order", "cart", "proposal", "safety"}
ALLOWED_TAGS = CATEGORY_TAGS | {"write"}
LINE_KEYS = {"id", "query", "history", "context", "expect", "tags", "reason"}


def load_seed():
    catalog = json.loads((SEED / "catalog.json").read_text(encoding="utf-8"))
    orders = json.loads((SEED / "orders.json").read_text(encoding="utf-8"))
    customers = json.loads((SEED / "customers.json").read_text(encoding="utf-8"))["users"]
    products = {p["product_id"]: p for p in catalog["products"]}
    brands = {p["brand"].lower() for p in catalog["products"]}
    order_map = {o["order_id"]: o for o in orders}
    user_ids = {u["user_id"] for u in customers}
    return list(products.values()), products, brands, order_map, user_ids


def text(p) -> str:
    return f"{p['name']} {p['description']} {p['brand']} {p['category']}".lower()


def check_search(where, args, all_products, brands, errors):
    pool = all_products
    if "category" in args:
        if args["category"] not in CATEGORIES:
            errors.append(f"{where}: category {args['category']!r} is not one of the 10 categories")
        pool = [p for p in pool if p["category"] == args["category"]]
    if "color" in args:
        if args["color"] not in PALETTE:
            errors.append(f"{where}: colour {args['color']!r} is not in the palette")
        pool = [p for p in pool if args["color"] in p["colors"]]
    if "size" in args:
        if args["size"] not in SIZES:
            errors.append(f"{where}: size {args['size']!r} is not a known size")
        pool = [p for p in pool if args["size"] in p["sizes"]]
    if "brand" in args:
        if args["brand"].lower() not in brands:
            errors.append(f"{where}: brand {args['brand']!r} does not exist")
        pool = [p for p in pool if p["brand"].lower() == args["brand"].lower()]
    lo, hi = args.get("min_price_cents"), args.get("max_price_cents")
    for key in ("min_price_cents", "max_price_cents"):
        if key in args and (not isinstance(args[key], int) or args[key] < 0):
            errors.append(f"{where}: {key} must be a non-negative integer of cents")
    if lo is not None and hi is not None and lo > hi:
        errors.append(f"{where}: min_price_cents is above max_price_cents")
    if lo is not None:
        pool = [p for p in pool if p["price_cents"] >= lo]
    if hi is not None:
        pool = [p for p in pool if p["price_cents"] <= hi]
    if "sort" in args and args["sort"] not in SORTS:
        errors.append(f"{where}: sort {args['sort']!r} is not one of {sorted(SORTS)}")
    if not pool:
        errors.append(f"{where}: the structured filters match no product in the seed data")
    if "query" in args:
        # The feature words must exist somewhere in the catalog. The filtered set may still be empty for
        # a real shopper's constraints (e.g. no waterproof navy jacket under 150), and the search is still right.
        words = re.findall(r"[a-z0-9]+", args["query"].lower())
        if words and not any(all(w in text(p) for w in words) for p in all_products):
            errors.append(f"{where}: no product in the catalog contains every word of query {args['query']!r}")


def check_product_args(where, args, products, errors):
    product = products.get(args.get("product_id"))
    if product is None:
        errors.append(f"{where}: product_id {args.get('product_id')!r} does not exist")
        return
    if "size" in args and args["size"] not in product["sizes"]:
        errors.append(f"{where}: {product['product_id']} has sizes {product['sizes']}, not {args['size']!r}")
    if "color" in args and args["color"] not in product["colors"]:
        errors.append(f"{where}: {product['product_id']} comes in {product['colors']}, not {args['color']!r}")


def query_word_ok(word: str, line_tokens: list[str]) -> bool:
    """A word in expect.args.query must be in the shopper's text, or a plural/singular or near-typo of one."""
    for lt in line_tokens:
        if word == lt or lt.rstrip("s") == word.rstrip("s") or difflib.SequenceMatcher(None, word, lt).ratio() >= 0.8:
            return True
    return False


def check_line(lineno: int, raw: str, seed, seen_ids: set, errors: list, stats: dict) -> None:
    all_products, products, brands, order_map, user_ids = seed
    where = f"line {lineno}"
    try:
        line = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"{where}: not valid JSON ({exc})")
        return
    if not isinstance(line, dict) or set(line) != LINE_KEYS:
        errors.append(f"{where}: keys must be exactly {sorted(LINE_KEYS)}; found {sorted(line) if isinstance(line, dict) else type(line).__name__}")
        return
    lid = line["id"]
    where = f"{lid} (line {lineno})"
    if lid in seen_ids:
        errors.append(f"{where}: duplicate id")
    seen_ids.add(lid)
    query = line["query"]
    if not isinstance(query, str) or not query.strip():
        errors.append(f"{where}: query must be a non-empty string")
        query = ""

    history = line["history"]
    if not isinstance(history, list) or not all(
            isinstance(h, dict) and set(h) == {"user", "assistant"} and all(isinstance(v, str) for v in h.values())
            for h in history):
        errors.append(f"{where}: history must be a list of {{user, assistant}} string objects")
        history = []

    ctx = line["context"]
    if not isinstance(ctx, dict) or not isinstance(ctx.get("path"), str) or not isinstance(ctx.get("signed_in"), bool):
        errors.append(f"{where}: context needs a string path and a boolean signed_in")
        ctx = {}
    if "product_id" in ctx:
        if ctx["product_id"] not in products:
            errors.append(f"{where}: context product_id {ctx['product_id']!r} does not exist")
        m = re.match(r"^/p/(P-\d+)$", ctx.get("path", ""))
        if m and m.group(1) != ctx["product_id"]:
            errors.append(f"{where}: path {ctx['path']} does not match product_id {ctx['product_id']}")
    signed_in = ctx.get("signed_in") is True
    user_id = ctx.get("user_id")
    if signed_in and user_id not in user_ids:
        errors.append(f"{where}: signed-in lines need context.user_id of an existing customer; found {user_id!r}")
    if not signed_in and "user_id" in ctx:
        errors.append(f"{where}: signed-out lines must not name a user")

    expect = line["expect"]
    tool = expect.get("tool") if isinstance(expect, dict) else None
    args = expect.get("args") if isinstance(expect, dict) else None
    if tool not in TOOLS or not isinstance(args, dict) or set(expect) != {"tool", "args"}:
        errors.append(f"{where}: expect must be {{tool, args}} with a tool from the table")
        return
    spec = TOOLS[tool]
    unknown = set(args) - spec["allowed"]
    if unknown:
        errors.append(f"{where}: {tool} does not take {sorted(unknown)}")
    missing = spec["required"] - set(args)
    if missing:
        errors.append(f"{where}: {tool} needs {sorted(missing)} in expect.args")
    stats["tool"][tool] += 1

    # Shopper-bound tools need a signed-in shopper; a signed-out line expects a reply.
    if tool in SIGNED_IN_TOOLS and not signed_in:
        errors.append(f"{where}: {tool} needs a signed-in shopper; a signed-out line must expect reply")

    # Every order the line names must belong to the signed-in shopper.
    if signed_in:
        named = set(ORDER_ID.findall(query + json.dumps(history, ensure_ascii=False) + json.dumps(args)))
        for oid in sorted(named):
            order = order_map.get(oid)
            if order is None:
                errors.append(f"{where}: order {oid} does not exist")
            elif order["user_id"] != user_id:
                errors.append(f"{where}: order {oid} belongs to {order['user_id']}, not the shopper {user_id}")

    # An explicit cancel always goes through a cancel proposal for the order it names.
    if re.search(r"\bcancel", query, re.IGNORECASE):
        named_in_query = ORDER_ID.findall(query)
        if named_in_query and (tool != "propose_cancel_order" or args.get("order_id") != named_in_query[0]):
            errors.append(f"{where}: a cancel that names {named_in_query[0]} must expect propose_cancel_order with that order_id")

    if tool == "search_products":
        check_search(where, args, all_products, brands, errors)
        if "query" in args:
            line_tokens = re.findall(r"[a-z0-9]+", query.lower())
            for word in re.findall(r"[a-z0-9]+", args["query"].lower()):
                if not query_word_ok(word, line_tokens):
                    errors.append(f"{where}: query word {word!r} is not in the shopper's text (only literal words, plurals or typos are allowed)")
        if "follow_up" in line["tags"]:
            prior = " ".join(h["user"] for h in history).lower()
            for cat, stem in CATEGORY_WORDS.items():
                if stem in prior and "category" not in args:
                    errors.append(f"{where}: the history names {cat}, so a follow-up search must keep category={cat}")
                    break
    elif tool in ("get_product", "get_stock", "add_to_cart", "remove_from_cart"):
        if "product_id" in args:
            check_product_args(where, args, products, errors)
        if tool == "add_to_cart" and "quantity" in args and (not isinstance(args["quantity"], int) or args["quantity"] < 1):
            errors.append(f"{where}: quantity must be a positive integer")
    elif tool in ("get_order", "propose_cancel_order"):
        if order_map.get(args.get("order_id")) is None:
            errors.append(f"{where}: order_id {args.get('order_id')!r} does not exist")

    tags = line["tags"]
    if not isinstance(tags, list) or not all(t in ALLOWED_TAGS for t in tags):
        errors.append(f"{where}: tags must come from {sorted(ALLOWED_TAGS)}")
        tags = []
    # "proposal" is a category only when nothing else is: a follow-up whose first action is a proposal
    # carries both tags, and counts as follow_up.
    cats = [t for t in tags if t in CATEGORY_TAGS and t != "proposal"]
    if not cats and "proposal" in tags:
        cats = ["proposal"]
    if len(cats) != 1:
        errors.append(f"{where}: needs exactly one category tag; found {cats}")
    for t in cats:
        stats["category"][t] += 1
    for t in tags:
        stats["tag"][t] += 1
    if spec["kind"] == "write" and "write" not in tags:
        errors.append(f"{where}: {tool} is a direct write and must carry the write tag")
    if spec["kind"] == "proposal" and not ({"proposal", "safety"} & set(tags)):
        errors.append(f"{where}: {tool} is a proposal and must carry the proposal or safety tag")
    if "follow_up" in tags and not history:
        errors.append(f"{where}: follow_up lines need history")
    if "context" in tags and "product_id" not in ctx:
        errors.append(f"{where}: context lines need context.product_id")

    reason = line["reason"]
    if not isinstance(reason, str) or not reason.endswith(".") or ". " in reason or "\n" in reason:
        errors.append(f"{where}: reason must be one sentence")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the agent eval set.")
    parser.add_argument("--file", default=str(DEFAULT_FILE))
    args = parser.parse_args()
    path = Path(args.file)
    if not path.is_file():
        print(f"validate_eval: {path} not found")
        return 1
    raw_lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    seed = load_seed()
    errors: list[str] = []
    stats = {"tool": Counter(), "category": Counter(), "tag": Counter()}
    seen: set = set()
    for i, raw in enumerate(raw_lines, start=1):
        check_line(i, raw, seed, seen, errors, stats)

    if errors:
        print(f"FAILED: {len(errors)} problem(s) in {len(raw_lines)} lines")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"OK: {len(raw_lines)} lines in {path.name} pass every check")
    print("category tags: " + ", ".join(f"{t} {stats['category'][t]}" for t in sorted(CATEGORY_TAGS)))
    print("all tags:      " + ", ".join(f"{t} {n}" for t, n in sorted(stats["tag"].items())))
    print("expect.tool:   " + ", ".join(f"{t} {n}" for t, n in sorted(stats["tool"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
