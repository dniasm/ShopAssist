"""Orchestrator configuration, read from environment variables."""
import logging
import os
import re
import secrets
from dataclasses import dataclass, field


def parse_keys(*raw: str | None) -> tuple[str, ...]:
    """Split comma/whitespace separated key lists, strip quotes, drop empties and duplicates (order kept)."""
    keys: dict[str, None] = {}
    for chunk in raw:
        for k in re.split(r"[,\s]+", chunk or ""):
            k = k.strip().strip("'\"")
            if k:
                keys[k] = None
    return tuple(keys)


log = logging.getLogger(__name__)
SECRET_WARNING = "SESSION_SECRET is not set: using a random one, so session tokens will not survive a restart"


@dataclass(frozen=True)
class Settings:
    catalog_addr: str = "localhost:50051"
    inventory_addr: str = "localhost:50052"
    order_addr: str = "localhost:50053"
    customer_addr: str = "localhost:50054"
    payment_addr: str = "localhost:50055"  # only health-checked from here: the Order service is the one that charges
    port: int = 8011
    session_secret: str = field(default="", repr=False)  # signs session tokens; secret, so out of repr/logs
    session_max_age_seconds: int = 12 * 3600
    signup_code: str = field(default="", repr=False)  # SIGNUP_CODE: when set, creating an account needs it
    unary_timeout: float = 2.0  # deadline for every unary gRPC call
    health_timeout: float = 1.5  # a health probe must answer quickly or count as down
    checkout_first_event_timeout: float = 5.0  # POST /checkout waits this long for the order id
    ws_auth_timeout_seconds: float = 5.0  # a new WebSocket must send its auth message within this long, or it is closed (4401)
    # Origins (besides the orchestrator's own host) allowed to open the WebSocket: the Vite dev server. A request with
    # no Origin header is not a browser (browsers always send one), so it is not a cross-site hijack and is let through.
    ws_allowed_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    ws_buffer_size: int = 500  # events kept per user for WebSocket replay
    ws_keep_seconds: float = 3600  # how long a user's log outlives their last socket
    order_watch_stop_seconds: float = 30  # a user's order stream stops this long after their last socket closes
    order_watch_retry_seconds: float = 1.0  # first wait before resuming a dropped order stream (doubles up to 5 s)
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_keys: tuple[str, ...] = field(default=(), repr=False)  # secrets: keep out of repr/logs
    llm_model: str = "openai/gpt-oss-120b"
    llm_reasoning_effort: str = "low"
    llm_max_tokens: int = 1024
    llm_timeout: float = 10.0  # total budget for one detection, across all key attempts
    llm_limit_rpm: int = 30
    llm_limit_rpd: int = 1000
    llm_limit_tpm: int = 8000
    llm_limit_tpd: int = 200000

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        env = os.environ.get
        if not env("SESSION_SECRET"):
            log.warning(SECRET_WARNING)
        return cls(
            catalog_addr=env("CATALOG_ADDR") or d.catalog_addr,
            inventory_addr=env("INVENTORY_ADDR") or d.inventory_addr,
            order_addr=env("ORDER_ADDR") or d.order_addr,
            customer_addr=env("CUSTOMER_ADDR") or d.customer_addr,
            payment_addr=env("PAYMENT_ADDR") or d.payment_addr,
            port=int(env("ORCHESTRATOR_PORT") or d.port),
            session_secret=env("SESSION_SECRET") or secrets.token_urlsafe(32),  # random: tokens die with the process
            signup_code=(env("SIGNUP_CODE") or "").strip(),
            ws_auth_timeout_seconds=float(env("WS_AUTH_TIMEOUT_SECONDS") or d.ws_auth_timeout_seconds),
            ws_allowed_origins=parse_keys(env("WS_ALLOWED_ORIGINS")) or d.ws_allowed_origins,
            llm_base_url=env("LLM_BASE_URL") or d.llm_base_url,
            # LLM_API_KEYS is the list; the legacy single LLM_API_KEY is merged in.
            llm_api_keys=parse_keys(env("LLM_API_KEYS"), env("LLM_API_KEY")),
            llm_model=env("LLM_MODEL") or d.llm_model,
            llm_reasoning_effort=env("LLM_REASONING_EFFORT") or d.llm_reasoning_effort,
            llm_max_tokens=int(env("LLM_MAX_TOKENS") or d.llm_max_tokens),
            llm_timeout=float(env("LLM_TIMEOUT") or d.llm_timeout),
            llm_limit_rpm=int(env("LLM_LIMIT_RPM") or d.llm_limit_rpm),
            llm_limit_rpd=int(env("LLM_LIMIT_RPD") or d.llm_limit_rpd),
            llm_limit_tpm=int(env("LLM_LIMIT_TPM") or d.llm_limit_tpm),
            llm_limit_tpd=int(env("LLM_LIMIT_TPD") or d.llm_limit_tpd),
        )
