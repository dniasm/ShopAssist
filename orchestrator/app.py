"""FastAPI app: REST in, WebSocket event stream out, gRPC to the services. No database access here."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from orchestrator.dispatch import run_query
from orchestrator.grpc_clients import Clients
from orchestrator.llm import LLMClient
from orchestrator.settings import Settings
from orchestrator.store import QueryStore, make_event


class QueryIn(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be empty")
        return v.strip()


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if llm is None and settings.llm_api_keys:
        llm = LLMClient(settings)
    store = QueryStore()
    tasks: set[asyncio.Task] = set()  # strong refs so background queries are not garbage collected

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.clients = Clients(settings.product_addr, settings.inventory_addr, settings.order_addr)
        yield
        for t in list(tasks):
            t.cancel()
        await app.state.clients.close()

    app = FastAPI(title="ShopAssist orchestrator", lifespan=lifespan)
    # client/index.html is opened from file:// (Origin: null), so allow any origin. No cookies/credentials are used.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/llm/status")
    async def llm_status():
        """Per-key pool state. Keys are shown only as key#N plus their last 4 characters."""
        keys = await llm.pool.status() if llm is not None else []
        return {"enabled": llm is not None and len(keys) > 0, "model": settings.llm_model, "keys": keys}

    @app.post("/query", status_code=202)
    async def post_query(body: QueryIn):
        rec = store.create(body.text)
        await rec.emit("accepted", {"text": rec.text})
        task = asyncio.create_task(run_query(rec, app.state.clients, llm))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"query_id": rec.query_id, "ws_url": f"/ws/{rec.query_id}"}

    @app.get("/query/{query_id}")
    async def get_query(query_id: str):
        rec = store.get(query_id)
        if rec is None:
            raise HTTPException(404, "unknown query id")
        return {
            "query_id": rec.query_id, "status": rec.status, "intent": rec.intent,
            "entities": rec.entities, "answer": rec.answer, "data": rec.data,
        }

    @app.websocket("/ws/{query_id}")
    async def ws_query(websocket: WebSocket, query_id: str):
        await websocket.accept()
        rec = store.get(query_id)
        try:
            if rec is None:
                await websocket.send_json(make_event("error", query_id, {"message": "Unknown query id"}))
            else:
                async for event in rec.stream():
                    await websocket.send_json(event)
            await websocket.close()
        except WebSocketDisconnect:
            pass

    return app
