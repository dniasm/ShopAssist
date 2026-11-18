"""Rule-based intent + entity parser (deterministic, no network)."""
import logging
import re

INTENTS = ("order_status", "track_order", "product_search", "stock_check", "product_info", "unknown")

CATEGORY_WORDS = {
    "headphones": "headphones", "headphone": "headphones", "earphones": "headphones", "earphone": "headphones",
    "earbuds": "headphones", "earbud": "headphones", "headset": "headphones", "headsets": "headphones",
    "hoodies": "hoodies", "hoodie": "hoodies", "sweatshirt": "hoodies", "sweatshirts": "hoodies",
    "shoes": "shoes", "shoe": "shoes", "sneakers": "shoes", "sneaker": "shoes", "trainers": "shoes",
    "backpacks": "backpacks", "backpack": "backpacks", "bags": "backpacks", "bag": "backpacks",
    "rucksack": "backpacks", "rucksacks": "backpacks",
}
COLORS = {
    "black", "white", "blue", "red", "green", "gray", "grey", "navy", "pink", "yellow", "orange",
    "purple", "brown", "beige", "silver", "gold", "clear",
}
SIZE_WORDS = {"small": "S", "medium": "M", "large": "L", "xl": "XL"}

_PRICE_RES = [
    re.compile(r"(?:under|below|less than|cheaper than|up to|at most|max(?:imum)?|within|<=?)\s*\$?\s*(\d+(?:\.\d+)?)"),
    re.compile(r"\$\s*(\d+(?:\.\d+)?)"),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:dollars|usd|bucks)"),
]
_PRODUCT_ID_RE = re.compile(r"\bp-?(\d+)\b", re.I)
_ORDER_NUM_RE = re.compile(r"\border\s*(?:number|no\.?|id|#)?\s*#?\s*(\d+)", re.I)
_BARE_NUM_RE = re.compile(r"(?<![\w$.\-])#?(\d{3,})(?![\w.])")
_SIZE_RE = re.compile(r"\bsize\s+(xs|s|m|l|xl|xxl)\b", re.I)
_SIZE_CAPS_RE = re.compile(r"\b(XS|S|M|L|XL|XXL)\b")  # case-sensitive: avoids "what's" -> S

_ORDER_WORDS = {"order", "orders", "package", "parcel", "shipment", "delivery"}
_TRACK_WORDS = {"track", "tracking", "live", "follow", "watch", "realtime", "real-time", "stream"}
_STOCK_PHRASES = ("in stock", "stock", "available", "availability", "how many", "left in")
_SEARCH_WORDS = {"show", "find", "search", "looking", "browse", "list", "buy", "recommend", "cheap", "cheapest", "need", "want"}

_STOPWORDS = set("""
a an the any some me my i we you your do does did have has had is are was were be there it its this that these those
of in on at to for from with and or but not no yes please can could would should will just also
show find search looking look browse list buy recommend need want like get see give let us lets what whats which who where when how
many much more most less than under below within up max maximum cheaper cheap cheapest at least
tell about info information details detail describe more
size sizes stock available availability left
dollars dollar usd bucks price priced cost costs
order orders package parcel shipment delivery track tracking live follow watch
""".split())


def _extract_price(text: str) -> tuple[float | None, str]:
    """Return (max_price, text with the price phrases blanked out)."""
    price = None
    for rx in _PRICE_RES:
        m = rx.search(text)
        if m:
            price = float(m.group(1))
            break
    for rx in _PRICE_RES:
        text = rx.sub(" ", text)
    return price, text


def _extract_size(raw: str, lowered: str) -> str | None:
    m = _SIZE_RE.search(raw)
    if m:
        return m.group(1).upper()
    if re.search(r"\bextra[- ]large\b", lowered):
        return "XL"
    for word, size in SIZE_WORDS.items():
        if re.search(rf"\b{word}\b", lowered):
            return size
    m = _SIZE_CAPS_RE.search(raw)
    return m.group(1) if m else None


def parse_rules(text: str) -> tuple[str, dict]:
    """Return (intent, entities). Entities only contain keys that were found."""
    raw = text.strip()
    lowered = raw.lower()
    max_price, no_price = _extract_price(lowered)
    words = re.findall(r"[a-z0-9\-]+", no_price)
    wordset = set(words)

    entities: dict = {}
    pm = _PRODUCT_ID_RE.search(raw)
    if pm:
        entities["product_id"] = f"P-{pm.group(1)}"
    category = next((CATEGORY_WORDS[w] for w in words if w in CATEGORY_WORDS), None)
    if category:
        entities["category"] = category
    color = next((w for w in words if w in COLORS), None)
    if color:
        entities["color"] = color
    size = _extract_size(raw, no_price)
    if size:
        entities["size"] = size
    if max_price is not None:
        entities["max_price"] = max_price

    om = _ORDER_NUM_RE.search(no_price)
    bare = _BARE_NUM_RE.search(_PRODUCT_ID_RE.sub(" ", no_price))
    wants_order = bool(om) or (not pm and (bool(wordset & _ORDER_WORDS) or (bool(wordset & _TRACK_WORDS) and bool(bare))))
    if wants_order:
        order_id = om.group(1) if om else (bare.group(1) if bare else None)
        if order_id:
            entities["order_id"] = order_id
        intent = "track_order" if wordset & _TRACK_WORDS else "order_status"
        return intent, entities

    # Anything left over after removing known tokens becomes search keywords.
    known = set(CATEGORY_WORDS) | COLORS | set(SIZE_WORDS) | _STOPWORDS
    keywords = [
        w for w in words
        if w not in known and not re.fullmatch(r"p-?\d+|\d+|[a-z]|xs|xl|xxl|extra|-+", w)
    ]
    if keywords:
        entities["keywords"] = keywords

    has_product_ref = bool(pm or category or color or keywords)
    if any(p in lowered for p in _STOCK_PHRASES) and has_product_ref:
        return "stock_check", entities
    if size and (pm or category or color):
        return "stock_check", entities
    if pm:
        return "product_info", entities
    if category or max_price is not None or (wordset & _SEARCH_WORDS and has_product_ref):
        return "product_search", entities
    return "unknown", {}


async def detect_intent(text: str, llm=None) -> tuple[str, dict, str]:
    """Return (intent, entities, source). Uses the LLM when one is configured, else (or on any failure) the rules."""
    if llm is not None:
        try:
            intent, entities = await llm.detect(text)
            return intent, entities, "llm"
        except Exception as e:  # LLMError or anything unexpected: never let the LLM break a query
            logging.getLogger(__name__).warning("LLM intent failed (%s); using rules", e)
    intent, entities = parse_rules(text)
    return intent, entities, "rules"
