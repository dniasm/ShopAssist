"""The rule-based planner: no LLM, no network, no gRPC. It decides the FIRST action for a message and, once tools have
run, writes the answer from their summaries.

Deliberately simple and readable: pull the shop words out of the text (categories, colours, sizes, price words, ids),
resolve "this/it/the second one" from the page context and the conversation, then pick a tool by a short list of rules.
Anything it cannot place becomes a polite `reply` that asks or explains; it never guesses an id.
"""
import difflib
import re

from orchestrator.agent import vocab as V

PRODUCT_ID, ORDER_ID = re.compile(r"\bP-\d+\b", re.I), re.compile(r"\bO-\d+\b", re.I)
DEICTIC = re.compile(r"\b(this|it|that|these|those|they|them|one|ones)\b")
ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3, "last": -1}
GENERIC = {"the", "and", "remove", "delete", "take", "out", "add", "put", "cart", "basket", "that", "those", "my", "for", "any", "you"}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

OFFER = "I can help you find products, check stock and sizes, manage your cart, and track or cancel your orders."
SIGN_IN = "Please sign in first (pick a profile at the top right) and then ask me again; I need to know whose cart and orders to use."


def action(tool: str, args: dict | None = None, reply: str | None = None) -> dict:
    return {"tool": tool, "args": args or {}, "reply": reply, "source": "rules"}


def reply(text: str, **args) -> dict:
    return action("reply", args, text)


# ------------------------------------------------------------------ reading the text

def normalise(text: str) -> str:
    low = PRODUCT_ID.sub(" ", ORDER_ID.sub(" ", text.lower()))
    low = re.sub(r"\bt[\s-]*sh\w*", "tshirt", low)  # "t-shirt", "t shirt", "t shrits"
    return re.sub(r"['’]", "", low)


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalise(text))


def fuzzy(word: str, choices, cutoff: float = 0.78) -> str | None:
    """The closest choice for a misspelt word (same first letter, so 'show' never becomes 'shoe')."""
    if len(word) < 4 or word in V.STOP:
        return None
    close = difflib.get_close_matches(word, [c for c in choices if c[0] == word[0]], n=1, cutoff=cutoff)
    return close[0] if close else None


def category_of(words: list[str]) -> tuple[str | None, str]:
    for w in words:
        if w in V.WORD_TO_CATEGORY:
            return V.WORD_TO_CATEGORY[w], w
    for w in words:
        near = fuzzy(w, V.WORD_TO_CATEGORY, 0.75)
        if near:
            return V.WORD_TO_CATEGORY[near], near
    return None, ""


def colors_in(words: list[str]) -> list[str]:
    found = []
    for w in words:
        color = w if w in V.COLORS else V.COLOR_ALIASES.get(w) or fuzzy(w, V.COLORS)
        if color and color not in found:
            found.append(color)
    return found


def sizes_in(text: str) -> list[str]:
    """Sizes the person named: 'size L', 'in XL', 'size 32', a bare capital 'M'. Two different ones ('S to M') mean a range: none."""
    found: list[str] = []
    low = normalise(text)
    for m in re.finditer(r"\bsize\s+([a-z0-9]+)\b|\bin\s+(xs|s|m|l|xl|xxl)\b", low):
        found.append((m.group(1) or m.group(2)).upper())
    for m in re.finditer(r"\b(xs|xl|xxl)\b", low):
        found.append(m.group(1).upper())
    for m in re.finditer(r"\b(XS|S|M|L|XL|XXL)\b", re.sub(r"\bP-\d+\b", " ", text)):  # capital letters as typed: "M, navy"
        found.append(m.group(1))
    sizes = list(dict.fromkeys(s for s in found if s in V.SIZES))
    return sizes if len(sizes) == 1 else []


def prices_in(low: str) -> dict:
    money = r"\$?\s*(\d+(?:\.\d+)?)"
    filler = r"(?:like |about |around |roughly )?"
    out: dict = {}
    if m := re.search(rf"between {filler}{money}(?: dollars| bucks| quid)? (?:and|to) {filler}{money}", low):
        lo, hi = sorted((float(m.group(1)), float(m.group(2))))
        out = {"min_price_cents": round(lo * 100), "max_price_cents": round(hi * 100)}
    elif m := re.search(rf"(?:under|below|less than|max|up to|upto|at most|within|cheaper than) {filler}{money}", low):
        out = {"max_price_cents": round(float(m.group(1)) * 100)}
    elif m := re.search(rf"{money}(?: dollars| bucks)? (?:or less|or under|max|or below)", low):
        out = {"max_price_cents": round(float(m.group(1)) * 100)}
    elif m := re.search(rf"(?:over|above|more than|at least|from) {filler}{money}", low) and "size" not in low:
        out = {"min_price_cents": round(float(re.search(rf"(?:over|above|more than|at least|from) {filler}{money}", low).group(1)) * 100)}
    return out


def sort_of(low: str, words: list[str]) -> str | None:
    if re.search(r"\b(cheap\w*|budget|inexpensive|lowest price)\b", low) or fuzzy_has(words, "cheapest"):
        return "price_asc"
    if re.search(r"\b(priciest|most expensive|premium|luxury|highest price)\b", low):
        return "price_desc"
    if re.search(r"\b(best rated|top rated|highest rated|best reviewed|top[- ]rated)\b", low):
        return "rating"
    if re.search(r"\b(newest|latest|new arrivals?|whats new|just in)\b", low):
        return "newest"
    return None


def fuzzy_has(words: list[str], target: str) -> bool:
    return any(w == target or (len(w) > 5 and difflib.SequenceMatcher(None, w, target).ratio() > 0.85) for w in words)


def brand_in(text: str, words: list[str]) -> str | None:
    low = text.lower()
    for brand in V.BRANDS:
        if brand.lower() in low:
            return brand
    singles = {b.lower(): b for b in V.BRANDS if " " not in b}
    for w in words:
        near = fuzzy(w, singles, 0.85)
        if near:
            return singles[near]
    return None


def search_args(text: str) -> dict:
    """Everything a product search can take from this text. Empty when there is nothing shop-like in it."""
    words = tokens(text)
    low = normalise(text)
    args: dict = {}
    category, category_word = category_of(words)
    if category:
        args["category"] = category
    colors = colors_in(words)
    if len(colors) == 1:  # "black or grey" is two colours: leave it to the person to narrow
        args["color"] = colors[0]
    if sizes := sizes_in(text):
        args["size"] = sizes[0]
    if brand := brand_in(text, words):
        args["brand"] = brand
    args.update(prices_in(low))
    if sort := sort_of(low, words):
        args["sort"] = sort
    extra = []
    for w in words:
        feature = w if w in V.FEATURES else fuzzy(w, V.FEATURES, 0.8)
        noun = w.rstrip("s") if w.rstrip("s") in V.NOUNS else None
        if feature and feature not in extra:
            extra.append(feature)
        elif noun and noun not in extra:
            extra.append(noun)
    if extra:
        args["query"] = " ".join(extra + ([args["category"]] if "category" in args else []))
    return args


# ------------------------------------------------------------------ the conversation

def entities(history: list[dict], key: str, pattern: re.Pattern) -> list[str]:
    """Ids (products or orders) from the most recent assistant message that mentions any, in the order shown."""
    for turn in reversed(history):
        found = (turn.get("entities") or {}).get(key) or [i.upper() for i in pattern.findall(turn.get("assistant", ""))]
        if found:
            return list(dict.fromkeys(found))
    return []


def product_by_words(history: list[dict], words: list[str]) -> str | None:
    """The product in the last assistant message whose name carries these words ('the belt' -> the id after '... Belt')."""
    wanted = [w.rstrip("s") for w in words if w not in V.STOP and w not in GENERIC and w not in V.COLORS and len(w) > 2]
    if not wanted:
        return None
    for turn in reversed(history):
        for chunk in re.split(r"(?<=P-\d{4})", turn.get("assistant", "")):
            m = PRODUCT_ID.search(chunk)
            if m and any(w in chunk.lower() for w in wanted):
                return m.group(0).upper()
    return None


def variant_hints(history: list[dict], product_id: str) -> dict:
    """What the conversation already told us about a product's variants: 'one size', or a single colour mentioned."""
    for turn in reversed(history):
        text = turn.get("assistant", "")
        if product_id.lower() in text.lower():
            hints: dict = {}
            if re.search(r"\bone size\b", text, re.I):
                hints["size"] = "ONE"
            seen = colors_in(tokens(text))
            if len(seen) == 1:
                hints["color"] = seen[0]
            return hints
    return {}


def page_product_id(context: dict) -> str | None:
    """The product the person is looking at right now: from the product summary, the page's product id, or the /p/<id> path."""
    if pid := (context.get("product") or {}).get("product_id") or context.get("product_id"):
        return str(pid).upper()
    m = PRODUCT_ID.search(context.get("path", ""))
    return m.group(0).upper() if m else None


def resolve_product(low: str, words: list[str], text: str, history: list[dict], context: dict) -> str | None:
    if m := PRODUCT_ID.search(text):
        return m.group(0).upper()
    listed = entities(history, "products", PRODUCT_ID)
    for w in words:
        if w in ORDINALS and listed:
            index = ORDINALS[w]
            return listed[index] if -len(listed) <= index < len(listed) else None
    if DEICTIC.search(low) and page_product_id(context):  # the page wins over anything remembered
        return page_product_id(context)
    if DEICTIC.search(low) and len(listed) == 1:
        return listed[0]
    if re.search(r"\b(the|that|my)\b", low):
        return product_by_words(history, words)
    return None


def refine_search(text: str, history: list[dict], new: dict) -> dict | None:
    """'what about in navy?' after 'show me hoodies': the new constraints on top of the previous search."""
    if "category" in new or not any(k in new for k in ("color", "size", "sort", "brand", "max_price_cents", "min_price_cents")):
        return None
    for turn in reversed(history):
        previous = search_args(turn.get("user", ""))
        if "category" in previous:
            return {**previous, **new}
    return None


# ------------------------------------------------------------------ the decision

def plan(query: str, history: list[dict], context: dict, tool_results=()) -> dict:
    if tool_results:
        return reply(compose(tool_results))
    text = query.strip()
    low, words = normalise(text), tokens(text)
    signed_in = bool(context.get("signed_in"))
    product = context.get("product") or {}

    if re.search(V.OUT_OF_SCOPE, low) or re.match(r"why\b", low):
        return reply(OFFER + " I can't help with that one." if not low.startswith("why") else
                     "I suggest things that match what you asked for: category, colour, size and price. Tell me what to change and I'll search again.")
    if re.search(r"\brefund|pretend|\badmin\b|mark .* as (delivered|shipped|paid|refunded)", low):
        return reply("I can't issue refunds or change an order's status. I can prepare a cancellation for you to confirm, though.")

    order_ids = [i.upper() for i in ORDER_ID.findall(text)]
    if re.search(r"\bcancel(?!led)", low):
        if not signed_in:
            return reply(SIGN_IN, needs_sign_in=True)
        listed = entities(history, "orders", ORDER_ID)
        ordinal = next((ORDINALS[w] for w in words if w in ORDINALS), None)
        if order_ids:
            return action("propose_cancel_order", {"order_id": order_ids[0]})
        if ordinal is not None and listed and -len(listed) <= ordinal < len(listed):
            return action("propose_cancel_order", {"order_id": listed[ordinal]})
        if re.search(r"\b(all|every|everything)\b", low) or re.search(r"\bdont ask\b", low):
            return reply("I won't cancel orders in bulk. Tell me which order (for example O-10010) and I'll prepare it for you to confirm.")
        return action("list_orders")

    if re.search(r"check\s*out|\bpay\b|place (the|my|an|this) order|buy (it|them|this) now|ready to pay", low):
        return action("propose_checkout") if signed_in else reply(SIGN_IN, needs_sign_in=True)

    path_order = ORDER_ID.search(context.get("path", ""))
    if order_ids or re.search(r"\b(orders?|my stuff|parcel|package|delivery)\b|\btrack\b", low):
        if not signed_in:
            return reply(SIGN_IN, needs_sign_in=True)
        if order_ids or (path_order and re.search(r"\border\b", low)):
            return action("get_order", {"order_id": order_ids[0] if order_ids else path_order.group(0).upper()})
        return action("list_orders")

    asked_variant = bool(history) and re.search(r"which (size|colou?r)", history[-1].get("assistant", ""), re.I) and len(words) <= 4
    wants_cart = re.search(r"\b(cart|basket)\b", low)
    verb_add = re.search(r"\b(add|put|pop|throw)\b", low)
    verb_remove = re.search(r"\b(remove|delete|take .*out)\b", low)
    if verb_add or asked_variant or verb_remove or wants_cart:
        if not signed_in:
            return reply(SIGN_IN, needs_sign_in=True)
        target = resolve_product(low, words, text, history, context) or (entities(history, "products", PRODUCT_ID) or [None])[0] \
            if (verb_add or asked_variant or verb_remove) else None
        if verb_remove and target:
            return action("remove_from_cart", {"product_id": target})
        if (verb_add or asked_variant) and target:
            return plan_add(text, words, target, history, context, product)
        if verb_add or asked_variant or verb_remove:
            return reply("Which product do you mean? Open it, or give me its id (like P-1043), and I'll do it.")
        return action("get_cart")

    found = search_args(text)
    wants_stock = re.search(r"\b(stock|left|available|availability|come|comes|have|sizes)\b", low) and not re.search(r"\bother (colou?rs?|sizes?)\b", low)
    variant = {k: found[k] for k in ("size", "color") if k in found}
    page = page_product_id(context)
    plural = re.search(r"\b(those|these|them|ones)\b", low)
    if plural and not page and (refined := refine_search(text, history, found)):
        return action("search_products", refined)  # "the cheapest of those": the earlier search again, sorted
    target = None if ("category" in found and not DEICTIC.search(low)) else resolve_product(low, words, text, history, context)
    similar = re.search(r"\b(similar|like this|cheaper|alternatives?)\b", low)
    if not target and page and (variant or wants_stock or similar) and "category" not in found:
        target = page  # on a product page, stock / size / colour / "similar" questions are about THIS product
    if not target and variant and "category" not in found and len(entities(history, "products", PRODUCT_ID)) == 1:
        target = entities(history, "products", PRODUCT_ID)[0]
    if target:
        if re.search(r"\b(similar|like this|cheaper|alternatives?)\b", low):
            args = {k: v for k, v in found.items() if k != "query"}
            if product.get("category"):
                args["category"] = product["category"]
            if product.get("price_cents") and "cheaper" in low:
                args["max_price_cents"] = product["price_cents"] - 1
            if "query" in found:
                args["query"] = found["query"]
            return action("search_products", args or {"sort": "price_asc"})
        if variant or wants_stock:
            return action("get_stock", {"product_id": target, **variant})
        return action("get_product", {"product_id": target})

    if refined := refine_search(text, history, found):
        return action("search_products", refined)
    if "category" in found or "brand" in found or "query" in found:
        return action("search_products", found)
    if DEICTIC.search(low) or not words:
        return reply("Which product do you mean? Open it or tell me what you're after, for example 'red hoodies under $60', and I'll look.")
    return reply("I'm not sure what you're after. Tell me a category (hoodies, jackets, shoes, headphones, watches...), "
                 "and any colour, size or budget, and I'll search. " + OFFER)


def plan_add(text: str, words: list[str], product_id: str, history: list[dict], context: dict, product: dict) -> dict:
    """add_to_cart with size and colour settled, or a question when they are not."""
    found = search_args(text)
    hints = variant_hints(history, product_id)
    summary = product if product.get("product_id", "").upper() == product_id else {}
    size = found.get("size") or hints.get("size") or (summary["sizes"][0] if len(summary.get("sizes", [])) == 1 else None)
    color = found.get("color") or hints.get("color") or (summary["colors"][0] if len(summary.get("colors", [])) == 1 else None)
    if not size or not color:
        options = ""
        if summary:
            options = f" It comes in sizes {', '.join(summary.get('sizes', []))} and colours {', '.join(summary.get('colors', []))}."
        return reply(f"Which size and colour would you like for {product_id}?{options}")
    quantity = 1
    if m := re.search(r"\b(\d+|one|two|three|four|five)\s*(?:x|of)\b|\bx\s?(\d+)\b", normalise(text)):
        quantity = int(m.group(1) or m.group(2)) if (m.group(1) or "").isdigit() or m.group(2) else NUMBER_WORDS[m.group(1)]
    return action("add_to_cart", {"product_id": product_id, "size": size, "color": color, "quantity": max(1, min(quantity, 10))})


def compose(results: list[dict]) -> str:
    """The answer, from what the tools said (their summaries are already written for people)."""
    summaries = dict.fromkeys(r["summary"] for r in results[-3:] if r.get("summary"))  # a repeated call is said once
    return " ".join(summaries) or "Done."
