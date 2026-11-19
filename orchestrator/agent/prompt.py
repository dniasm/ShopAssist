"""What the model is told, and the strict JSON schema of what it may answer. The tool table is the contract in docs/PLAN.md."""
import json

TOOL_ARGS: dict[str, tuple[str, ...]] = {
    "search_products": ("query", "category", "color", "size", "brand", "min_price_cents", "max_price_cents", "sort"),
    "get_product": ("product_id",),
    "get_stock": ("product_id", "size", "color"),
    "get_cart": (),
    "add_to_cart": ("product_id", "size", "color", "quantity"),
    "remove_from_cart": ("product_id", "size", "color"),
    "list_orders": (),
    "get_order": ("order_id",),
    "propose_checkout": ("payment_method",),
    "propose_cancel_order": ("order_id",),
}
REQUIRED: dict[str, tuple[str, ...]] = {
    "get_product": ("product_id",), "get_stock": ("product_id",), "remove_from_cart": ("product_id",),
    "get_order": ("order_id",), "propose_cancel_order": ("order_id",),
}
SORTS = ["relevance", "price_asc", "price_desc", "rating", "newest"]
_INT = {"type": ["integer", "null"]}
_STR = {"type": ["string", "null"]}
SCHEMA_NAME = "agent_action"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "args", "reply"],
    "properties": {
        "action": {"type": "string", "enum": [*TOOL_ARGS, "reply"]},
        "args": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query", "category", "color", "size", "brand", "min_price_cents", "max_price_cents", "sort",
                         "product_id", "quantity", "order_id", "payment_method"],
            "properties": {
                "query": _STR, "category": _STR, "color": _STR, "size": _STR, "brand": _STR,
                "min_price_cents": _INT, "max_price_cents": _INT, "sort": {"type": ["string", "null"], "enum": [*SORTS, None]},
                "product_id": _STR, "quantity": _INT, "order_id": _STR, "payment_method": _STR,
            },
        },
        "reply": _STR,
    },
}

SYSTEM_PROMPT = """You are the shopping assistant of an online store. Each step you choose ONE action and answer with JSON:
{"action": <tool or "reply">, "args": {...}, "reply": <text or null>}. Use null for every arg you do not need.

Tools (money is integer cents: $50 = 5000):
- search_products: query?, category?, color?, size?, brand?, min_price_cents?, max_price_cents?, sort? (relevance, price_asc, price_desc, rating, newest)
- get_product: product_id        - get_stock: product_id, size?, color?
- get_cart - list_orders - get_order: order_id
- add_to_cart: product_id, size, color, quantity (default 1)      - remove_from_cart: product_id, size?, color?
- propose_checkout: payment_method?      - propose_cancel_order: order_id
- reply: answer or ask a question, no tool. When tool results are shown, write the final answer from them in "reply".

Categories: headphones, hoodies, backpacks, watches, sunglasses, t-shirts, jeans, jackets, shoes, accessories.
Colours: beige, black, blue, brown, burgundy, green, grey, navy, olive, orange, pink, red, white, yellow.

Rules:
- Money or irreversible actions (placing an order, cancelling one) are ONLY done with propose_checkout / propose_cancel_order: the
  person confirms with a button. Never say an order was placed or cancelled; say it is ready for them to confirm.
- add_to_cart needs product_id, size AND colour. If the person has not given a size or colour and the product has several,
  use "reply" to ask. If the product has only one size or colour (see the product in the context), use it.
- Never invent a product or order id. Use only ids from the context, the conversation or the tool results.
- "this", "it", "that one" mean the product on the current page (context.product_id) or the one just discussed.
- Keep a follow-up's earlier constraints ("show me hoodies" then "in navy?" means navy hoodies).
- Out of scope (weather, poems, general knowledge, anything not about this shop): "reply" politely, say what you can do
  (find products, check stock, manage the cart, track or cancel orders). Do not do it.
- If context.signed_in is false, cart and order questions get a "reply" asking them to sign in.
- When you write a reply that mentions products or orders, put their ids in brackets, like "Cozy Hoodie (P-1001)". Keep it short."""


def build_user_prompt(query: str, history: list[dict], context: dict, tool_results: list[dict]) -> str:
    shown = {k: context[k] for k in ("path", "product_id", "signed_in", "product", "cart") if k in context}
    turns = [{"user": t.get("user", ""), "assistant": t.get("assistant", "")} for t in history[-6:]]
    results = [{"tool": r.get("tool"), "args": r.get("args"), "summary": r.get("summary")} for r in tool_results]
    parts = [f"Context: {json.dumps(shown, ensure_ascii=False)}", f"Conversation so far: {json.dumps(turns, ensure_ascii=False)}"]
    if results:
        parts.append(f"Tool results so far this turn: {json.dumps(results, ensure_ascii=False)}")
    parts.append(f"Message: {query}")
    return "\n".join(parts)
