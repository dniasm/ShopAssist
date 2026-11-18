"""Orchestrator configuration, read from environment variables."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    product_addr: str = "localhost:50051"
    inventory_addr: str = "localhost:50052"
    order_addr: str = "localhost:50053"
    port: int = 8000
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout: float = 10.0

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        env = os.environ.get
        return cls(
            product_addr=env("PRODUCT_ADDR") or d.product_addr,
            inventory_addr=env("INVENTORY_ADDR") or d.inventory_addr,
            order_addr=env("ORDER_ADDR") or d.order_addr,
            port=int(env("ORCHESTRATOR_PORT") or d.port),
            llm_base_url=env("LLM_BASE_URL") or d.llm_base_url,
            llm_api_key=env("LLM_API_KEY") or "",
            llm_model=env("LLM_MODEL") or d.llm_model,
            llm_timeout=float(env("LLM_TIMEOUT") or d.llm_timeout),
        )
