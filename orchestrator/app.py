"""FastAPI gateway: REST under /api, gRPC to the services, the built web app at /. No database access here."""
import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from orchestrator import errors, routes_auth, routes_cart, routes_chat, routes_checkout, routes_health, routes_orders, routes_products, routes_ws
from orchestrator.agent.loop import AgentLoop
from orchestrator.agent.memory import MemoryReader, MemoryWorker
from orchestrator.agent.state import ConversationMemory, ProposalStore
from orchestrator.clients import Clients
from orchestrator.embed import Embedder
from orchestrator.events import EventHub
from orchestrator.order_watch import OrderWatchers
from orchestrator.llm import LLMClient
from orchestrator.schemas import ErrorResponse
from orchestrator.login_limits import LoginLimits
from orchestrator.session import SessionTokens
from orchestrator.settings import SECRET_WARNING, Settings
from orchestrator.spa import install_spa

log = logging.getLogger(__name__)
WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]  # the Vite dev server

# Documented once so /openapi.json (and the generated TypeScript types) show the real error shape.
ERROR_RESPONSES = {code: {"model": ErrorResponse, "description": "Error envelope"} for code in (401, 404, 422, 503)}


def create_app(settings: Settings | None = None, llm: LLMClient | None = None, web_dist: Path | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if llm is None and settings.llm_api_keys:
        llm = LLMClient(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.clients = Clients(settings)
        app.state.agent = AgentLoop(app.state.clients, app.state.hub, app.state.proposals, app.state.memory, app.state.llm)
        app.state.watchers = OrderWatchers(
            app.state.clients, app.state.hub, settings.order_watch_stop_seconds, settings.order_watch_retry_seconds)
        embedder = Embedder(settings)
        app.state.memory_reader = MemoryReader(app.state.clients, embedder)
        app.state.memory_worker = MemoryWorker(app.state.clients, app.state.llm, embedder, settings)
        app.state.memory_worker.start()
        yield
        await app.state.memory_worker.close()
        await app.state.watchers.close()
        for task in list(app.state.tasks):  # checkout forwarders still relaying events
            task.cancel()
        await asyncio.gather(*app.state.tasks, return_exceptions=True)
        await app.state.clients.close()

    app = FastAPI(title="ShopAssist API", lifespan=lifespan)
    app.state.settings = settings
    app.state.llm = llm
    app.state.hub = EventHub(settings.ws_buffer_size, settings.ws_keep_seconds)
    app.state.proposals = ProposalStore()
    app.state.memory = ConversationMemory()
    app.state.forwarding = set()  # (user_id, order_id) pairs whose checkout events are being relayed right now
    app.state.tasks = set()
    app.state.login_limits = LoginLimits()
    if not settings.session_secret:
        log.warning(SECRET_WARNING)
    app.state.tokens = SessionTokens(settings.session_secret or secrets.token_urlsafe(32), settings.session_max_age_seconds)
    app.add_middleware(
        CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    errors.install(app)
    routers = (routes_health, routes_auth, routes_products, routes_cart, routes_orders, routes_checkout, routes_chat, routes_ws)
    for router in (m.router for m in routers):
        app.include_router(router, prefix="/api", responses=ERROR_RESPONSES)
    install_spa(app, web_dist if web_dist is not None else WEB_DIST)
    return app
