"""Chat memory on the orchestrator side: fact retrieval for a turn, and a background worker that extracts facts and
rewrites the conversation summary. The worker never blocks or fails a chat turn; failures are logged without content."""
import asyncio
import json
import logging
import re

from orchestrator.errors import ApiError
from orchestrator.llm import LLMError
from shopassist_proto import shopassist_pb2 as pb

log = logging.getLogger(__name__)

CATEGORIES = ("headphones", "hoodies", "backpacks", "watches", "sunglasses", "t-shirts", "jeans", "jackets", "shoes", "accessories")
FACT_LIMIT, MIN_SCORE, SUMMARY_WORDS = 5, 0.45, 200
QUEUE_MAX, CLOSE_WAIT_SECONDS = 100, 10.0
SUMMARY_MESSAGES, MESSAGE_CHARS = 12, 600  # the last 6 exchanges, each message cut short

_EXPLICIT = re.compile(r"\b(remember|forget|call me|from now on)\b", re.I)
_PERSONAL = re.compile(r"\b(i|i'm|im|i've|i'd|i'll|my|mine|looking for)\b", re.I)
_QUESTION_START = re.compile(
    r"^(do|does|did|is|are|was|were|can|could|would|will|what|which|who|how|where|when|why|show|tell|give)\b", re.I)

FACTS_SYSTEM = (
    "You maintain a shopper's long-term memory. From the user's message decide which facts to remember or forget. "
    "Permanent facts (kind=permanent) are lasting traits; temporal facts (kind=temporal) are a current need or short-lived plan. "
    "Suggested keys: preferred_name, size_top, size_bottom, shoe_size, height, weight, budget, favorite_color, liked_brands, "
    "disliked, style, need (temporal). op is remember or forget; for forget, value is the exact value to drop or \"*\" to forget the whole key. category is the shop category the fact concerns: "
    + ", ".join(CATEGORIES) + ", or \"\" when general. Only store what the user said about themselves; never invent. "
    "Return {\"changes\": []} when there is nothing to store."
)
SUMMARY_SYSTEM = (
    f"Summarise these recent exchanges of a shopping chat in at most {SUMMARY_WORDS} words: what the user wants, "
    "products/variants discussed by name, decisions made, and open questions. Return {\"summary\": \"...\"}."
)
FACTS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["changes"],
    "properties": {"changes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["op", "kind", "key", "value", "category"],
        "properties": {
            "op": {"type": "string", "enum": ["remember", "forget"]},
            "kind": {"type": "string", "enum": ["permanent", "temporal"]},
            "key": {"type": "string"}, "value": {"type": "string"},
            "category": {"type": "string", "enum": [*CATEGORIES, ""]},
        }}}},
}
SUMMARY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["summary"], "properties": {"summary": {"type": "string"}}}


def may_hold_fact(text: str) -> bool:
    """Cheap gate before paying for an LLM call: explicit memory words, or a statement (not a question) about the user."""
    if _EXPLICIT.search(text):
        return True
    for part in re.findall(r"[^.!?;,\n]+\??", text):
        clause = part.strip()
        if not clause or clause.endswith("?") or _QUESTION_START.match(clause):
            continue
        if _PERSONAL.search(clause):
            return True
    return False


def _parse_changes(data) -> list[dict]:
    if not isinstance(data, dict) or not isinstance(data.get("changes"), list):
        raise LLMError("memory: bad shape")
    out = []
    for c in data["changes"]:
        if not isinstance(c, dict):
            continue
        op, kind = c.get("op"), c.get("kind")
        key, value, category = str(c.get("key") or "").strip(), str(c.get("value") or "").strip(), str(c.get("category") or "")
        if op not in ("remember", "forget") or kind not in ("permanent", "temporal") or not key:
            continue
        if category not in (*CATEGORIES, "") or (op == "remember" and not value):
            continue
        if op == "forget" and not value:
            value = "*"  # the service rejects an empty forget value; "*" means the whole key
        out.append({"op": op, "kind": kind, "key": key, "value": value, "category": category})
    return out


def _parse_summary(data) -> str:
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        raise LLMError("memory: bad shape")
    return " ".join(data["summary"].split()[:SUMMARY_WORDS])


def fact_line(fact) -> str:
    return f"{fact.key}: {fact.value}"


class MemoryReader:
    def __init__(self, clients, embedder):
        self._clients, self._embedder = clients, embedder

    async def _call(self, rpc_name: str, request, user_id: str):
        try:
            return await self._clients.call("assistant", getattr(self._clients.assistant, rpc_name), request, user_id=user_id)
        except ApiError as e:
            # a chat that is not saved yet (a new chat's client-made id) is expected, not a problem
            (log.debug if e.code == "not_found" else log.warning)("memory %s failed: %s", rpc_name, e.code)
            return None

    async def context(self, user_id: str, conversation_id: str, message: str) -> dict:
        conv, recent = await asyncio.gather(
            self._call("GetConversation", pb.GetConversationRequest(conversation_id=conversation_id), user_id),
            self._call("ListMessages", pb.ListMessagesRequest(conversation_id=conversation_id, limit=2), user_id))
        summary = conv.summary if conv else ""
        facts = await self.search(user_id, f"{message} {summary[:300]}".strip())
        latest = [{"role": m.role, "text": m.text, "cards": parse_cards(m.cards_json)} for m in recent.messages] if recent else []
        return {"facts": facts, "summary": summary, "latest": latest}

    async def search(self, user_id: str, query: str) -> list[str]:
        vectors = await self._embedder.embed([query], "query")
        request = pb.SearchFactsRequest(query_text=query, query_embedding=vectors[0] if vectors else [], limit=FACT_LIMIT, min_score=MIN_SCORE)
        reply = await self._call("SearchFacts", request, user_id)
        return [fact_line(f) for f in reply.facts] if reply else []


def parse_cards(cards_json: str) -> list[dict]:
    """The cards saved with a message; [] when there are none or the text is not a list of cards."""
    try:
        cards = json.loads(cards_json) if cards_json else []
    except ValueError:
        return []
    return [c for c in cards if isinstance(c, dict) and "kind" in c] if isinstance(cards, list) else []


class MemoryWorker:
    """One queue, one consumer: jobs run one at a time in submit order."""

    def __init__(self, clients, llm, embedder, settings):
        self._clients, self._llm, self._embedder, self._settings = clients, llm, embedder, settings
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self.dropped = 0

    def start(self) -> None:
        self._queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._task = asyncio.create_task(self._consume())

    def submit(self, user_id: str, conversation_id: str, user_text: str, assistant_text: str) -> None:
        if self._queue is None:
            return
        try:
            self._queue.put_nowait((user_id, conversation_id, user_text, assistant_text))
        except asyncio.QueueFull:
            self.dropped += 1
            log.warning("memory queue full; job dropped (%d dropped so far)", self.dropped)

    async def close(self) -> None:
        if self._queue is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), CLOSE_WAIT_SECONDS)
        except asyncio.TimeoutError:
            log.warning("memory worker closing with %d jobs unfinished", self._queue.qsize())
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._queue = self._task = None

    async def _consume(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._run(*job)
            except Exception as e:  # a bad job must never stop the worker
                log.warning("memory job failed (%s)", type(e).__name__)
            finally:
                self._queue.task_done()

    async def _run(self, user_id: str, conversation_id: str, user_text: str, assistant_text: str) -> None:
        if self._llm is None:
            return
        for step in (self._facts, self._summary):
            try:
                await step(user_id, conversation_id, user_text, assistant_text)
            except Exception as e:
                log.warning("memory %s failed (%s)", step.__name__.lstrip("_"), type(e).__name__)

    async def _call(self, rpc_name: str, request, user_id: str):
        return await self._clients.call("assistant", getattr(self._clients.assistant, rpc_name), request, user_id=user_id)

    async def _facts(self, user_id: str, _conversation_id: str, user_text: str, _assistant_text: str) -> None:
        if not may_hold_fact(user_text):
            return
        changes = await self._llm.complete_json(
            FACTS_SYSTEM, user_text, "facts", FACTS_SCHEMA, _parse_changes, max_tokens=400, model=self._settings.memory_model)
        if not changes:
            return
        remembered = [c for c in changes if c["op"] == "remember"]
        vectors = await self._embedder.embed([f"{c['key']}: {c['value']}" for c in remembered], "document") if remembered else None
        embedding = {id(c): v for c, v in zip(remembered, vectors)} if vectors else {}
        await self._call("ApplyFacts", pb.ApplyFactsRequest(
            changes=[pb.FactChange(**c, embedding=embedding.get(id(c), [])) for c in changes]), user_id)

    async def _summary(self, user_id: str, conversation_id: str, _user_text: str, _assistant_text: str) -> None:
        reply = await self._call("ListMessages", pb.ListMessagesRequest(conversation_id=conversation_id, limit=SUMMARY_MESSAGES), user_id)
        prompt = "\n".join(f"{'User' if m.role == 'user' else 'Assistant'}: {m.text[:MESSAGE_CHARS]}" for m in reply.messages)
        summary = await self._llm.complete_json(
            SUMMARY_SYSTEM, prompt, "summary", SUMMARY_SCHEMA, _parse_summary, max_tokens=500, model=self._settings.memory_model)
        await self._call("SetSummary", pb.SetSummaryRequest(conversation_id=conversation_id, summary=summary), user_id)
