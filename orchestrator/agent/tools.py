"""The agent's 8 tools, over the same gRPC clients the REST endpoints use.

search, product, cart (view/add/set_qty/remove), orders, cancel_order, checkout, open_page, recall_facts.
`AgentTools.run` returns a ToolResult dict:
  text      compact text for the model (see compact.py)
  card      {"kind", "data"} full data for the web UI, or None
  final     a ready reply that needs no second model call, or None (the model reasons on `text`)
  proposal  a stored Proposal the person must confirm, or None
  navigate  {"path": ...} for the web UI to open, or None
  error     True when the call failed or was ambiguous (nothing was changed)
Nothing here acts irreversibly: cancel_order only stores a proposal and checkout only proposes sending the person to the
checkout page (it never calls the Order service). Account tools without a signed-in user return a sign-in card.
"""
from urllib.parse import urlencode

from orchestrator import serialize
from orchestrator.agent.compact import cart_summary, money, order_line, product_line
from orchestrator.agent.memory import recall_lines
from orchestrator.agent.sizing import MAX_STEPS, base_size, resolve_fit, suggest_sizes
from orchestrator.agent.state import Proposal, ProposalStore
from orchestrator.cart import priced_cart
from orchestrator.clients import Clients
from orchestrator.errors import ApiError
from orchestrator.routes_products import SORTS, stock_by_product
from shopassist_proto import shopassist_pb2 as pb

TOOL_NAMES = {"search", "product", "cart", "orders", "cancel_order", "checkout", "open_page", "recall_facts"}
ACCOUNT_TOOLS = {"cart", "orders", "cancel_order", "checkout"}
CANCELLABLE = ("CONFIRMED", "PACKED")
SIGN_IN_TEXT = "Please sign in first (pick a profile at the top right) so I can use your cart and orders."
PAGE_SIZE = 6
MAX_QTY = 10
CLOSING = "Do any of them pique your interest?"


def result(text: str, card: dict | None = None, *, final: str | None = None, proposal: Proposal | None = None,
           navigate: dict | None = None, error: bool = False, **extra) -> dict:
    return {"text": text, "card": card, "final": final, "proposal": proposal, "navigate": navigate, "error": error, **extra}


def card(kind: str, data: dict) -> dict:
    return {"kind": kind, "data": data}


def status_word(status: str) -> str:
    return status.lower().replace("_", " ")


def product_list(c: dict, viewing: dict | None) -> tuple[str, str, dict]:
    """(intro, closing, card) for a product list. The cards name and link the products, so words only introduce them.
    On a product page, a list of that product's category is "similar to" it, and the product itself is left out of it."""
    products, total = c["data"]["products"], c["data"]["total"]
    if viewing and any(p.get("category") != viewing.get("category") for p in products):
        viewing = None  # e.g. "show me hoodies" while looking at a watch: not similar, just a search
    if viewing and any(p["product_id"] == viewing["product_id"] for p in products):
        products = [p for p in products if p["product_id"] != viewing["product_id"]]
        total -= 1
    if viewing:
        intro = f"There are {total} similar products to {viewing['name']}:"
    else:
        intro = f"I found {total} product{'' if total == 1 else 's'} for you:"
    return intro, CLOSING, {**c, "data": {**c["data"], "products": products, "total": total}}


class AgentTools:
    def __init__(self, clients: Clients, proposals: ProposalStore):
        self.clients, self.proposals = clients, proposals

    async def run(self, name: str, args: dict, user_id: str | None, turn_id: str, page_product: dict | None = None,
                  facts: list[dict] | None = None) -> dict:
        """`page_product` ({"product_id","name","category"}) is the product on the page, so a same-category search reads "similar to".
        `facts` are the person's saved facts, loaded once for the turn (None: no memory); recall_facts and sized searches read them."""
        if name not in TOOL_NAMES:
            return result(f"Unknown tool {name}.", error=True)
        if name in ACCOUNT_TOOLS and not user_id:
            return result(SIGN_IN_TEXT, card("signin", {"message": SIGN_IN_TEXT}), final=SIGN_IN_TEXT, needs_sign_in=True)
        try:
            if name == "search":
                return await self.t_search(args or {}, page_product, facts)
            if name == "recall_facts":
                return self.t_recall_facts(args or {}, facts if user_id else None)
            return await getattr(self, f"t_{name}")(args or {}, user_id, turn_id)
        except ApiError as e:
            if e.code == "not_found":
                return result(f"Not found: {e.message}", error=True)
            return result(f"Couldn't do that right now: {e.message}", error=True)
        except (KeyError, TypeError, ValueError) as e:
            return result(f"Bad arguments for {name}: {e!r}", error=True)

    # ---------------- products ----------------

    async def t_search(self, a: dict, page_product: dict | None = None, facts: list[dict] | None = None) -> dict:
        page = max(1, int(a.get("page") or 1))
        sizes, fit_note = [str(a["size"])] if a.get("size") else [], ""
        if not sizes and facts and a.get("category"):
            sizes, fit_note = self._fit_sizes(a, facts)
        request = pb.ListProductsRequest(
            query=a.get("query") or "", categories=[a["category"]] if a.get("category") else [],
            brands=[a["brand"]] if a.get("brand") else [], colors=[a["color"].lower()] if a.get("color") else [],
            sizes=sizes, min_price_cents=int(float(a.get("min_price") or 0) * 100),
            max_price_cents=int(float(a.get("max_price") or 0) * 100),
            sort=SORTS.get(a.get("sort") or "relevance", pb.SORT_RELEVANCE), page_size=PAGE_SIZE)
        reply = await self.clients.call("catalog", self.clients.catalog.ListProducts, request)
        for _ in range(page - 1):  # page tokens are opaque: walk to the wanted page
            if not reply.next_page_token:
                break
            request.page_token = reply.next_page_token
            reply = await self.clients.call("catalog", self.clients.catalog.ListProducts, request)
        stock = await stock_by_product(self.clients, [p.product_id for p in reply.products])
        cards = [{**serialize.product(p), "in_stock": stock.get(p.product_id, 0) > 0} for p in reply.products]
        if not cards:
            return result(f"No results{fit_note}.", card("products", {"products": [], "total": 0}))
        intro, _, shown = product_list(card("products", {"products": cards, "total": reply.total}), page_product)
        head = f"{reply.total} results" + (", closest matches" if reply.fuzzy else "") + f" (page {page})" + fit_note
        more = f"\nMore on page {page + 1}." if reply.next_page_token else ""
        return result(head + "\n" + "\n".join(product_line(i, c) for i, c in enumerate(cards, 1)) + more, shown, final=intro)

    async def _product(self, product_id: str):
        product = await self.clients.call("catalog", self.clients.catalog.GetProduct, pb.GetProductRequest(product_id=product_id))
        stock = await self.clients.call("inventory", self.clients.inventory.GetStock, pb.GetStockRequest(product_ids=[product_id]))
        return product, {level.sku_id: level.available for level in stock.levels}

    async def t_product(self, a: dict, user_id, turn_id) -> dict:
        ids = a["ids"] if isinstance(a["ids"], list) else [a["ids"]]
        ids = ids[:3]
        size, color = str(a.get("size") or "").lower(), (a.get("color") or "").lower()
        texts, first, failed = [], None, 0
        for pid in ids:
            try:
                product, available = await self._product(pid)
            except ApiError as e:
                texts.append(f"{pid}: not found ({e.message})")
                failed += 1
                continue
            options = [{"sku_id": s.sku_id, "size": s.size, "color": s.color, "available": available.get(s.sku_id, 0)} for s in product.skus]
            rows = [o for o in options if (not size or o["size"].lower() == size) and (not color or o["color"].lower() == color)]
            if first is None:
                if size or color:
                    first = card("stock", {"product_id": product.product_id, "name": product.name, "items": rows})
                else:
                    first = card("product", {"product": {**serialize.product(product), "skus": [serialize.sku(s) for s in product.skus]},
                                             "stock": options})
            head = (f"{product.name} [{product.product_id}] {money(product.price_cents)} · rated {product.rating:.1f}/5 · "
                    f"{', '.join(product.colors)} · {', '.join(product.sizes)}")
            if rows:
                stock_text = ", ".join(f"{o['size']} {o['color']} " + (str(o["available"]) if o["available"] else "sold out") for o in rows[:12])
            else:
                stock_text = "no such variant"
            texts.append(f"{head}\n{product.description}\nStock: {stock_text}")
        return result("\n\n".join(texts), first if len(ids) == 1 else None, error=failed == len(ids))

    # ---------------- cart ----------------

    async def _cart(self, user_id: str) -> tuple[pb.Cart, dict]:
        cart = await self.clients.call("customer", self.clients.customer.GetCart, pb.GetCartRequest(), user_id=user_id)
        return cart, await priced_cart(self.clients, cart)

    async def t_cart(self, a: dict, user_id, turn_id) -> dict:
        action = a.get("action") or "view"
        if action == "view":
            _, view = await self._cart(user_id)
            return result(cart_summary(view), card("cart", view))
        if action == "add":
            return await self._add(a, user_id)
        if action in ("set_qty", "remove"):
            return await self._edit(a, action, user_id)
        return result(f"Unknown cart action {action}. Use view, add, set_qty or remove.", error=True)

    async def _add(self, a: dict, user_id: str) -> dict:
        product, available = await self._product(a["product_id"])
        sizes, colors = sorted({s.size for s in product.skus}), sorted({s.color for s in product.skus})
        size = a.get("size") or (sizes[0] if len(sizes) == 1 else None)
        color = a.get("color") or (colors[0] if len(colors) == 1 else None)
        sku = next((s for s in product.skus if size and color and s.size.lower() == str(size).lower() and s.color.lower() == str(color).lower()), None)
        if sku is None:
            size_ok = bool(size) and any(s.size.lower() == str(size).lower() for s in product.skus)
            color_ok = bool(color) and any(s.color.lower() == str(color).lower() for s in product.skus)
            if size_ok and not color_ok:
                ask = f"Which colour would you like for {product.name} in {size}? Colours: {', '.join(product.colors)}."
            elif color_ok and not size_ok:
                ask = f"Which size would you like for {product.name} in {color}? Sizes: {', '.join(product.sizes)}."
            else:
                ask = f"Which size and colour would you like for {product.name}? Sizes: {', '.join(product.sizes)}; colours: {', '.join(product.colors)}."
            # The product card goes with the question, so the next turn knows which product the answer is about.
            options = [{"sku_id": s.sku_id, "size": s.size, "color": s.color, "available": available.get(s.sku_id, 0)} for s in product.skus]
            asked = card("product", {"product": {**serialize.product(product), "skus": [serialize.sku(s) for s in product.skus]},
                                     "stock": options})
            pending = f"Pending: add {product.name} [{product.product_id}]" + (f" size {size}" if size_ok else "") + (f" colour {color}" if color_ok else "")
            return result(f"{ask}\n{pending}", asked, final=ask)
        if available.get(sku.sku_id, 0) <= 0:
            sold = f"Sorry, {product.name} in {sku.size} {sku.color} is sold out."
            return result(sold, final=sold)
        cart, _ = await self._cart(user_id)
        have = next((line.quantity for line in cart.lines if line.sku_id == sku.sku_id), 0)
        added = max(1, int(a.get("quantity") or 1))
        quantity = min(MAX_QTY, have + added)
        updated = await self.clients.call(
            "customer", self.clients.customer.SetCartLine,
            pb.SetCartLineRequest(sku_id=sku.sku_id, product_id=product.product_id, quantity=quantity), user_id=user_id)
        view = await priced_cart(self.clients, updated)
        what = f"{product.name} ({sku.size}, {sku.color})" if added == 1 else f"{added} × {product.name} ({sku.size}, {sku.color})"
        cap = f" Limit is {MAX_QTY} per item." if have + added > MAX_QTY else ""
        text = f"Added {what}.{cap} Cart total {money(view['total_cents'])}."
        return result(text, card("cart", view), final=text)

    async def _edit(self, a: dict, action: str, user_id: str) -> dict:
        """set_qty / remove act only on an exact line id from the cart; nothing is guessed."""
        _, view = await self._cart(user_id)
        line = next((l for l in view["lines"] if l["sku_id"] == a.get("line_id")), None)
        if line is None:
            listing = cart_summary(view)
            return result(f"Unknown or missing line_id. Call again with the id in [] of one of these lines:\n{listing}",
                          card("cart", view), error=True)
        quantity = 0 if action == "remove" else max(0, min(MAX_QTY, int(a["quantity"])))
        updated = await self.clients.call("customer", self.clients.customer.SetCartLine,
                                          pb.SetCartLineRequest(sku_id=line["sku_id"], product_id=line["product_id"], quantity=quantity), user_id=user_id)
        view = await priced_cart(self.clients, updated)
        what = f"{line['name']} ({line['size']}, {line['color']})"
        tail = f"Cart total {money(view['total_cents'])}." if view["lines"] else "Your cart is now empty."
        text = f"Updated {what} to {quantity}. {tail}" if quantity else f"Removed {what}. {tail}"
        return result(text, card("cart", view), final=text)

    # ---------------- orders ----------------

    async def t_orders(self, a: dict, user_id, turn_id) -> dict:
        if a.get("order_id"):
            order = await self.clients.call("order", self.clients.order.GetOrder, pb.GetOrderRequest(order_id=a["order_id"]), user_id=user_id)
            data = serialize.order(order)
            items = ", ".join(f"{l['quantity']} × {l['name']} ({l['size']}, {l['color']})" for l in data["lines"])
            last = data["history"][-1]["message"] if data["history"] else ""
            final = f"Order {data['order_id']} is {status_word(data['status'])}: {items}." + (f" Latest: {last}" if last else "")
            return result(f"{order_line(data)}\n{items}\nLatest: {last}", card("order", {"order": data}), final=final)
        reply = await self.clients.call("order", self.clients.order.ListOrders, pb.ListOrdersRequest(page_size=5), user_id=user_id)
        orders = [serialize.order_summary(o) for o in reply.orders]
        if not orders:
            return result("No orders yet.", card("orders", {"orders": []}), final="You have no orders yet.")
        return result("\n".join(order_line(o) for o in orders), card("orders", {"orders": orders}), final="Here are your latest orders:")

    async def t_cancel_order(self, a: dict, user_id, turn_id) -> dict:
        order = await self.clients.call("order", self.clients.order.GetOrder, pb.GetOrderRequest(order_id=a["order_id"]), user_id=user_id)
        data = serialize.order(order)
        if data["status"] not in CANCELLABLE:
            text = (f"Order {data['order_id']} is {status_word(data['status'])}, so it can't be cancelled: "
                    "only confirmed or packed orders can.")
            return result(text, card("order", {"order": data}), final=text)
        summary = f"Cancel order {data['order_id']} ({money(data['total_cents'])}); your card is refunded and the items go back to stock."
        proposal = self.proposals.create(user_id, "cancel_order", {"order_id": data["order_id"]}, summary, turn_id)
        text = summary + " Nothing is cancelled until you confirm."
        return result(text, card("order", {"order": data}), final=text, proposal=proposal)

    async def t_checkout(self, a: dict, user_id, turn_id) -> dict:
        """Never orders: proposes sending the person to the checkout page, where they pay themselves."""
        _, view = await self._cart(user_id)
        if not view["lines"]:
            text = "Your cart is empty, so there is nothing to check out yet."
            return result(text, card("cart", view), final=text)
        summary = "Are you sure you'd like to check out?"
        proposal = self.proposals.create(user_id, "checkout_redirect", {"total_cents": view["total_cents"]}, summary, turn_id)
        return result(f"{summary} Cart total {money(view['total_cents'])}.", card("cart", view),
                      final=f"Your cart total is {money(view['total_cents'])}. {summary}", proposal=proposal)

    # ---------------- pages and memory ----------------

    async def t_open_page(self, a: dict, user_id, turn_id) -> dict:
        kind = a["kind"]
        if kind == "product":
            product = await self.clients.call("catalog", self.clients.catalog.GetProduct, pb.GetProductRequest(product_id=a["product_id"]))
            return self._go(f"/p/{product.product_id}", f"Opening {product.name}.")
        if kind == "shop":
            query = {k: v for k, v in (("category", a.get("category")), ("q", a.get("query"))) if v}
            return self._go("/shop" + (f"?{urlencode(query)}" if query else ""), "Opening the shop.")
        if kind not in ("orders", "order"):
            return result(f"Unknown page kind {kind}. Use product, shop, orders or order.", error=True)
        if not user_id:
            return result(SIGN_IN_TEXT, card("signin", {"message": SIGN_IN_TEXT}), final=SIGN_IN_TEXT, needs_sign_in=True)
        if kind == "orders":
            return self._go("/orders", "Opening your orders.")
        order = await self.clients.call("order", self.clients.order.GetOrder, pb.GetOrderRequest(order_id=a["order_id"]), user_id=user_id)
        return self._go(f"/orders/{order.order_id}", f"Opening order {order.order_id}.")

    @staticmethod
    def _go(path: str, text: str) -> dict:
        return result(text, final=text, navigate={"path": path})

    @staticmethod
    def _fit_sizes(a: dict, facts: list[dict]) -> tuple[list[str], str]:
        """The size filter for a fit: the model's `fit`, else a saved or wished-for one. ([], "") when there is no base size or no fit."""
        category = a["category"]
        base = base_size(facts, category)
        fit = str(a.get("fit") or "").lower() or resolve_fit("", facts, category)
        if not base or fit not in ("tight", "regular", "loose"):
            return [], ""
        steps = max(1, min(MAX_STEPS, int(a.get("fit_steps") or 1)))
        sizes = suggest_sizes(base, fit, category, None, steps)
        return sizes, f"; sizes {', '.join(sizes)} ({fit} fit on size {base.upper()})"

    @staticmethod
    def t_recall_facts(a: dict, facts: list[dict] | None) -> dict:
        if facts is None:
            return result("Memory is unavailable.")
        keys = a.get("keys") or []
        return result(recall_lines(facts, [keys] if isinstance(keys, str) else keys, str(a.get("query") or "")))
