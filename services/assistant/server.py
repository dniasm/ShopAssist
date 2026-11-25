"""AssistantService (:50056): chat history and fact memory for the shopping assistant. Run: python -m services.assistant.server

The user comes from `x-user-id` metadata (set by the orchestrator after it verified the session), like Customer's carts.
"""
import asyncio
import json
import logging
from pathlib import Path

import grpc
from dotenv import load_dotenv

from services.assistant import db, facts as rules
from services.common.auth import require_user
from services.common.db import create_pool, database_url, safe_target
from services.common.migrate import apply_migrations
from services.common.server import Service, listen_address
from services.common.util import iso
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc

MIGRATIONS = Path(__file__).parent / "migrations"
DEFAULT_TITLE = "New chat"
MAX_TITLE = 120
MAX_TEXT = 20_000
MAX_SUMMARY = 20_000
MAX_CARDS = 200_000
MAX_CONVERSATIONS = 100
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 20
MAX_CHANGES = 50
MAX_FACT_KEY = 64
MAX_FACT_VALUE = 500
MAX_EMBEDDING = 4096


def to_conversation(row: dict) -> pb.Conversation:
    return pb.Conversation(
        conversation_id=row["conversation_id"], title=row["title"], summary=row["summary"],
        created_at=iso(row["created_at"]), updated_at=iso(row["updated_at"]),
    )


def to_message(row: dict) -> pb.ChatMessage:
    return pb.ChatMessage(
        message_id=row["message_id"], conversation_id=row["conversation_id"], role=row["role"], text=row["text"],
        cards_json=row["cards_json"] or "", created_at=iso(row["created_at"]),
    )


def to_fact(row: dict) -> pb.Fact:
    return pb.Fact(
        fact_id=row["fact_id"], kind=row["kind"], key=row["key"], value=row["value"], category=row["category"],
        expires_at=iso(row["expires_at"]), score=row.get("score", 0.0),
    )


class AssistantServicer(pb_grpc.AssistantServiceServicer):
    def __init__(self, pool, clock):
        self.pool = pool
        self.clock = clock

    @staticmethod
    async def _invalid(context, message: str):
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, message)

    @staticmethod
    async def _run(context, operation):
        """Await a database operation, turning 'not the caller's' into NOT_FOUND."""
        try:
            return await operation
        except db.NotFound as e:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"conversation {e} not found")

    async def CreateConversation(self, request, context):
        user_id = await require_user(context)
        title = request.title.strip()[:MAX_TITLE] or DEFAULT_TITLE
        return to_conversation(await db.create_conversation(self.pool, user_id, title))

    async def ListConversations(self, request, context):
        user_id = await require_user(context)
        limit = min(request.page_size, MAX_CONVERSATIONS) if request.page_size > 0 else MAX_CONVERSATIONS
        return pb.ListConversationsResponse(conversations=[to_conversation(r) for r in await db.list_conversations(self.pool, user_id, limit)])

    async def GetConversation(self, request, context):
        user_id = await require_user(context)
        return to_conversation(await self._run(context, db.get_conversation(self.pool, user_id, request.conversation_id)))

    async def RenameConversation(self, request, context):
        user_id = await require_user(context)
        title = request.title.strip()
        if not 1 <= len(title) <= MAX_TITLE:
            await self._invalid(context, f"a title is 1-{MAX_TITLE} characters")
        return to_conversation(await self._run(context, db.rename_conversation(self.pool, user_id, request.conversation_id, title)))

    async def DeleteConversation(self, request, context):
        user_id = await require_user(context)
        await self._run(context, db.delete_conversation(self.pool, user_id, request.conversation_id))
        return pb.DeleteConversationResponse()

    async def AppendMessage(self, request, context):
        user_id = await require_user(context)
        if request.role not in ("user", "assistant"):
            await self._invalid(context, "role must be 'user' or 'assistant'")
        if len(request.text) > MAX_TEXT:
            await self._invalid(context, f"text is at most {MAX_TEXT} characters")
        if request.cards_json:
            try:
                json.loads(request.cards_json)
            except ValueError:
                await self._invalid(context, "cards_json is not valid JSON")
            if len(request.cards_json) > MAX_CARDS:
                await self._invalid(context, f"cards_json is at most {MAX_CARDS} characters")
        row = await self._run(context, db.append_message(
            self.pool, user_id, request.conversation_id, request.role, request.text, request.cards_json))
        return to_message(row)

    async def ListMessages(self, request, context):
        user_id = await require_user(context)
        rows = await self._run(context, db.list_messages(self.pool, user_id, request.conversation_id, max(request.limit, 0)))
        return pb.ListMessagesResponse(messages=[to_message(r) for r in rows])

    async def SetSummary(self, request, context):
        user_id = await require_user(context)
        if len(request.summary) > MAX_SUMMARY:
            await self._invalid(context, f"a summary is at most {MAX_SUMMARY} characters")
        return to_conversation(await self._run(context, db.set_summary(self.pool, user_id, request.conversation_id, request.summary)))

    async def ApplyFacts(self, request, context):
        user_id = await require_user(context)
        if len(request.changes) > MAX_CHANGES:
            await self._invalid(context, f"at most {MAX_CHANGES} changes per call")
        changes = []
        for c in request.changes:
            key, value = rules.normalise_key(c.key), c.value.strip()
            if c.op not in ("remember", "forget"):
                await self._invalid(context, "op must be 'remember' or 'forget'")
            if not 1 <= len(key) <= MAX_FACT_KEY:
                await self._invalid(context, f"a fact key is 1-{MAX_FACT_KEY} characters")
            if c.op == "remember":
                if c.kind not in ("permanent", "temporal"):
                    await self._invalid(context, "kind must be 'permanent' or 'temporal'")
                if not 1 <= len(value) <= MAX_FACT_VALUE:
                    await self._invalid(context, f"a fact value is 1-{MAX_FACT_VALUE} characters")
                if len(c.embedding) > MAX_EMBEDDING:
                    await self._invalid(context, f"an embedding has at most {MAX_EMBEDDING} numbers")
            elif not value:
                await self._invalid(context, "forget needs a value, or '*' for every value of the key")
            changes.append({"op": c.op, "kind": c.kind, "key": key, "value": value, "category": c.category[:MAX_FACT_KEY],
                            "embedding": list(c.embedding)})
        rows = await db.apply_facts(self.pool, user_id, changes, self.clock())
        return pb.ListFactsResponse(facts=[to_fact(r) for r in rows])

    async def SearchFacts(self, request, context):
        user_id = await require_user(context)
        limit = min(request.limit, MAX_SEARCH_LIMIT) if request.limit > 0 else DEFAULT_SEARCH_LIMIT
        rows = await db.search_facts(
            self.pool, user_id, request.query_text, list(request.query_embedding) or None, limit, request.min_score, self.clock())
        return pb.ListFactsResponse(facts=[to_fact(r) for r in rows])

    async def ListFacts(self, request, context):
        user_id = await require_user(context)
        return pb.ListFactsResponse(facts=[to_fact(r) for r in await db.list_facts(self.pool, user_id, self.clock())])

    async def ClearTemporalFacts(self, request, context):
        user_id = await require_user(context)
        categories = [c.strip().lower() for c in request.categories]
        return pb.ClearTemporalFactsResponse(removed=await db.clear_temporal(self.pool, user_id, categories))


async def create_service(url: str, *, address: str = "127.0.0.1:0", clock=None) -> Service:
    """`clock` returns the current time as an aware datetime; tests pass their own to move past a fact's 30 days."""
    pool = await create_pool(url)
    await apply_migrations(pool, MIGRATIONS)
    service = Service("assistant", pool)
    service.register(pb_grpc.add_AssistantServiceServicer_to_server, AssistantServicer(pool, clock or rules.utcnow), "shopassist.v2.AssistantService")
    service.bind(address)
    return service


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s assistant %(levelname)s %(message)s")
    url = database_url("ASSISTANT_DATABASE_URL")
    logging.getLogger("assistant").info("database %s", safe_target(url))
    service = await create_service(url, address=listen_address("ASSISTANT_ADDR", "localhost:50056"))
    await service.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    except KeyboardInterrupt:
        pass
