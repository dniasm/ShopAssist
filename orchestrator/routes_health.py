"""Liveness, per-service health (via grpc.health.v1) and LLM key-pool status."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from orchestrator.clients import SERVICES, Clients
from orchestrator.deps import get_clients
from orchestrator.schemas import HealthOut, LlmStatusOut, ServicesHealthOut

router = APIRouter()


@router.get("/health", response_model=HealthOut)
async def health():
    return {"status": "ok"}


@router.get("/health/services", response_model=ServicesHealthOut)
async def services_health(clients: Clients = Depends(get_clients)):
    """Always 200: the body says who is down, so the System page can render a partial outage."""
    services = await asyncio.gather(*(clients.probe(name) for name in SERVICES))
    return {
        "all_up": all(s["status"] == "up" for s in services),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "services": services,
    }


@router.get("/llm/status", response_model=LlmStatusOut)
async def llm_status(request: Request):
    """Per-key pool state. Keys appear only as key#N plus their last 4 characters."""
    llm, settings = request.app.state.llm, request.app.state.settings
    keys = await llm.pool.status() if llm is not None else []
    limits = {"rpm": settings.llm_limit_rpm, "rpd": settings.llm_limit_rpd,
              "tpm": settings.llm_limit_tpm, "tpd": settings.llm_limit_tpd}  # the caps each key's "left" counts down from
    return {"enabled": llm is not None and len(keys) > 0, "model": settings.llm_model, "limits": limits, "keys": keys}
