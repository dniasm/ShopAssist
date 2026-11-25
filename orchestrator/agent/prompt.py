"""What the model is told: TOOLS (the 8 tool schemas, see tools.py) and SYSTEM_PROMPT."""

SORTS = ["relevance", "price_asc", "price_desc", "rating", "newest"]

# ---------------- tool-calling interface (OpenAI tool schemas) ----------------

def _fn(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []}}}


_S, _I = {"type": "string"}, {"type": "integer"}

TOOLS: list[dict] = [
    _fn("search", "Find products. Prices in dollars. 6 per page.", {
        "query": _S, "category": _S, "brand": _S, "color": _S, "size": _S, "min_price": _I, "max_price": _I,
        "sort": {"type": "string", "enum": SORTS}, "page": {"type": "integer", "description": "1-based"}}),
    _fn("product", "Details and stock of 1-3 products.", {
        "ids": {"type": "array", "items": _S, "maxItems": 3}, "size": _S, "color": _S}, ["ids"]),
    _fn("cart", "View or change the cart. set_qty/remove need a line_id from view.", {
        "action": {"type": "string", "enum": ["view", "add", "set_qty", "remove"]}, "product_id": _S, "size": _S,
        "color": _S, "quantity": _I, "line_id": _S}, ["action"]),
    _fn("orders", "List recent orders, or one order's details.", {"order_id": _S}),
    _fn("cancel_order", "Propose cancelling an order; the person confirms.", {"order_id": _S}, ["order_id"]),
    _fn("checkout", "Propose going to checkout; the person confirms. Never pays.", {}),
    _fn("open_page", "Open a page in the shop.", {
        "kind": {"type": "string", "enum": ["product", "shop", "orders", "order"]}, "product_id": _S, "order_id": _S,
        "query": _S, "category": _S}, ["kind"]),
    _fn("recall_facts", "Look up saved facts about the person (tastes, sizes, past choices).", {"query": _S}, ["query"]),
]

SYSTEM_PROMPT = """You are the assistant of an online clothing and accessories store. Help people find products, check stock, manage the cart and track orders, using the tools.

Rules:
- You never pay or place orders. For checkout call checkout; for cancelling call cancel_order. The person confirms with a button. Never say an order was placed or cancelled.
- If a request is ambiguous (which product, size, colour or cart line), ask a short question instead of guessing.
- Talk about products by name, size and colour. Never show product, order or line ids to the person; use them only in tool arguments, copied from tool results.
- Only state products, prices and stock that tool results show. Never invent any.
- Only shop topics. Politely decline anything else and say what you can help with.
- Saved facts (recall_facts) are hints about the person. The latest message always wins over them.
- When the person says it, that, or the first one, it means the product on the current page or the one just discussed.
- Keep earlier constraints in follow-ups: "show me hoodies" then "in navy?" means navy hoodies.
- Before add, remove or set_qty on a cart line, use the exact line_id from cart view when you have no id yet.
- Keep replies short."""
