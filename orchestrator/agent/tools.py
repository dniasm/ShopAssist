"""The agent's tools, over the same gRPC clients the REST endpoints use. Each returns
{"tool", "args", "summary" (short text the model and the reply use), "card" ({"kind", "data"} for the UI or None)}.

Reads and cart edits run directly. propose_* never act: they store a proposal the person must confirm (see state.py).
Signed-out callers get a sign-in card from every account tool.
"""
from orchestrator import serialize
from orchestrator.agent.state import ProposalStore
from orchestrator.cart import priced_cart
from orchestrator.clients import Clients
from orchestrator.errors import ApiError
from orchestrator.routes_products import SORTS, stock_by_product
from shopassist_proto import shopassist_pb2 as pb

ACCOUNT_TOOLS = {"get_cart", "add_to_cart", "remove_from_cart", "list_orders", "get_order", "propose_checkout", "propose_cancel_order"}
CANCELLABLE = ("CONFIRMED", "PACKED")
SIGN_IN_TEXT = "Please sign in first (pick a profile at the top right) so I can use your cart and orders."


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def result(tool: str, args: dict, summary: str, kind: str | None = None, data: dict | None = None, **extra) -> dict:
    return {"tool": tool, "args": args, "summary": summary, "card": {"kind": kind, "data": data} if kind else None, **extra}


def describe_line(line: dict) -> str:
    return f"{line['quantity']} × {line['name']} ({line['product_id']}, {line['size']}, {line['color']}) {money(line['line_total_cents'])}"


class AgentTools:
    def __init__(self, clients: Clients, proposals: ProposalStore):
        self.clients, self.proposals = clients, proposals

    async def run(self, tool: str, args: dict, user_id: str | None, turn_id: str) -> dict:
        if tool in ACCOUNT_TOOLS and not user_id:
            return result(tool, args, SIGN_IN_TEXT, "signin", {"message": SIGN_IN_TEXT}, needs_sign_in=True)
        try:
            return await getattr(self, tool)(args, user_id, turn_id)
        except ApiError as e:
            if e.code == "not_found":
                return result(tool, args, f"I couldn't find that ({e.message}).")
            return result(tool, args, f"I couldn't do that right now: {e.message}")

    # ---------------- products ----------------

    async def search_products(self, a: dict, user_id, turn_id) -> dict:
        request = pb.ListProductsRequest(
            query=a.get("query") or "", categories=[a["category"]] if a.get("category") else [],
            brands=[a["brand"]] if a.get("brand") else [], colors=[a["color"].lower()] if a.get("color") else [],
            sizes=[str(a["size"])] if a.get("size") else [], min_price_cents=a.get("min_price_cents") or 0,
            max_price_cents=a.get("max_price_cents") or 0, sort=SORTS.get(a.get("sort") or "relevance", pb.SORT_RELEVANCE), page_size=6,
        )
        reply = await self.clients.call("catalog", self.clients.catalog.ListProducts, request)
        stock = await stock_by_product(self.clients, [p.product_id for p in reply.products])
        cards = [{**serialize.product(p), "in_stock": stock.get(p.product_id, 0) > 0} for p in reply.products]
        if not cards:
            return result("search_products", a, "I couldn't find anything matching that. Try a different colour, size or price.",
                          "products", {"products": [], "total": 0})
        shown = "; ".join(f"{i}. {c['name']} ({c['product_id']}) {money(c['price_cents'])}{'' if c['in_stock'] else ' - out of stock'}"
                          for i, c in enumerate(cards[:5], 1))
        close = " These are the closest matches." if reply.fuzzy else ""
        return result("search_products", a, f"I found {reply.total} product(s).{close} {shown}.", "products",
                      {"products": cards, "total": reply.total})

    async def _product(self, product_id: str):
        product = await self.clients.call("catalog", self.clients.catalog.GetProduct, pb.GetProductRequest(product_id=product_id))
        stock = await self.clients.call("inventory", self.clients.inventory.GetStock, pb.GetStockRequest(product_ids=[product_id]))
        return product, {level.sku_id: level.available for level in stock.levels}

    async def get_product(self, a: dict, user_id, turn_id) -> dict:
        product, available = await self._product(a["product_id"])
        options = [{"sku_id": s.sku_id, "size": s.size, "color": s.color, "available": available.get(s.sku_id, 0)} for s in product.skus]
        data = {"product": {**serialize.product(product), "skus": [serialize.sku(s) for s in product.skus]}, "stock": options}
        in_stock = sum(1 for o in options if o["available"] > 0)
        summary = (f"{product.name} ({product.product_id}) costs {money(product.price_cents)}, rated {product.rating:.1f}/5. "
                   f"Colours: {', '.join(product.colors)}. Sizes: {', '.join(product.sizes)}. {in_stock} of {len(options)} variants in stock. "
                   f"{product.description}")
        return result("get_product", a, summary, "product", data)

    async def get_stock(self, a: dict, user_id, turn_id) -> dict:
        product, available = await self._product(a["product_id"])
        size, color = (a.get("size") or "").lower(), (a.get("color") or "").lower()
        rows = [{"sku_id": s.sku_id, "size": s.size, "color": s.color, "available": available.get(s.sku_id, 0)}
                for s in product.skus if (not size or s.size.lower() == size) and (not color or s.color.lower() == color)]
        data = {"product_id": product.product_id, "name": product.name, "items": rows}
        if not rows:
            return result("get_stock", a, f"{product.name} ({product.product_id}) doesn't come in that. Sizes: "
                          f"{', '.join(product.sizes)}; colours: {', '.join(product.colors)}.", "stock", data)
        lines = ", ".join(f"{r['size']} {r['color']}: " + (f"{r['available']} left" if r["available"] else "sold out") for r in rows[:12])
        return result("get_stock", a, f"{product.name} ({product.product_id}) - {lines}.", "stock", data)

    # ---------------- cart ----------------

    async def _cart(self, user_id: str) -> tuple[pb.Cart, dict]:
        cart = await self.clients.call("customer", self.clients.customer.GetCart, pb.GetCartRequest(), user_id=user_id)
        return cart, await priced_cart(self.clients, cart)

    def _cart_summary(self, view: dict) -> str:
        if not view["lines"]:
            return "Your cart is empty."
        items = "; ".join(describe_line(line) for line in view["lines"])
        return f"Your cart: {items}. Subtotal {money(view['subtotal_cents'])}, shipping {money(view['shipping_cents'])}, total {money(view['total_cents'])}."

    async def get_cart(self, a: dict, user_id, turn_id) -> dict:
        _, view = await self._cart(user_id)
        return result("get_cart", a, self._cart_summary(view), "cart", view)

    async def add_to_cart(self, a: dict, user_id, turn_id) -> dict:
        product, available = await self._product(a["product_id"])
        sizes, colors = sorted({s.size for s in product.skus}), sorted({s.color for s in product.skus})
        size = a.get("size") or (sizes[0] if len(sizes) == 1 else None)
        color = a.get("color") or (colors[0] if len(colors) == 1 else None)
        sku = next((s for s in product.skus if size and color and s.size.lower() == str(size).lower() and s.color.lower() == color.lower()), None)
        if sku is None:
            return result("add_to_cart", a, f"Which size and colour for {product.name} ({product.product_id})? Sizes: {', '.join(product.sizes)}; "
                          f"colours: {', '.join(product.colors)}.")
        if available.get(sku.sku_id, 0) <= 0:
            return result("add_to_cart", a, f"Sorry, {product.name} in {sku.size} {sku.color} is sold out.")
        cart, _ = await self._cart(user_id)
        have = next((line.quantity for line in cart.lines if line.sku_id == sku.sku_id), 0)
        quantity = min(10, have + max(1, int(a.get("quantity") or 1)))
        updated = await self.clients.call(
            "customer", self.clients.customer.SetCartLine,
            pb.SetCartLineRequest(sku_id=sku.sku_id, product_id=product.product_id, quantity=quantity), user_id=user_id)
        view = await priced_cart(self.clients, updated)
        return result("add_to_cart", a, f"Added {product.name} ({product.product_id}, {sku.size}, {sku.color}) to your cart. "
                      f"You have {quantity} of it; cart total {money(view['total_cents'])}.", "cart", view)

    async def remove_from_cart(self, a: dict, user_id, turn_id) -> dict:
        cart, view = await self._cart(user_id)
        gone = [l for l in view["lines"] if l["product_id"] == a["product_id"]
                and (not a.get("size") or l["size"].lower() == a["size"].lower()) and (not a.get("color") or l["color"].lower() == a["color"].lower())]
        if not gone:
            return result("remove_from_cart", a, f"{a['product_id']} isn't in your cart.", "cart", view)
        for line in gone:
            cart = await self.clients.call("customer", self.clients.customer.SetCartLine,
                                           pb.SetCartLineRequest(sku_id=line["sku_id"], quantity=0), user_id=user_id)
        view = await priced_cart(self.clients, cart)
        return result("remove_from_cart", a, f"Removed {gone[0]['name']} ({a['product_id']}) from your cart. "
                      + self._cart_summary(view), "cart", view)

    # ---------------- orders ----------------

    async def list_orders(self, a: dict, user_id, turn_id) -> dict:
        reply = await self.clients.call("order", self.clients.order.ListOrders, pb.ListOrdersRequest(page_size=5), user_id=user_id)
        orders = [serialize.order_summary(o) for o in reply.orders]
        if not orders:
            return result("list_orders", a, "You have no orders yet.", "orders", {"orders": []})
        shown = "; ".join(f"{o['order_id']} {o['status'].lower().replace('_', ' ')} {money(o['total_cents'])}" for o in orders)
        return result("list_orders", a, f"Your latest orders: {shown}.", "orders", {"orders": orders})

    async def get_order(self, a: dict, user_id, turn_id) -> dict:
        order = await self.clients.call("order", self.clients.order.GetOrder, pb.GetOrderRequest(order_id=a["order_id"]), user_id=user_id)
        data = serialize.order(order)
        last = data["history"][-1]["message"] if data["history"] else ""
        items = ", ".join(f"{l['quantity']} × {l['name']}" for l in data["lines"])
        return result("get_order", a, f"Order {data['order_id']} is {data['status'].lower().replace('_', ' ')}: {items}, total "
                      f"{money(data['total_cents'])}. Latest update: {last}", "order", {"order": data})

    # ---------------- proposals: nothing happens until the person confirms ----------------

    async def propose_checkout(self, a: dict, user_id, turn_id) -> dict:
        _, view = await self._cart(user_id)
        if not view["lines"]:
            return result("propose_checkout", a, "Your cart is empty, so there is nothing to check out yet.", "cart", view)
        method = a.get("payment_method") or "card_ok"
        count = sum(line["quantity"] for line in view["lines"])
        summary = f"Place an order for {count} item(s), total {money(view['total_cents'])} (shipping included), paying with {method}."
        proposal = self.proposals.create(user_id, "checkout", {"payment_method": method}, summary, turn_id)
        return result("propose_checkout", a, summary + " Nothing is ordered until you confirm.", "cart", view, proposal=proposal)

    async def propose_cancel_order(self, a: dict, user_id, turn_id) -> dict:
        order = await self.clients.call("order", self.clients.order.GetOrder, pb.GetOrderRequest(order_id=a["order_id"]), user_id=user_id)
        data = serialize.order(order)
        status = data["status"]
        if status not in CANCELLABLE:
            return result("propose_cancel_order", a, f"Order {data['order_id']} is {status.lower().replace('_', ' ')}, so it can't be "
                          "cancelled: only confirmed or packed orders can.", "order", {"order": data})
        summary = f"Cancel order {data['order_id']} ({money(data['total_cents'])}); your card is refunded and the items go back to stock."
        proposal = self.proposals.create(user_id, "cancel_order", {"order_id": data["order_id"]}, summary, turn_id)
        return result("propose_cancel_order", a, summary + " Nothing is cancelled until you confirm.", "order", {"order": data}, proposal=proposal)
