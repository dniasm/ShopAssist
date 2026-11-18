"""Deterministic answer templates. Pure functions: (entities, data) -> text. `data` is what the dispatcher gathered."""

STATUS_TEXT = {
    "PLACED": "has been placed and is waiting to be packed",
    "PACKED": "has been packed and is ready to ship",
    "SHIPPED": "has shipped and is on its way",
    "OUT_FOR_DELIVERY": "is out for delivery",
    "DELIVERED": "has been delivered",
    "CANCELLED": "was cancelled",
}

HELP = (
    "I can help with: checking an order's status (\"Where is my order 1042?\"), "
    "tracking an order live (\"Track order 1043 live\"), finding products (\"Show me headphones under $100\"), "
    "checking stock (\"Do you have the blue hoodie in size M?\") and product details (\"Tell me about P-1003\")."
)


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _stock_text(levels: list[dict], size: str | None = None) -> str:
    if size:
        match = [lv for lv in levels if lv["size"].upper() == size.upper()]
        if not match:
            return f"size {size} is not carried"
        q = match[0]["quantity"]
        return f"size {size}: {q} in stock" if q > 0 else f"size {size}: out of stock"
    if not levels:
        return "no stock information"
    if len(levels) == 1 and levels[0]["size"] == "ONE":
        q = levels[0]["quantity"]
        return f"{q} in stock" if q > 0 else "out of stock"
    return ", ".join(f"{lv['size']}: {lv['quantity'] if lv['quantity'] > 0 else 'out of stock'}" for lv in levels)


def order_status(entities: dict, data: dict) -> str:
    oid = entities.get("order_id")
    if not oid:
        return "Which order do you mean? Please include the order number, for example \"Where is my order 1042?\"."
    order = data.get("order")
    if order is None:
        return f"I couldn't find order {oid}. Please check the number and try again."
    lines = [f"Order {order['order_id']} for {order['customer_name']} {STATUS_TEXT[order['status']]} "
             f"(last updated {order['updated_at']})."]
    for it in order["items"]:
        size = "" if it["size"] == "ONE" else f" (size {it['size']})"
        lines.append(f"- {it['quantity']} x {it.get('name', it['product_id'])}{size} at {_money(it['unit_price'])}")
    lines.append(f"Total: {_money(order['total'])}.")
    return "\n".join(lines)


def track_order(entities: dict, data: dict) -> str:
    oid = entities.get("order_id")
    if not oid:
        return "Which order should I track? Please include the order number, for example \"Track order 1043 live\"."
    updates = data.get("updates", [])
    if not updates:
        return f"I couldn't find order {oid}. Please check the number and try again."
    last = updates[-1]
    steps = " -> ".join(u["status"] for u in updates)
    if last["status"] == "CANCELLED":
        return f"Order {oid} was cancelled. ({last['message']})"
    if last["status"] == "DELIVERED":
        return f"Order {oid} has been delivered. Progress seen: {steps}."
    return f"Order {oid} {STATUS_TEXT[last['status']]}. Progress seen: {steps}."


def _dropped(entities: dict) -> str:
    return ", ".join(f"'{k}'" for k in entities.get("keywords", []))


def product_search(entities: dict, data: dict) -> str:
    products = data.get("products", [])
    if not products:
        return "I couldn't find any products matching that. Try a different category, colour or price."
    price = f" up to {_money(entities['max_price'])}" if "max_price" in entities else ""
    if data.get("relaxed"):  # the search words matched nothing, so they were dropped and the other filters kept
        what = " ".join(x for x in (entities.get("color"), entities.get("category") or "products") if x)
        head = f"No exact match for {_dropped(entities)}, but here are {what}{price}"
    else:
        head = f"I found {len(products)} product{'s' if len(products) != 1 else ''}{price}"
    lines = [head + ":"]
    for p in products:
        lines.append(f"- {p['name']} ({p['product_id']}), {p['color']}, {_money(p['price'])}")
    return "\n".join(lines)


def stock_check(entities: dict, data: dict) -> str:
    results = data.get("results", [])
    size = entities.get("size")
    if not results:
        return "I couldn't find a product matching that, so I can't check stock."
    lines = []
    for r in results:
        p = r["product"]
        lines.append(f"- {p['name']} ({p['product_id']}): {_stock_text(r['stock'], size)}")
    intro = "Here is the stock I found:\n" if len(lines) > 1 else "Stock check:\n"
    if data.get("relaxed"):
        intro = f"No exact match for {_dropped(entities)}, but here is what I found:\n"
    return intro + "\n".join(lines)


def product_info(entities: dict, data: dict) -> str:
    p = data.get("product")
    if p is None:
        return f"I couldn't find product {entities['product_id']}. Please check the product code."
    return (f"{p['name']} ({p['product_id']}) - {p['category']}, {p['color']}, {_money(p['price'])}.\n"
            f"{p['description']}\nAvailability: {_stock_text(data.get('stock', []))}.")


def unknown(entities: dict, data: dict) -> str:
    return "Sorry, I didn't understand that. " + HELP


COMPOSERS = {
    "order_status": order_status,
    "track_order": track_order,
    "product_search": product_search,
    "stock_check": stock_check,
    "product_info": product_info,
    "unknown": unknown,
}


def compose(intent: str, entities: dict, data: dict) -> str:
    return COMPOSERS[intent](entities, data)
