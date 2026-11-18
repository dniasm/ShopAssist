"""End to end: real gRPC servers on free ports + FastAPI TestClient (REST + WebSocket)."""
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from orchestrator.app import create_app
from orchestrator.settings import Settings

TERMINAL = {"final_answer", "error"}


@pytest.fixture
def client(grpc_services):
    settings = Settings(
        product_addr=grpc_services["product"], inventory_addr=grpc_services["inventory"],
        order_addr=grpc_services["order"],
    )
    with TestClient(create_app(settings)) as c:  # `with` keeps one event loop alive so background tasks keep running
        yield c


def post(client, text):
    r = client.post("/query", json={"text": text})
    assert r.status_code == 202
    body = r.json()
    assert body["ws_url"] == f"/ws/{body['query_id']}"
    return body["query_id"], body["ws_url"]


def collect(client, ws_url):
    """Read events until the server closes the socket."""
    events = []
    with client.websocket_connect(ws_url) as ws:
        while True:
            try:
                events.append(ws.receive_json())
            except WebSocketDisconnect as e:
                assert e.code == 1000  # normal close
                return events


def wait_done(client, qid, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        body = client.get(f"/query/{qid}").json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.02)
    raise AssertionError("query did not finish")


def types(events):
    return [e["type"] for e in events]


def check_envelope(events, qid):
    for e in events:
        assert e["query_id"] == qid and isinstance(e["data"], dict) and e["ts"].endswith(("Z", "+00:00"))


def check_order(events):
    """accepted -> intent_detected -> service_call first -> partial_result/order_update -> exactly one terminal last."""
    t = types(events)
    assert t[:2] == ["accepted", "intent_detected"]
    assert t[-1] in TERMINAL and sum(x in TERMINAL for x in t) == 1
    middle = t[2:-1]
    assert set(middle) <= {"service_call", "partial_result", "order_update"}
    assert not middle or middle[0] == "service_call"


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_cors_allows_file_origin(client):
    pre = client.options("/query", headers={"Origin": "null", "Access-Control-Request-Method": "POST",
                                            "Access-Control-Request-Headers": "content-type"})
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == "*"
    assert "POST" in pre.headers["access-control-allow-methods"]
    assert "access-control-allow-credentials" not in pre.headers
    r = client.post("/query", json={"text": "hello"}, headers={"Origin": "null"})
    assert r.status_code == 202 and r.headers["access-control-allow-origin"] == "*"
    assert client.get("/health", headers={"Origin": "null"}).headers["access-control-allow-origin"] == "*"


def test_blank_query_rejected(client):
    assert client.post("/query", json={"text": "   "}).status_code == 422
    assert client.post("/query", json={}).status_code == 422


def test_unknown_query_id(client):
    assert client.get("/query/nope").status_code == 404
    events = collect(client, "/ws/nope")
    assert types(events) == ["error"] and "Unknown" in events[0]["data"]["message"]


def test_order_status_end_to_end_with_late_subscriber(client):
    qid, ws_url = post(client, "Where is my order 1042?")
    wait_done(client, qid)  # everything already happened before the socket opens -> must be replayed
    events = collect(client, ws_url)
    check_envelope(events, qid)
    check_order(events)
    assert types(events) == ["accepted", "intent_detected", "service_call", "partial_result",
                             "service_call", "service_call", "final_answer"]
    assert events[1]["data"] == {"intent": "order_status", "entities": {"order_id": "1042"}, "source": "rules"}
    assert [(e["data"]["service"], e["data"]["method"]) for e in events if e["type"] == "service_call"] == [
        ("OrderService", "GetOrder"), ("ProductService", "GetProduct"), ("ProductService", "GetProduct")]
    final = events[-1]["data"]
    assert "Order 1042" in final["answer"] and "Classic Hoodie" in final["answer"] and "$169.50" in final["answer"]
    assert final["data"]["order"]["status"] == "SHIPPED"
    # GET /query reflects the same result
    body = client.get(f"/query/{qid}").json()
    assert body["status"] == "done" and body["intent"] == "order_status" and body["answer"] == final["answer"]


def test_two_sockets_get_the_full_sequence(client):
    qid, ws_url = post(client, "Tell me about P-1004")
    first = collect(client, ws_url)
    second = collect(client, ws_url)
    assert types(first) == types(second) and first == second
    assert types(first)[-1] == "final_answer"


def test_track_order_streams_live_updates_to_delivered(client):
    qid, ws_url = post(client, "Track order 1043 live")
    events = collect(client, ws_url)  # connects while the 0.05s-per-step simulation is still running
    check_envelope(events, qid)
    t = types(events)
    assert t[:3] == ["accepted", "intent_detected", "service_call"] and t[-1] == "final_answer"
    assert t[3:-1] == ["order_update"] * 5
    assert [e["data"]["status"] for e in events if e["type"] == "order_update"] == [
        "PLACED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"]
    assert "delivered" in events[-1]["data"]["answer"]
    assert client.get(f"/query/{qid}").json()["status"] == "done"
    # the simulation was persisted by the Order service: asking again shows DELIVERED
    qid2, _ = post(client, "Where is my order 1043?")
    assert "delivered" in wait_done(client, qid2)["answer"]


def test_track_order_replay_after_finish(client):
    qid, ws_url = post(client, "Track order 1043 live")
    wait_done(client, qid)
    assert types(collect(client, ws_url)).count("order_update") == 5


def test_product_search(client):
    qid, ws_url = post(client, "Show me headphones under $100")
    events = collect(client, ws_url)
    check_order(events)
    assert types(events) == ["accepted", "intent_detected", "service_call", "partial_result", "final_answer"]
    names = [p["name"] for p in events[-1]["data"]["data"]["products"]]
    assert names == ["Budget Earbuds", "Wireless Headphones"]  # the $199 pair is excluded


def test_slang_keyword_is_relaxed_end_to_end(client):
    events = collect(client, post(client, "Show me cans under $100 headphones")[1])
    check_order(events)
    assert [(e["data"]["service"], e["data"]["method"]) for e in events if e["type"] == "service_call"] == [
        ("ProductService", "SearchProducts"), ("ProductService", "SearchProducts")]
    final = events[-1]["data"]
    assert final["data"]["relaxed"] is True
    assert [p["name"] for p in final["data"]["products"]] == ["Budget Earbuds", "Wireless Headphones"]
    assert final["answer"].startswith("No exact match for 'cans', but here are headphones up to $100.00:")


def test_stock_check_chains_product_then_inventory(client):
    qid, ws_url = post(client, "Do you have the blue hoodie in size M?")
    events = collect(client, ws_url)
    check_order(events)
    assert [(e["data"]["service"], e["data"]["method"]) for e in events if e["type"] == "service_call"] == [
        ("ProductService", "SearchProducts"), ("InventoryService", "GetStock")]
    assert "Classic Hoodie" in events[-1]["data"]["answer"] and "size M: 4 in stock" in events[-1]["data"]["answer"]
    out = collect(client, post(client, "Is the blue hoodie available in size L?")[1])
    assert "out of stock" in out[-1]["data"]["answer"]


def test_product_info(client):
    events = collect(client, post(client, "Tell me about P-1001")[1])
    check_order(events)
    assert [e["data"]["method"] for e in events if e["type"] == "service_call"] == ["GetProduct", "GetStock"]
    assert "Wireless Headphones" in events[-1]["data"]["answer"] and "7" in events[-1]["data"]["answer"]


def test_not_found_is_a_friendly_answer_not_an_error(client):
    for text, needle in [("Where is my order 9999?", "couldn't find order 9999"),
                         ("Track order 9999 live", "couldn't find order 9999"),
                         ("Tell me about P-9999", "couldn't find product P-9999")]:
        events = collect(client, post(client, text)[1])
        assert types(events)[-1] == "final_answer", text
        assert needle in events[-1]["data"]["answer"]


def test_unknown_intent(client):
    events = collect(client, post(client, "What's the weather?")[1])
    assert types(events) == ["accepted", "intent_detected", "final_answer"]  # no service is called
    assert events[1]["data"]["intent"] == "unknown"
    assert "I can help with" in events[-1]["data"]["answer"]


def test_unreachable_service_yields_error_event(grpc_services):
    settings = Settings(product_addr="127.0.0.1:1", inventory_addr=grpc_services["inventory"],
                        order_addr=grpc_services["order"])
    with TestClient(create_app(settings)) as c:
        qid, ws_url = post(c, "Show me headphones")
        events = collect(c, ws_url)
        assert types(events)[-1] == "error" and "product service is not reachable" in events[-1]["data"]["message"]
        assert c.get(f"/query/{qid}").json()["status"] == "error"
