"""plan_next_action with a scripted LLM (no network): valid actions, rejected output, and the fall-back to the rules."""
import asyncio
import json

import httpx

from orchestrator.agent.planner import plan_next_action
from orchestrator.llm import LLMClient, LLMError
from orchestrator.settings import Settings

CONTEXT = {"path": "/p/P-1006", "product_id": "P-1006", "signed_in": True}
NO_ARGS = {k: None for k in ("query", "category", "color", "size", "brand", "min_price_cents", "max_price_cents", "sort",
                             "product_id", "quantity", "order_id", "payment_method")}


def model_says(action, reply=None, **args):
    return {"action": action, "args": {**NO_ARGS, **args}, "reply": reply}


class ScriptedLLM:
    """Stands in for LLMClient: hands `parse` the next scripted object, exactly like the real client does."""

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    async def complete_json(self, system, user, schema_name, schema, parse, max_tokens=None):
        self.calls.append({"system": system, "user": user, "schema_name": schema_name, "schema": schema})
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return parse(out)


def plan(query, llm, history=(), context=None, tool_results=()):
    return asyncio.run(plan_next_action(query, list(history), context or CONTEXT, llm=llm, tool_results=list(tool_results)))


def test_a_valid_model_action_is_returned_with_source_llm_and_nulls_dropped():
    llm = ScriptedLLM(model_says("get_stock", product_id="P-1006", size="M"))
    action = plan("is this available in medium?", llm)
    assert action == {"tool": "get_stock", "args": {"product_id": "P-1006", "size": "M"}, "reply": None, "source": "llm"}


def test_the_prompt_carries_the_context_the_last_turns_and_the_tool_results():
    llm = ScriptedLLM(model_says("reply", "Here you go."))
    history = [{"user": f"q{i}", "assistant": f"a{i}"} for i in range(9)]
    results = [{"tool": "get_cart", "args": {}, "summary": "Your cart is empty."}]
    plan("hello", llm, history, {**CONTEXT, "product": {"product_id": "P-1006", "name": "Trucker"}}, results)
    user = llm.calls[0]["user"]
    assert "P-1006" in user and "Trucker" in user and "signed_in" in user
    assert "q8" in user and "q3" in user and "q2" not in user  # only the last six turns
    assert "Your cart is empty." in user and "Message: hello" in user
    assert llm.calls[0]["schema_name"] == "agent_action" and "propose_checkout" in llm.calls[0]["system"]


def test_an_invented_id_is_rejected_and_the_rules_decide():
    llm = ScriptedLLM(model_says("get_product", product_id="P-9999"))
    action = plan("show me hoodies", llm)
    assert action["source"] == "rules" and action["tool"] == "search_products"


def test_an_unknown_tool_or_a_missing_required_arg_falls_back_to_the_rules():
    for bad in (model_says("delete_everything"), model_says("get_order"), model_says("reply", None)):
        assert plan("show me hoodies", ScriptedLLM(bad))["source"] == "rules"


def test_an_llm_error_falls_back_to_the_rules():
    action = plan("cheapest watch", ScriptedLLM(LLMError("no API key has rate-limit headroom")))
    assert action["source"] == "rules" and action["args"]["sort"] == "price_asc"


def test_a_write_missing_its_size_or_colour_becomes_a_question():
    action = plan("add it", ScriptedLLM(model_says("add_to_cart", None, product_id="P-1006", size="M")))
    assert action["tool"] == "reply" and action["source"] == "llm" and "size" in action["reply"].lower()


def test_ids_from_the_conversation_and_tool_results_are_allowed():
    history = [{"user": "x", "assistant": "Try the Cozy Hoodie (P-1234)."}]
    assert plan("add it", ScriptedLLM(model_says("get_product", product_id="p-1234")), history)["args"] == {"product_id": "P-1234"}
    results = [{"tool": "list_orders", "args": {}, "summary": "You have O-10010 (packed)."}]
    action = plan("cancel it", ScriptedLLM(model_says("propose_cancel_order", order_id="O-10010")), context=CONTEXT, tool_results=results)
    assert action["tool"] == "propose_cancel_order"


# ---- through the real LLMClient: request shape and malformed bodies ----

def real_client(handler):
    settings = Settings(llm_api_keys=("gsk_FAKEKEY_test_1234567890abcd",), llm_base_url="http://llm.invalid/v1", llm_timeout=5.0)
    return LLMClient(settings, transport=httpx.MockTransport(handler))


def completion(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 300}},
                          headers={"x-ratelimit-remaining-requests": "999", "x-ratelimit-remaining-tokens": "7000"})


def test_through_the_real_client_the_request_is_a_strict_json_schema_and_the_answer_parses():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return completion(json.dumps(model_says("search_products", category="hoodies", color="navy")))

    action = plan("navy hoodies please", real_client(handler))
    schema = seen["body"]["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["name"] == "agent_action"
    assert "reply" in schema["schema"]["properties"]["action"]["enum"] and "add_to_cart" in schema["schema"]["properties"]["action"]["enum"]
    assert set(schema["schema"]["properties"]["args"]["required"]) == set(schema["schema"]["properties"]["args"]["properties"])  # strict mode
    assert seen["body"]["reasoning_effort"] == "low" and "reasoning_format" not in seen["body"]
    assert action["source"] == "llm" and action["args"] == {"category": "hoodies", "color": "navy"}


def test_a_malformed_response_body_falls_back_to_the_rules():
    for body in ("this is not json", "```json\n{\"action\": \"nope\"}\n```", "{}"):
        action = plan("show me hoodies", real_client(lambda request, b=body: completion(b)))
        assert action["source"] == "rules" and action["args"]["category"] == "hoodies"


def test_an_http_error_falls_back_to_the_rules():
    action = plan("show me hoodies", real_client(lambda request: httpx.Response(500)))
    assert action["source"] == "rules"


def test_a_400_json_validate_failed_still_uses_the_models_own_answer():
    """Found by the eval: Groq answers 400 when the model leaves out a nullable key, but sends its generation along."""
    generated = {"action": "search_products", "args": {"category": "headphones", "max_price_cents": 10000}, "reply": None}
    error = {"error": {"code": "json_validate_failed", "message": "missing properties: 'product_id'", "failed_generation": json.dumps(generated)}}
    action = plan("headphones under 100", real_client(lambda request: httpx.Response(400, json=error)))
    assert action["source"] == "llm" and action["args"] == {"category": "headphones", "max_price_cents": 10000}
    plain_400 = real_client(lambda request: httpx.Response(400, json={"error": {"message": "bad"}}))
    assert plan("show me hoodies", plain_400)["source"] == "rules"
