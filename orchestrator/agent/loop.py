"""One chat turn on native tool calling: the model picks tools, the tools answer, at most MAX_STEPS model calls.

Events go to the person's session WebSocket on channel `chat`, in this order:
turn_started, [conversation], (tool_call, tool_result, [proposal], [navigate])*, message, turn_done. The web shop shows only the
message, proposals and navigation. A failure sends `error` and still ends with `turn_done`.
The model never executes money or irreversible actions: cancel_order and checkout only store a proposal that the person confirms.
When there is no model (or it fails before any tool ran) the rules planner answers read-only questions; it never writes.
"""
import asyncio
import json
import logging
import re

from orchestrator.agent import rules
from orchestrator.agent.compact import cart_summary, order_line, product_line
from orchestrator.agent.prompt import SYSTEM_PROMPT, TOOLS
from orchestrator.agent.state import ConversationMemory, ProposalStore
from orchestrator.agent.tools import CLOSING, AgentTools, product_list
from orchestrator.cart import priced_cart
from orchestrator.clients import Clients
from orchestrator.errors import ApiError
from orchestrator.events import EventHub
from orchestrator.llm import LLMError
from shopassist_proto import shopassist_pb2 as pb

log = logging.getLogger(__name__)
MAX_STEPS = 6
MAX_CARDS = 3
MAX_SHOWN_CHARS = 1500
TITLE_CHARS = 60
LLM_DOWN = "I can't do that right now. Please try again in a minute."
READ_ONLY = {"search_products", "get_product", "get_stock", "get_cart", "list_orders", "get_order"}  # the rules planner's read tools
WRITE_WORDS = re.compile(r"\b(add|remove|delete|cancel|check ?out|buy|purchase|put|take out)\b", re.I)
# A search with results ends the turn with its own reply and cards unless the message changes the cart ("find X and add it")
# or answers a question the assistant just asked ("green then, M and L").
PRODUCT_ID = re.compile(r"P-\d+")
BRACKETED_ID = re.compile(r"\s*[(\[]\s*(P-\d+)\s*[)\]]")


SORRY = "Sorry, something went wrong on our side. Please try again."


class AgentLoop:
    def __init__(self, clients: Clients, hub: EventHub, proposals: ProposalStore, memory: ConversationMemory, llm=None,
                 reader=None, worker=None):
        self.clients, self.hub, self.memory, self.llm = clients, hub, memory, llm
        self.reader, self.worker = reader, worker
        self.tools = AgentTools(clients, proposals)
        self._locks: dict[str, asyncio.Lock] = {}
        self._waiting: dict[str, int] = {}  # turns running or queued per owner

    def emit(self, owner: str, type_: str, data: dict) -> None:
        self.hub.publish(owner, "chat", type_, data)

    async def run_turn(self, owner: str, user_id: str | None, turn_id: str, message: str, page: dict, conversation_id: str = "default") -> None:
        """One user message, start to finish. Turns of one person run one after the other, so their memory stays in order."""
        lock = self._locks.setdefault(owner, asyncio.Lock())
        self._waiting[owner] = self._waiting.get(owner, 0) + 1
        try:
            async with lock:
                self.emit(owner, "turn_started", {"turn_id": turn_id})
                source = "rules"
                saved = {}  # set by _turn once the user message is saved: (conversation id)
                try:
                    source = await self._turn(owner, user_id, turn_id, message, page, conversation_id, saved)
                except Exception:
                    log.exception("chat turn %s failed", turn_id)
                    if "conversation_id" in saved and "answered" not in saved:
                        await self.save(owner, user_id, turn_id, saved["conversation_id"], "assistant", SORRY, "")
                    self.emit(owner, "error", {"turn_id": turn_id, "message": SORRY})
                self.emit(owner, "turn_done", {"turn_id": turn_id, "source": source})
        finally:
            self._waiting[owner] -= 1
            if not self._waiting[owner]:  # nobody else waits: forget the lock (a guest id would otherwise stay forever)
                del self._waiting[owner]
                self._locks.pop(owner, None)

    # ---------------- one turn ----------------

    async def _turn(self, owner: str, user_id: str | None, turn_id: str, message: str, page: dict, conversation_id: str,
                    saved: dict | None = None) -> str:
        saved = {} if saved is None else saved
        key = f"{owner}|{conversation_id}"  # per conversation, not per person: a new chat starts clean
        context = await self.fill_context(user_id, page)
        block = {"facts": [], "names": "", "all": [], "summary": "", "latest": []}
        if user_id and self.reader:  # before the new message is saved, so `latest` is the previous exchange
            block = await self.reader.context(user_id, conversation_id, message)
        elif not user_id:
            last = self.memory.history(key)[-1:]
            block["latest"] = [m for t in last for m in ({"role": "user", "text": t["user"], "cards": []},
                                                         {"role": "assistant", "text": t["assistant"], "cards": t["entities"].get("cards", [])})]
        saving = bool(user_id)
        if saving:
            conversation_id, saving = await self.save(owner, user_id, turn_id, conversation_id, "user", message, "", title=message)
            key = f"{owner}|{conversation_id}"  # the server's id, which the next message will carry
            if saving:
                saved["conversation_id"] = conversation_id

        results: list[dict] = []
        calls = tokens = 0
        text, final_reply, reply_args, llm_ok = "", False, {}, False
        if self.llm is not None:
            messages = self.model_messages(block, context, message, key)
            try:
                text, final_reply, calls, tokens = await self.converse(
                    owner, user_id, turn_id, messages, results, context.get("product"), block["all"] if self.reader and user_id else None)
                llm_ok = True
            except LLMError as e:
                log.warning("agent: LLM failed (%s)", e)
        if not llm_ok:
            if results:  # tools already ran this turn: say what they found rather than guess again
                text = self.said(results)
            else:
                text, reply_args, results = await self.fallback(owner, user_id, turn_id, message, key, context)
        text = self.without_ids(text, results)
        cards = self.cards(results, reply_args)
        closing = ""
        if cards and cards[-1]["kind"] == "products" and cards[-1]["data"]["products"]:
            if final_reply:
                closing = CLOSING
            elif not llm_ok and results:
                text, closing, cards[-1] = product_list(cards[-1], context.get("product"))
        self.emit(owner, "message", {"turn_id": turn_id, "text": text, "cards": cards, "closing": closing})
        self.remember(key, message, text, results, cards)
        if saving:
            saved["answered"] = True
            await self.save(owner, user_id, turn_id, conversation_id, "assistant", text, json.dumps(cards))
            if self.worker:
                self.worker.submit(user_id, conversation_id, message, text)
        log.info("chat turn llm_calls=%d tokens=%d", calls, tokens)
        return "llm" if llm_ok else "rules"

    async def converse(self, owner: str, user_id: str | None, turn_id: str, messages: list[dict], results: list[dict],
                       page_product: dict | None, facts: list[dict] | None = None) -> tuple[str, bool, int, int]:
        """The model/tool loop. Returns (text, text is a tool's own `final`, model calls, tokens). Raises LLMError."""
        seen: set[str] = set()
        calls = tokens = 0
        message = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        asked = next((m["content"] for m in reversed(messages[:-1]) if m["role"] == "assistant"), "")
        asked = asked.split("\n[Shown to the user:")[0].rstrip().removesuffix(CLOSING).rstrip().endswith("?")
        lookup_ends_turn = not WRITE_WORDS.search(message) and not asked
        for _ in range(MAX_STEPS):
            reply = await self.llm.chat(messages, TOOLS)
            calls += 1
            tokens += int(reply.get("usage") or 0)
            wanted = reply["tool_calls"]
            if not wanted:
                return (reply["content"] or "").strip() or self.said(results), False, calls, tokens
            messages.append({"role": "assistant", "content": reply["content"] or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["args"])}} for c in wanted]})
            repeated = False
            for c in wanted:
                signature = json.dumps([c["name"], c["args"]], sort_keys=True, default=str)
                if signature in seen:
                    repeated = True
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": "Already done this turn."})
                    continue
                seen.add(signature)
                out = await self.run_tool(owner, user_id, turn_id, c["name"], c["args"], page_product, facts)
                results.append(out)
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": out["text"]})
            if repeated:
                return self.said(results), False, calls, tokens  # it asked again for what it already has: answer with that
            lookup_goes_on = wanted[0]["name"] in ("search", "product") and not lookup_ends_turn
            if len(wanted) == 1 and results[-1]["final"] and not results[-1]["error"] and not lookup_goes_on:
                return results[-1]["final"], True, calls, tokens
        return self.said(results), False, calls, tokens

    async def run_tool(self, owner: str, user_id: str | None, turn_id: str, name: str, args: dict, page_product: dict | None,
                       facts: list[dict] | None = None) -> dict:
        self.emit(owner, "tool_call", {"turn_id": turn_id, "tool": name, "args": args})
        out = await self.tools.run(name, args, user_id, turn_id, page_product, facts)
        self.emit(owner, "tool_result", {"turn_id": turn_id, "tool": name, "summary": out["final"] or out["text"]})
        if proposal := out.get("proposal"):
            self.emit(owner, "proposal", {"turn_id": turn_id, "proposal_id": proposal.proposal_id, "action": proposal.action,
                                          "args": proposal.args, "summary": proposal.summary})
        if out.get("navigate"):
            self.emit(owner, "navigate", {"turn_id": turn_id, "path": out["navigate"]["path"]})
        return out

    @staticmethod
    def said(results: list[dict]) -> str:
        """The answer when the model wrote none: what the last tools said (a repeated call is said once)."""
        return rules.compose([{"summary": r["final"] or r["text"]} for r in results])

    async def fallback(self, owner: str, user_id: str | None, turn_id: str, message: str, key: str, context: dict) -> tuple[str, dict, list[dict]]:
        """No model: the rules planner may look things up, never change anything. Returns (text, reply args, results)."""
        action = rules.plan(message, self.memory.history(key), context, [])
        if action["tool"] == "reply":
            if WRITE_WORDS.search(message) and not action["args"].get("needs_sign_in"):
                return LLM_DOWN, {}, []  # a write the rules could not even pin down: not worth a guess
            return action["reply"], action["args"], []
        if action["tool"] not in READ_ONLY:
            return LLM_DOWN, {}, []
        name, args = rules.to_tool_call(action["tool"], action["args"])
        self.emit(owner, "tool_call", {"turn_id": turn_id, "tool": name, "args": args})
        out = await self.tools.run(name, args, user_id, turn_id)
        summary = out["final"] or out["text"]
        self.emit(owner, "tool_result", {"turn_id": turn_id, "tool": name, "summary": summary})
        extra = {"needs_sign_in": True} if out.get("needs_sign_in") else {}
        return rules.compose([{"summary": summary}]), extra, [{"text": summary, "final": None, "card": out["card"], "error": False}]

    # ---------------- what the model is told ----------------

    @classmethod
    def model_messages(cls, block: dict, context: dict, message: str, key: str = "") -> list[dict]:
        latest = [{"role": m["role"], "content": m["text"]} for m in block["latest"]]
        if block["latest"] and block["latest"][-1]["role"] == "assistant" and (shown := cls.shown_text(block["latest"][-1].get("cards") or [])):
            latest[-1]["content"] += f"\n[Shown to the user:\n{shown}]"
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "system", "content": cls.memory_text(block, context)},
                *latest, {"role": "user", "content": message}]

    @staticmethod
    def shown_text(cards: list[dict]) -> str:
        """What the last answer's cards listed, in the shopper's numbering, so "the second one" and "remove the L one" resolve."""
        lines: list[str] = []
        for c in cards:
            data = c.get("data") or {}
            try:
                if c["kind"] == "products":
                    lines += [product_line(i, p) for i, p in enumerate(data["products"], 1)]
                elif c["kind"] == "product":
                    lines.append(product_line(1, data["product"]))
                elif c["kind"] == "cart":
                    lines.append(cart_summary(data))
                elif c["kind"] == "orders":
                    lines += [order_line(o) for o in data["orders"]]
                elif c["kind"] == "order":
                    lines.append(order_line(data["order"]))
            except (KeyError, TypeError):
                continue  # a card in an older shape: skip it rather than fail the turn
        return "\n".join(lines)[:MAX_SHOWN_CHARS]

    @staticmethod
    def memory_text(block: dict, context: dict) -> str:
        lines = ["Memory"]
        if block.get("names"):
            lines.append(f"Facts you can recall: {block['names']}")
        if block["facts"]:
            lines += ["Facts that match this message:", *(f"- {f}" for f in block["facts"])]
        if block["summary"]:
            lines.append(f"Conversation so far: {block['summary']}")
        if context.get("cart_text"):
            lines += ["Their cart now:", context["cart_text"]]
        page = f"Page: {context['path']}"
        if product := context.get("product"):
            page += f" (they are viewing {product['name']} [{product['product_id']}], category {product['category']})"
        lines.append(page)
        return "\n".join(lines)

    async def fill_context(self, user_id: str | None, page: dict) -> dict:
        """What may be known without calling a tool: the page, the product on it, a cart summary."""
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
                pass  # an unknown id on the page: there is just no summary
        if user_id:
            try:
                cart = await self.clients.call("customer", self.clients.customer.GetCart, pb.GetCartRequest(), user_id=user_id)
                view = await priced_cart(self.clients, cart)
                context["cart"] = {"items": sum(l["quantity"] for l in view["lines"]), "total_cents": view["total_cents"],
                                   "products": [l["product_id"] for l in view["lines"]]}
                context["cart_text"] = cart_summary(view)
            except ApiError:
                pass
        return context

    # ---------------- saving and remembering ----------------

    async def save(self, owner: str, user_id: str, turn_id: str, conversation_id: str, role: str, text: str, cards_json: str,
                   title: str = "") -> tuple[str, bool]:
        """Append a message; a conversation the service does not know is created first. Returns (conversation id, still saving)."""

        def call(rpc: str, request):
            return self.clients.call("assistant", getattr(self.clients.assistant, rpc), request, user_id=user_id)

        def append(cid: str):
            return call("AppendMessage", pb.AppendMessageRequest(conversation_id=cid, role=role, text=text, cards_json=cards_json))

        try:
            try:
                await append(conversation_id)
            except ApiError as e:
                if e.code != "not_found" or not title:
                    raise
                created = await call("CreateConversation", pb.CreateConversationRequest(title=title[:TITLE_CHARS]))
                conversation_id = created.conversation_id
                self.emit(owner, "conversation", {"turn_id": turn_id, "conversation_id": conversation_id})
                await append(conversation_id)
            return conversation_id, True
        except ApiError as e:
            log.warning("saving a chat message failed: %s", e.code)
            return conversation_id, False

    def remember(self, key: str, message: str, text: str, results: list[dict], cards: list[dict]) -> None:
        self.memory.add(key, message, text, {**self.entities(results), "cards": cards})

    # ---------------- the answer ----------------

    @staticmethod
    def names(results: list[dict]) -> dict[str, str]:
        """Product id -> name for every product this turn's tools showed."""
        names: dict[str, str] = {}
        for r in results:
            c = r.get("card") or {}
            data = c.get("data") or {}
            items = [*data.get("products", []), *data.get("lines", [])]
            if c.get("kind") == "product":
                items.append(data["product"])
            if c.get("kind") == "stock" and data.get("name"):
                items.append({"product_id": data.get("product_id"), "name": data["name"]})
            names.update({i["product_id"]: i["name"] for i in items if i.get("product_id") and i.get("name")})
        return names

    @classmethod
    def without_ids(cls, text: str, results: list[dict]) -> str:
        """People read names, not ids: a product id in the reply becomes the product's name, or goes if the name is unknown."""
        if not PRODUCT_ID.search(text):
            return text
        names = cls.names(results)

        def bracketed(m: re.Match) -> str:
            name = names.get(m.group(1))
            return "" if not name or name in text else f" {name}"  # "Name (P-1)" -> "Name"

        text = BRACKETED_ID.sub(bracketed, text)
        text = PRODUCT_ID.sub(lambda m: names.get(m.group(0), ""), text)
        return re.sub(r"[ \t]{2,}", " ", re.sub(r"[ \t]+([.,;:!?])", r"\1", text)).strip()

    @staticmethod
    def cards(results: list[dict], reply_args: dict) -> list[dict]:
        """The cards worth showing: the latest of each kind, newest last, at most three."""
        latest: dict[str, dict] = {}
        for r in results:
            if r.get("card"):
                latest.pop(r["card"]["kind"], None)
                latest[r["card"]["kind"]] = r["card"]
        if reply_args.get("needs_sign_in") and "signin" not in latest:
            latest["signin"] = {"kind": "signin", "data": {"message": rules.SIGN_IN}}
        return list(latest.values())[-MAX_CARDS:]

    @staticmethod
    def entities(results: list[dict]) -> dict:
        """Product and order ids this turn showed, in order: what the rules planner's "it" and "cancel the first" refer to."""
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
