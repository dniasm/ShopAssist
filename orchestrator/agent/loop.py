"""One chat turn: plan -> run a tool -> plan again with its result ... at most 4 steps, then the answer.

Events go to the person's session WebSocket on channel `chat`, in this order:
turn_started, (tool_call, tool_result, [proposal])*, message, turn_done. The web shop shows only the message and proposals. A failure sends `error` and still ends with `turn_done`.
The model never executes money or irreversible actions: propose_* only store a proposal that the person confirms (routes_chat).
"""
import asyncio
import logging
import re

from orchestrator.agent import rules
from orchestrator.agent.planner import plan_next_action
from orchestrator.agent.state import ConversationMemory, ProposalStore
from orchestrator.agent.tools import AgentTools
from orchestrator.cart import priced_cart
from orchestrator.clients import Clients
from orchestrator.errors import ApiError
from orchestrator.events import EventHub
from shopassist_proto import shopassist_pb2 as pb

log = logging.getLogger(__name__)
MAX_STEPS = 4
MAX_CARDS = 3
ID = re.compile(r"\b[PO]-\d+\b")


class AgentLoop:
    def __init__(self, clients: Clients, hub: EventHub, proposals: ProposalStore, memory: ConversationMemory, llm=None):
        self.clients, self.hub, self.memory, self.llm = clients, hub, memory, llm
        self.tools = AgentTools(clients, proposals)
        self._locks: dict[str, asyncio.Lock] = {}

    def emit(self, owner: str, type_: str, data: dict) -> None:
        self.hub.publish(owner, "chat", type_, data)

    async def run_turn(self, owner: str, user_id: str | None, turn_id: str, message: str, page: dict, conversation_id: str = "default") -> None:
        """One user message, start to finish. Turns of one person run one after the other, so their memory stays in order."""
        async with self._locks.setdefault(owner, asyncio.Lock()):
            self.emit(owner, "turn_started", {"turn_id": turn_id})
            source = "rules"
            try:
                source = await self._turn(owner, user_id, turn_id, message, page, f"{owner}|{conversation_id}")
            except Exception:
                log.exception("chat turn %s failed", turn_id)
                self.emit(owner, "error", {"turn_id": turn_id, "message": "Sorry, something went wrong on our side. Please try again."})
            self.emit(owner, "turn_done", {"turn_id": turn_id, "source": source})

    async def _turn(self, owner: str, user_id: str | None, turn_id: str, message: str, page: dict, memory_key: str) -> str:
        history = self.memory.history(memory_key)  # per conversation, not per user: a new chat starts clean
        context = await self.fill_context(user_id, page)
        results: list[dict] = []
        sources: set[str] = set()
        text, reply_args = None, {}
        for _ in range(MAX_STEPS):
            action = await plan_next_action(message, history, context, llm=self.llm, tool_results=results)
            sources.add(action["source"])
            if action["tool"] == "reply":
                text, reply_args = action["reply"], action["args"]
                break
            if any(r["tool"] == action["tool"] and r["args"] == action["args"] for r in results):
                break  # the planner asked for a call it already made: it learns nothing new, so answer now
            self.emit(owner, "tool_call", {"turn_id": turn_id, "tool": action["tool"], "args": action["args"]})
            outcome = await self.tools.run(action["tool"], action["args"], user_id, turn_id)
            results.append(outcome)
            self.emit(owner, "tool_result", {"turn_id": turn_id, "tool": action["tool"], "summary": outcome["summary"]})
            if proposal := outcome.get("proposal"):
                self.emit(owner, "proposal", {"turn_id": turn_id, "proposal_id": proposal.proposal_id, "action": proposal.action,
                                              "args": proposal.args, "summary": proposal.summary})
        if not text:  # the step cap was reached without a reply: say what the tools found
            text = rules.compose(results)
        cards = self.cards(results, reply_args)
        closing = ""
        if cards and cards[-1]["kind"] == "products" and cards[-1]["data"]["products"]:
            text, closing, cards[-1] = self.product_list(cards[-1], context.get("product"))
        self.emit(owner, "message", {"turn_id": turn_id, "text": text, "cards": cards, "closing": closing})
        self.memory.add(memory_key, message, self.remembered(text, results), self.entities(results))
        return "llm" if "llm" in sources else "rules"

    async def fill_context(self, user_id: str | None, page: dict) -> dict:
        """What the planner may know without calling anything itself: the page, the product on it, a cart summary."""
        context = {"path": page.get("path", "/"), "signed_in": bool(user_id)}
        if user_id:
            context["user_id"] = user_id
        if product_id := page.get("product_id"):
            context["product_id"] = product_id
            try:
                p = await self.clients.call("catalog", self.clients.catalog.GetProduct, pb.GetProductRequest(product_id=product_id))
                context["product"] = {"product_id": p.product_id, "name": p.name, "category": p.category, "price_cents": p.price_cents,
                                      "colors": list(p.colors), "sizes": list(p.sizes)}
            except ApiError:
                pass  # an unknown id on the page: the planner just has no summary
        if user_id:
            try:
                cart = await self.clients.call("customer", self.clients.customer.GetCart, pb.GetCartRequest(), user_id=user_id)
                view = await priced_cart(self.clients, cart)
                context["cart"] = {"items": sum(l["quantity"] for l in view["lines"]), "total_cents": view["total_cents"],
                                   "products": [l["product_id"] for l in view["lines"]]}
            except ApiError:
                pass
        return context

    @staticmethod
    def cards(results: list[dict], reply_args: dict) -> list[dict]:
        """The cards worth showing: the latest of each kind, newest last, at most three."""
        latest: dict[str, dict] = {}
        for r in results:
            if r.get("card"):
                latest[r["card"]["kind"]] = r["card"]
        if reply_args.get("needs_sign_in") and "signin" not in latest:
            latest["signin"] = {"kind": "signin", "data": {"message": rules.SIGN_IN}}
        return list(latest.values())[-MAX_CARDS:]

    @staticmethod
    def product_list(card: dict, viewing: dict | None) -> tuple[str, str, dict]:
        """A product list is answered by its cards (each one links to its page), not by repeating names and prices in words.
        On a product page, a list of that product's category is "similar to" it, and the product itself is left out of it."""
        products, total = card["data"]["products"], card["data"]["total"]
        if viewing and any(p.get("category") != viewing.get("category") for p in products):
            viewing = None  # e.g. "show me hoodies" while looking at a watch: not similar, just a search
        if viewing and any(p["product_id"] == viewing["product_id"] for p in products):
            products = [p for p in products if p["product_id"] != viewing["product_id"]]
            total -= 1
        if viewing:
            intro = f"There are {total} similar products to {viewing['name']}:"
        else:
            intro = f"I found {total} product{'' if total == 1 else 's'} for you:"
        return intro, "Do any of them pique your interest?", {**card, "data": {**card["data"], "products": products, "total": total}}

    @staticmethod
    def entities(results: list[dict]) -> dict:
        """Product and order ids this turn showed, in order: what "it", "the second one" and "cancel the first" will refer to."""
        products, orders = [], []
        for r in results:
            card = r.get("card") or {}
            data = card.get("data") or {}
            if card.get("kind") == "products":
                products += [p["product_id"] for p in data.get("products", [])]
            elif card.get("kind") == "product":
                products.append(data["product"]["product_id"])
            elif card.get("kind") == "stock":
                products.append(data["product_id"])
            elif card.get("kind") == "cart":
                products += [line["product_id"] for line in data.get("lines", [])]
            elif card.get("kind") == "orders":
                orders += [o["order_id"] for o in data.get("orders", [])]
            elif card.get("kind") == "order":
                orders.append(data["order"]["order_id"])
        return {"products": list(dict.fromkeys(products)), "orders": list(dict.fromkeys(orders))}

    def remembered(self, text: str, results: list[dict]) -> str:
        """The assistant text kept in memory: if it does not name the ids it showed, they are appended for the next planner call."""
        shown = [i for i in (self.entities(results)["products"] + self.entities(results)["orders"]) if i not in ID.findall(text)]
        return text + (f" [shown: {', '.join(shown)}]" if shown else "")
