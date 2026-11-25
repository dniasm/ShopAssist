"""Compatibility shim until the turn loop is rewritten around the 8 tools: the old planner/rules still speak the old tool
names and read {"tool", "args", "summary", "card", "proposal"}. Deleted together with planner.py and rules.py."""
from orchestrator.agent.tools import AgentTools

SIGN_IN_ONLY = {"needs_sign_in"}


class LegacyTools:
    def __init__(self, tools: AgentTools):
        self.tools = tools

    async def run(self, tool: str, args: dict, user_id: str | None, turn_id: str) -> dict:
        name, new = await self._translate(tool, args, user_id)
        out = await self.tools.run(name, new, user_id, turn_id) if name else new
        extra = {k: out[k] for k in SIGN_IN_ONLY if k in out}
        return {"tool": tool, "args": args, "summary": out["final"] or out["text"], "card": out["card"], "proposal": out["proposal"], **extra}

    async def _translate(self, tool: str, a: dict, user_id: str | None) -> tuple[str | None, dict]:
        if tool == "search_products":
            cents = {k: a[f"{k}_cents"] / 100 for k in ("min_price", "max_price") if a.get(f"{k}_cents")}
            return "search", {**{k: a.get(k) for k in ("query", "category", "color", "size", "brand", "sort")}, **cents}
        if tool in ("get_product", "get_stock"):
            return "product", {"ids": [a["product_id"]], "size": a.get("size"), "color": a.get("color")}
        if tool == "get_cart":
            return "cart", {"action": "view"}
        if tool == "add_to_cart":
            return "cart", {"action": "add", **a}
        if tool == "remove_from_cart":
            _, view = await self.tools._cart(user_id) if user_id else (None, {"lines": []})
            lines = [l for l in view["lines"] if l["product_id"] == a["product_id"]
                     and (not a.get("size") or l["size"].lower() == a["size"].lower())
                     and (not a.get("color") or l["color"].lower() == a["color"].lower())]
            if not user_id or lines:
                for line in lines[1:]:
                    await self.tools.run("cart", {"action": "remove", "line_id": line["sku_id"]}, user_id, "")
                return "cart", {"action": "remove", "line_id": lines[0]["sku_id"] if lines else None}
            return None, {"text": f"{a['product_id']} isn't in your cart.", "final": None, "card": {"kind": "cart", "data": view}, "proposal": None}
        if tool == "list_orders":
            return "orders", {}
        if tool == "get_order":
            return "orders", {"order_id": a["order_id"]}
        if tool == "propose_checkout":
            return "checkout", {}
        if tool == "propose_cancel_order":
            return "cancel_order", {"order_id": a["order_id"]}
        return tool, a
