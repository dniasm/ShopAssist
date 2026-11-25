"""Orchestrator REST endpoints through FastAPI's TestClient, talking to REAL gRPC services (catalog, inventory,
customer) on free ports backed by throwaway databases. No mocks: the orchestrator has no database of its own."""
import json
import socket
import time

import pytest
from tests.conftest import AppClient

from orchestrator.app import create_app
from orchestrator.settings import Settings
from tests.fixtures import DEMO_PASSWORD, EMAIL_OF, INVENTORY, PRODUCTS, SKUS, USERS


@pytest.fixture(scope="module")
def settings(catalog_running, inventory_ro_running, customer_running, order_ro_running, payment_ro_running):
    return Settings(
        catalog_addr=catalog_running.addr, inventory_addr=inventory_ro_running.addr, customer_addr=customer_running.addr,
        order_addr=order_ro_running.addr, payment_addr=payment_ro_running.addr, session_secret="test-secret", llm_api_keys=(),
    )


@pytest.fixture(scope="module")
def client(settings, tmp_path_factory):
    dist = tmp_path_factory.mktemp("dist")
    with AppClient(create_app(settings, web_dist=dist / "missing")) as c:
        yield c


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sign_in(client, user_id="U-01"):
    r = client.post("/api/session", json={"email": EMAIL_OF[user_id], "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def error(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error"} and body["error"]["code"] == code and body["error"]["message"]
    return body["error"]


# ---------------- health ----------------

def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_services_health_uses_grpc_health_for_each_service(client, settings):
    body = client.get("/api/health/services").json()
    # No assistant fixture exists yet (the service arrives later), so it is the one service reported down.
    assert body["all_up"] is False and body["checked_at"]
    assert [s["name"] for s in body["services"]] == ["catalog", "inventory", "order", "customer", "payment", "assistant"]
    for s in (s for s in body["services"] if s["name"] != "assistant"):
        assert s["status"] == "up" and s["error"] is None and s["latency_ms"] >= 0
        assert s["service"].startswith("shopassist.v2.") and ":" in s["address"]


def test_services_health_reports_a_dead_service_without_failing(settings):
    dead = Settings(**{**settings.__dict__, "inventory_addr": f"127.0.0.1:{free_port()}"})
    with AppClient(create_app(dead)) as c:
        r = c.get("/api/health/services")
    assert r.status_code == 200
    body = r.json()
    assert body["all_up"] is False
    assert {s["name"]: s["status"] for s in body["services"]} == {
        "catalog": "up", "inventory": "down", "order": "up", "customer": "up", "payment": "up", "assistant": "down"}
    assert next(s for s in body["services"] if s["name"] == "inventory")["error"]


def test_llm_status_without_keys(client):
    assert client.get("/api/llm/status").json() == {
        "enabled": False, "model": "openai/gpt-oss-120b", "keys": [],
        "limits": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000},
    }


# ---------------- users, session, me ----------------

def test_the_customer_list_is_not_public_any_more(client):
    error(client.get("/api/users"), 404, "not_found")  # with passwords, listing every account would help guessers


def test_signing_in_with_email_and_password_returns_a_token_that_me_accepts(client):
    r = client.post("/api/session", json={"email": "GRACE@example.test", "password": DEMO_PASSWORD})  # email: any case
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["user_id"] == "U-02" and body["token"]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.json() == {"user": body["user"]}


def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(client):
    wrong = client.post("/api/session", json={"email": EMAIL_OF["U-03"], "password": "not-the-password"})
    unknown = client.post("/api/session", json={"email": "nobody@example.test", "password": DEMO_PASSWORD})
    error(wrong, 401, "unauthenticated")
    error(unknown, 401, "unauthenticated")
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"] == "Wrong email or password."


def test_session_body_is_validated(client):
    error(client.post("/api/session", json={}), 422, "invalid")
    error(client.post("/api/session", json={"email": "ada@example.test", "password": ""}), 422, "invalid")
    error(client.post("/api/session", json={"user_id": "U-01"}), 422, "invalid")  # the old demo picker is gone


def test_me_requires_a_valid_token(client, settings):
    error(client.get("/api/me"), 401, "unauthenticated")
    error(client.get("/api/me", headers={"Authorization": "Bearer garbage"}), 401, "unauthenticated")
    error(client.get("/api/me", headers={"Authorization": "Basic abc"}), 401, "unauthenticated")
    good = sign_in(client)["Authorization"]
    error(client.get("/api/me", headers={"Authorization": good[:-3] + "xyz"}), 401, "unauthenticated")  # tampered
    other = Settings(**{**settings.__dict__, "session_secret": "another-secret"})
    with AppClient(create_app(other)) as c2:
        error(c2.get("/api/me", headers={"Authorization": good}), 401, "unauthenticated")  # signed with a different key


def test_expired_token_is_rejected(settings):
    short = Settings(**{**settings.__dict__, "session_max_age_seconds": -1})
    with AppClient(create_app(short)) as c:
        headers = sign_in(c)
        error(c.get("/api/me", headers=headers), 401, "unauthenticated")


# ---------------- products ----------------

def test_products_search_with_filters_returns_cards_with_stock_flag(client):
    r = client.get("/api/products", params={"q": "hoodie", "size": "M"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0 and body["products"]
    card = body["products"][0]
    assert "skus" not in card and isinstance(card["in_stock"], bool) and isinstance(card["price_cents"], int)
    assert {"product_id", "name", "category", "brand", "rating", "colors", "sizes", "created_at"} <= set(card)
    assert all(p["category"] == "hoodies" and "M" in p["sizes"] for p in body["products"])
    assert {f["field"] for f in body["facets"]} == {"category", "brand", "color", "size"}
    assert body["price_min_cents"] <= body["price_max_cents"]


def test_in_stock_matches_inventory_for_every_product(client):
    expected = {
        p["product_id"]: any(i["on_hand"] > 0 for i in INVENTORY if i["product_id"] == p["product_id"]) for p in PRODUCTS
    }
    assert True in expected.values() and False in expected.values()  # the fixture exercises both
    got, token = {}, None
    while True:
        r = client.get("/api/products", params={"page_size": 10, **({"page_token": token} if token else {})}).json()
        got.update({p["product_id"]: p["in_stock"] for p in r["products"]})
        token = r["next_page_token"] or None
        if not token:
            break
    assert got == expected


def test_a_products_page_costs_exactly_one_batched_get_stock(settings):
    with AppClient(create_app(settings)) as c:
        stub = c.app.state.clients.inventory
        calls = []
        original = stub.GetStock

        def counting(request, **kw):
            calls.append(request)
            return original(request, **kw)

        stub.GetStock = counting
        assert len(c.get("/api/products", params={"page_size": 20}).json()["products"]) == 20
    assert len(calls) == 1 and len(calls[0].product_ids) == 20


def test_repeated_filter_params_are_or_within_a_field(client):
    r = client.get("/api/products", params=[("category", "hoodies"), ("category", "shoes"), ("page_size", 100)]).json()
    assert {p["category"] for p in r["products"]} == {"hoodies", "shoes"}


def test_sort_and_price_filters(client):
    r = client.get("/api/products", params={"sort": "price_desc", "min_price_cents": 5000, "max_price_cents": 9000}).json()
    prices = [p["price_cents"] for p in r["products"]]
    assert prices and prices == sorted(prices, reverse=True) and all(5000 <= c <= 9000 for c in prices)


@pytest.mark.parametrize("params", [{"sort": "cheapest"}, {"min_price_cents": -1}, {"page_size": 0}, {"page_size": 101}])
def test_invalid_query_parameters_are_422_invalid(client, params):
    error(client.get("/api/products", params=params), 422, "invalid")


def test_bad_page_token_is_422_invalid(client):
    error(client.get("/api/products", params={"page_token": "nonsense!"}), 422, "invalid")


def test_product_detail_has_skus_and_per_sku_availability(client):
    pid = PRODUCTS[0]["product_id"]
    body = client.get(f"/api/products/{pid}").json()
    assert body["product"]["product_id"] == pid
    expected = {s["sku_id"]: i["on_hand"] for s in SKUS if s["product_id"] == pid for i in INVENTORY if i["sku_id"] == s["sku_id"]}
    assert {s["sku_id"]: s["available"] for s in body["stock"]} == expected
    assert all({"sku_id", "size", "color", "available"} == set(s) for s in body["stock"])
    assert len(body["product"]["skus"]) == len(expected)


def test_unknown_product_is_not_found(client):
    error(client.get("/api/products/P-0000"), 404, "not_found")


def test_unknown_api_path_is_a_json_not_found(client):
    error(client.get("/api/nope"), 404, "not_found")


def test_unavailable_service_gives_503_naming_the_service(settings):
    dead = Settings(**{**settings.__dict__, "catalog_addr": f"127.0.0.1:{free_port()}"})
    with AppClient(create_app(dead)) as c:
        started = time.monotonic()
        err = error(c.get("/api/products"), 503, "unavailable")
    assert err["details"]["service"] == "catalog"
    assert time.monotonic() - started < 5  # the 2 s deadline bounds the wait


def test_openapi_declares_typed_responses_for_the_frontend_generator(client):
    spec = client.get("/openapi.json").json()
    ok = spec["paths"]["/api/products"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok["$ref"].endswith("/ProductListResponse")
    for name in ("ProductCard", "Product", "Facet", "User", "ErrorResponse"):
        assert name in spec["components"]["schemas"], name
    assert "422" in spec["paths"]["/api/products"]["get"]["responses"]


# ---------------- web/dist ----------------

def test_missing_dist_gives_a_short_503_text(client):
    r = client.get("/")
    assert r.status_code == 503 and r.headers["content-type"].startswith("text/plain")
    assert "npm run build" in r.text


def test_spa_fallback_serves_index_for_client_side_routes_but_not_for_api(settings, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    with AppClient(create_app(settings, web_dist=tmp_path)) as c:
        assert c.get("/").text == "<html>app</html>"
        assert c.get("/products/P-1001?size=M").text == "<html>app</html>"
        assert c.get("/assets/app.js").text == "console.log(1)"
        error(c.get("/api/nope"), 404, "not_found")
        error(c.get("/api"), 404, "not_found")
        # no path may escape the dist folder
        (tmp_path.parent / "secret.txt").write_text("top secret")
        assert "top secret" not in c.get("/..%2fsecret.txt").text
        assert "top secret" not in c.get("/%2e%2e/secret.txt").text


def test_cors_allows_the_vite_dev_server_only(client):
    r = client.options("/api/products", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET",
                                                 "Access-Control-Request-Headers": "authorization"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    r = client.options("/api/products", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


# ---------------- forgiving search through the REST layer ----------------

def test_products_exposes_fuzzy_flag(client):
    exact = client.get("/api/products", params={"q": "hood"}).json()
    typo = client.get("/api/products", params={"q": "hodie"}).json()
    assert exact["fuzzy"] is False and typo["fuzzy"] is True
    assert typo["products"][0]["category"] == "hoodies"
    assert client.get("/api/products").json()["fuzzy"] is False


# ---------------- startup config warnings ----------------

def test_missing_session_secret_logs_a_warning(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.WARNING)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    Settings.from_env()
    assert any("SESSION_SECRET" in r.message and "restart" in r.message for r in caplog.records)
    caplog.clear()
    monkeypatch.setenv("SESSION_SECRET", "configured")
    Settings.from_env()
    assert not [r for r in caplog.records if "SESSION_SECRET" in r.message]


def test_create_app_without_a_secret_warns_too(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    create_app(Settings())  # session_secret defaults to empty
    assert any("SESSION_SECRET" in r.message for r in caplog.records)
