"""In-memory query records with an ordered event log that late WebSocket subscribers can replay."""
import asyncio
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

TERMINAL_EVENTS = {"final_answer", "error"}
MAX_QUERIES = 1000  # oldest records are dropped beyond this


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def make_event(type_: str, query_id: str, data: dict) -> dict:
    return {"type": type_, "query_id": query_id, "data": data, "ts": now_iso()}


class QueryRecord:
    def __init__(self, text: str):
        self.query_id = uuid.uuid4().hex
        self.text = text
        self.status = "pending"  # pending | running | streaming | done | error
        self.intent: str | None = None
        self.entities: dict = {}
        self.answer: str | None = None
        self.data: dict | None = None
        self.events: list[dict] = []
        self.finished = False
        self._cond = asyncio.Condition()

    async def emit(self, type_: str, data: dict) -> None:
        async with self._cond:
            self.events.append(make_event(type_, self.query_id, data))
            if type_ in TERMINAL_EVENTS:
                self.finished = True
            self._cond.notify_all()

    async def stream(self):
        """Yield every event from the start (replay), then live ones, ending after the terminal event.

        Each subscriber keeps its own index into the shared log, so any number of sockets
        get the full sequence no matter when they connect.
        """
        i = 0
        while True:
            async with self._cond:
                await self._cond.wait_for(lambda: i < len(self.events) or self.finished)
                batch = self.events[i:]
                i += len(batch)
                done = self.finished and i >= len(self.events)
            for event in batch:
                yield event
            if done:
                return


class QueryStore:
    def __init__(self):
        self._records: OrderedDict[str, QueryRecord] = OrderedDict()

    def create(self, text: str) -> QueryRecord:
        rec = QueryRecord(text)
        self._records[rec.query_id] = rec
        while len(self._records) > MAX_QUERIES:
            self._records.popitem(last=False)
        return rec

    def get(self, query_id: str) -> QueryRecord | None:
        return self._records.get(query_id)
