"""Request id across HTTP and gRPC: continued, validated, isolated per call; one access line per request and per RPC."""
import asyncio
import logging
import re
import time

import grpc
import pytest

from shared_common.common.log import get_request_id, set_request_id, valid_request_id
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from tests import authchan
from tests.test_agent_chat import FakeLLM, open_chat, say, says, turn, uses
from tests.test_api_shop import make_shop  # noqa: F401  (fixture)
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_api import settings  # noqa: F401  (fixture)

HEX12 = re.compile(r"[0-9a-f]{12}")


@pytest.fixture(autouse=True)
def debug_records(caplog):
    """caplog sees every record; the filter stamps the request id the way setup_logging's handlers do."""
    caplog.set_level(logging.DEBUG)

    def stamp(record):
        record.request_id = get_request_id()
        return True

    caplog.handler.addFilter(stamp)
    yield
    caplog.handler.removeFilter(stamp)


def test_http_request_id_header(client):
    assert client.get("/api/health", headers={"X-Request-Id": "abc123"}).headers["x-request-id"] == "abc123"
    assert HEX12.fullmatch(client.get("/api/health").headers["x-request-id"])


@pytest.mark.parametrize("bad", ["a" * 300, "x\ny", "../x", ""])
def test_bad_request_id_replaced(client, bad):
    try:
        response = client.get("/api/health", headers={"X-Request-Id": bad})
    except Exception:  # an HTTP client may refuse a header with a newline before it is sent
        pytest.skip("client refuses to send this header")
    assert HEX12.fullmatch(response.headers["x-request-id"])


def test_valid_request_id_rules():
    assert valid_request_id("a.b_c-1") == "a.b_c-1" and valid_request_id("a" * 64)
    assert valid_request_id("a" * 65) is None and valid_request_id("x\ny") is None and valid_request_id(None) is None


def test_http_access_line_escapes_control_characters(client, caplog):
    client.get("/api/x%0a12:00:00 INFO    [order] [abc] audit: fake")
    seen = [r for r in caplog.records if r.name == "orchestrator.request_log"]
    assert seen
    message = seen[-1].getMessage()
    assert "\\n12:00:00" in message and "\n" not in message
    assert all("\n" not in r.getMessage() for r in caplog.records)


def test_http_access_line(client, caplog):
    client.get("/api/health?x=1", headers={"X-Request-Id": "rid-http"})
    seen = [r for r in caplog.records if r.name == "orchestrator.request_log"]
    assert seen and re.fullmatch(r"GET /api/health -> 200 \d+ms user=-", seen[-1].getMessage())
    assert seen[-1].levelno == logging.DEBUG and seen[-1].request_id == "rid-http"  # health checks are not worth INFO


def _access(caplog, rpc, wait: float = 2.0):
    """The server's access lines for `rpc`. The server logs after replying, so wait briefly for the line to land."""
    deadline = time.monotonic() + wait
    while True:
        seen = [r for r in caplog.records if r.name == "services.common.server" and r.getMessage().startswith(rpc)]
        if seen or time.monotonic() > deadline:
            return seen
        time.sleep(0.02)


def test_grpc_continues_request_id(loop_thread, catalog_running, caplog):
    async def call():
        set_request_id("rid1")
        async with authchan.aio_channel(catalog_running.addr) as ch:
            await pb_grpc.CatalogServiceStub(ch).ListProducts(pb.ListProductsRequest())

    loop_thread.call(call())
    seen = _access(caplog, "CatalogService/ListProducts")
    assert seen and seen[-1].request_id == "rid1" and "OK" in seen[-1].getMessage()
    assert "caller=orchestrator" in seen[-1].getMessage() and seen[-1].levelno == logging.INFO


def test_grpc_not_found_is_info(loop_thread, catalog_running, caplog):
    async def call():
        set_request_id("rid-miss")
        async with authchan.aio_channel(catalog_running.addr) as ch:
            with pytest.raises(grpc.aio.AioRpcError):
                await pb_grpc.CatalogServiceStub(ch).GetProduct(pb.GetProductRequest(product_id="P-0000"))

    loop_thread.call(call())
    seen = _access(caplog, "CatalogService/GetProduct")
    assert seen and seen[-1].levelno == logging.INFO and "NOT_FOUND" in seen[-1].getMessage()
    assert seen[-1].request_id == "rid-miss"


def test_ids_isolated_between_concurrent_calls(loop_thread, catalog_running, caplog):
    async def call(ch, rid):
        set_request_id(rid)
        await pb_grpc.CatalogServiceStub(ch).ListProducts(pb.ListProductsRequest())

    async def both():
        async with authchan.aio_channel(catalog_running.addr) as ch:
            await asyncio.gather(*(asyncio.create_task(call(ch, rid)) for rid in ("rid-a", "rid-b") * 5))

    loop_thread.call(both())
    ids = [r.request_id for r in _access(caplog, "CatalogService/ListProducts")]
    assert sorted(ids) == ["rid-a"] * 5 + ["rid-b"] * 5


def test_unexpected_exception_is_logged_with_traceback(loop_thread, catalog_running, caplog):
    from services.common.server import RequestIdInterceptor

    class Details:
        method = "/shopassist.v2.CatalogService/Boom"
        invocation_metadata = (("x-request-id", "rid-boom"),)

    async def boom(request, context):
        raise RuntimeError("kaput")

    async def scenario():
        handler = await RequestIdInterceptor().intercept_service(
            lambda details: _ready(grpc.unary_unary_rpc_method_handler(boom)), Details())
        with pytest.raises(RuntimeError):
            await handler.unary_unary(None, None)

    async def _ready(value):
        return value

    loop_thread.call(scenario())
    seen = [r for r in caplog.records if r.name == "services.common.server" and "Boom failed" in r.getMessage()]
    assert seen and seen[0].levelno == logging.ERROR and seen[0].exc_info and seen[0].request_id == "rid-boom"


def test_chat_turn_lines_share_id(make_shop, caplog):
    class LoudLLM(FakeLLM):
        async def chat(self, messages, tools, **kw):
            logging.getLogger("orchestrator.llm").info("llm call fake")
            return await super().chat(messages, tools, **kw)

    shop = make_shop(llm=LoudLLM(uses(("search", {"query": "shirt"})), says("Here you go.")))
    headers, ws = open_chat(shop)
    posted = shop.client.post("/api/chat", json={"message": "a shirt please", "context": {"path": "/shop"}}, headers=headers)
    assert posted.status_code == 202
    http_id = posted.headers["x-request-id"]
    turn(ws)
    started = [r for r in caplog.records if r.getMessage().startswith("chat turn started")]
    assert started and f"http_request={http_id}" in started[-1].getMessage()
    turn_id = started[-1].request_id
    assert turn_id != http_id
    llm_lines = [r for r in caplog.records if r.getMessage() == "llm call fake"]
    rpc_lines = _access(caplog, "CatalogService/ListProducts")
    assert llm_lines and all(r.request_id == turn_id for r in llm_lines)
    assert any(r.request_id == turn_id for r in rpc_lines)


def _raw_call(addr, metadata):
    with grpc.insecure_channel(addr) as ch:
        call = ch.unary_unary("/shopassist.v2.CatalogService/ListProducts", request_serializer=pb.ListProductsRequest.SerializeToString,
                              response_deserializer=pb.ListProductsResponse.FromString)
        try:
            call(pb.ListProductsRequest(), metadata=metadata, timeout=5)
        except grpc.RpcError as e:
            return e.code()


def test_denied_grpc_call_is_warning(catalog_running, caplog):
    assert _raw_call(catalog_running.addr, (("x-request-id", "rid-denied"),)) == grpc.StatusCode.UNAUTHENTICATED
    seen = _access(caplog, "CatalogService/ListProducts")
    assert seen and seen[-1].levelno == logging.WARNING and "UNAUTHENTICATED" in seen[-1].getMessage()
    assert seen[-1].request_id == "rid-denied"


@pytest.mark.parametrize("bad", ["a" * 300, "../x"])
def test_hostile_grpc_request_id_replaced(loop_thread, catalog_running, caplog, bad):
    async def call():
        async with authchan.aio_channel(catalog_running.addr) as ch:
            await pb_grpc.CatalogServiceStub(ch).ListProducts(pb.ListProductsRequest(), metadata=(("x-request-id", bad),))

    loop_thread.call(call())
    assert HEX12.fullmatch(_access(caplog, "CatalogService/ListProducts")[-1].request_id)


def test_unhandled_error_has_header_and_one_traceback(settings, caplog):
    from fastapi.testclient import TestClient

    from orchestrator.app import create_app

    app = create_app(settings)

    async def boom():
        raise RuntimeError("kaput")

    app.add_api_route("/api/boom", boom)
    app.router.routes.insert(0, app.router.routes.pop())  # ahead of the SPA catch-all

    response = TestClient(app, raise_server_exceptions=False).get("/api/boom", headers={"X-Request-Id": "rid-500"})
    assert response.status_code == 500 and response.headers["x-request-id"] == "rid-500"
    tracebacks = [r for r in caplog.records if r.exc_info]
    assert len(tracebacks) == 1 and tracebacks[0].request_id == "rid-500"
    access = [r for r in caplog.records if r.name == "orchestrator.request_log"]
    assert access and access[-1].levelno == logging.ERROR and "-> 500" in access[-1].getMessage()


def _recovery_with_orders(monkeypatch, caplog):
    from services.order import recovery

    orders = [{"order_id": "O-1", "saga_step": "X"}, {"order_id": "O-2", "saga_step": "X"}]

    async def unsettled(pool, min_age):
        return orders

    class NoSaga:
        def __init__(self, *a, **kw):
            pass

        async def resume(self):
            pass

    class NoRuns:
        def is_live(self, order_id):
            return False

    monkeypatch.setattr(recovery.db, "unsettled_older_than", unsettled)
    monkeypatch.setattr(recovery, "Saga", NoSaga)
    rec = recovery.Recovery(deps=type("D", (), {"pool": None})(), runs=NoRuns(), canceller=None, min_age_seconds=0, interval_seconds=1)

    def ids():
        return {r.getMessage().split()[2]: r.request_id for r in caplog.records if r.getMessage().startswith("recovered order")}

    return rec, ids


def test_recovery_passes_each_get_fresh_ids_and_leave_the_context_clean(monkeypatch, caplog):
    rec, ids = _recovery_with_orders(monkeypatch, caplog)

    async def two_passes():
        await rec.recover_now()
        first = dict(ids())
        caplog.clear()
        after_first = get_request_id()
        await rec.recover_now()
        return first, after_first, ids(), get_request_id()

    first, after_first, second, after_second = asyncio.run(two_passes())
    assert after_first == after_second == "-"  # nothing leaks out of a pass into the next one
    everything = [*first.values(), *second.values()]
    assert len(set(everything)) == 4 and all(HEX12.fullmatch(i) for i in everything)


def test_recovery_keeps_a_caller_set_id(monkeypatch, caplog):
    rec, ids = _recovery_with_orders(monkeypatch, caplog)

    async def direct():
        set_request_id("operator-1")
        await rec.recover_now()

    asyncio.run(direct())
    assert set(ids().values()) == {"operator-1"}
