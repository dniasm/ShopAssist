"""plan_next_action: the one entry point for "what should the agent do next?" (used by the chat loop and the eval scorer).

No gRPC in here. The caller fills `context` (path, product_id, signed_in, and `product` / `cart` summaries); `llm` is an
LLMClient (or anything with `complete_json`) or None for the rule-based planner. Any LLM failure falls back to the rules.
"""
import json
import logging
import re

from orchestrator.agent import rules
from orchestrator.agent.prompt import REQUIRED, SCHEMA, SCHEMA_NAME, SYSTEM_PROMPT, TOOL_ARGS, build_user_prompt
from orchestrator.llm import LLMError

log = logging.getLogger(__name__)
KNOWN_ID = re.compile(r"\b[PO]-\d+\b", re.I)
AGENT_MAX_TOKENS = 600  # an action or a short answer; low reasoning effort keeps the thinking well inside this
SIZES_MISSING = "Which size and colour would you like?"


def known_ids(query: str, history: list[dict], context: dict, tool_results) -> set[str]:
    """Ids the model may use: ones that appear in this conversation. Anything else would be invented."""
    blob = json.dumps([query, history, context, list(tool_results)], default=str)
    return {m.upper() for m in KNOWN_ID.findall(blob)}


def parse_action(data: dict, known: set[str]) -> dict:
    """Validate the model's JSON into an Action, or raise LLMError so the caller falls back to the rules."""
    tool = data.get("action")
    if tool not in TOOL_ARGS and tool != "reply":
        raise LLMError(f"unknown action {tool!r}")
    raw = data.get("args") or {}
    args = {k: v for k, v in raw.items() if v is not None and v != "" and k in TOOL_ARGS.get(tool, ())}
    for key in ("product_id", "order_id"):
        if key in args:
            args[key] = str(args[key]).upper()
            if args[key] not in known:
                raise LLMError(f"model invented {key} {args[key]}")
    for key in ("min_price_cents", "max_price_cents", "quantity"):
        if key in args:
            args[key] = int(args[key])
    for key in ("size", "color", "category", "brand", "query"):
        if key in args:
            args[key] = str(args[key])
    text = (data.get("reply") or "").strip() or None
    if tool == "add_to_cart" and not (args.get("product_id") and args.get("size") and args.get("color")):
        return {"tool": "reply", "args": {}, "reply": text or SIZES_MISSING, "source": "llm"}  # a write missing its variant: ask
    if tool == "reply" and not text:
        raise LLMError("reply without text")
    for key in REQUIRED.get(tool, ()):
        if key not in args:
            raise LLMError(f"{tool} without {key}")
    return {"tool": tool, "args": args, "reply": text, "source": "llm"}


async def plan_next_action(query: str, history: list[dict], context: dict, *, llm=None, tool_results=()) -> dict:
    """Action = {"tool", "args", "reply" (text when tool == "reply"), "source": "llm" | "rules"}."""
    tool_results = list(tool_results)
    if llm is not None:
        known = known_ids(query, history, context, tool_results)
        try:
            return await llm.complete_json(
                SYSTEM_PROMPT, build_user_prompt(query, history, context, tool_results), SCHEMA_NAME, SCHEMA,
                lambda data: parse_action(data, known), max_tokens=AGENT_MAX_TOKENS,
            )
        except LLMError as e:
            log.warning("agent planner: LLM failed (%s); using the rules", e)
    return rules.plan(query, history, context, tool_results)
