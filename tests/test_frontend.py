"""The two-page frontend (Assistant at /, System at /system.html) served by the orchestrator, the services health
endpoint behind the System page, and static-analysis guards on the HTML, CSS and JS."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from orchestrator.app import create_app
from orchestrator.settings import Settings

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend"
JS_FILES = sorted((FRONTEND / "js").glob("*.js"))
HTML_FILES = sorted(FRONTEND.glob("*.html"))
CSS_FILES = sorted(FRONTEND.glob("*.css"))
SHARED_MODULES = ["dom.js", "api.js", "status.js", "shell.js"]


@pytest.fixture
def client():
    # No gRPC servers needed for these routes; channels connect lazily.
    with TestClient(create_app(Settings())) as c:
        yield c


@pytest.fixture
def live(grpc_services):
    settings = Settings(product_addr=grpc_services["product"], inventory_addr=grpc_services["inventory"],
                        order_addr=grpc_services["order"])
    with TestClient(create_app(settings)) as c:
        yield c


def collect(client, ws_url):
    events = []
    with client.websocket_connect(ws_url) as ws:
        while True:
            try:
                events.append(ws.receive_json())
            except WebSocketDisconnect:
                return events


# ---------------- both pages are served ----------------

def test_assistant_page_is_served_at_root(client):
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<title>Assistant" in r.text and "<h1>Assistant</h1>" in r.text
    assert 'type="module" src="js/assistant.js"' in r.text
    assert 'href="styles.css"' in r.text and 'href="assistant.css"' in r.text
    assert 'aria-live="polite"' in r.text  # live region for new answers
    assert 'id="composer"' in r.text and 'id="questions"' in r.text


def test_system_page_is_served(client):
    r = client.get("/system.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = client.get("/system.html").text
    assert "<title>System" in html and "<h1>System</h1>" in html
    assert 'type="module" src="js/system.js"' in html and 'href="system.css"' in html
    assert "No database" in html  # the diagram says plainly that the orchestrator owns none
    for block in ("Service health", "Groq key pool", "What happens to a question", "Where a question goes"):
        assert block in html


def test_shared_header_links_the_pages_and_marks_the_current_one(client):
    pages = {"/": "index.html", "/system.html": "system.html"}
    for path, current in pages.items():
        html = client.get(path).text
        nav = re.search(r'<nav class="nav".*?</nav>', html, re.S).group(0)
        assert 'href="index.html"' in nav and 'href="system.html"' in nav
        marked = re.findall(r'<a href="([^"]+)" aria-current="page">', nav)
        assert marked == [current], f"{path} should mark exactly {current}, got {marked}"
        assert 'class="brand"' in html and 'class="mark"' in html and "ShopAssist" in html  # the drawn mark and the name
        assert 'id="readout"' in html


def test_css_js_modules_and_fonts_are_served_with_usable_types(client):
    for css in ("styles.css", "assistant.css", "system.css"):
        r = client.get(f"/{css}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/css"), css
    assert "prefers-color-scheme: dark" in client.get("/styles.css").text
    for name in SHARED_MODULES + ["route.js", "render.js", "assistant.js", "system.js"]:
        r = client.get(f"/js/{name}")
        assert r.status_code == 200 and "javascript" in r.headers["content-type"], name
        assert r.content == (FRONTEND / "js" / name).read_bytes()
    for weight in (400, 500, 600):
        r = client.get(f"/fonts/plex-sans-{weight}.woff2")
        assert r.status_code == 200 and r.content[:4] == b"wOF2"
    assert "SIL Open Font License" in client.get("/fonts/OFL.txt").text


def test_unknown_static_path_is_404(client):
    assert client.get("/no-such-file.txt").status_code == 404


def test_both_pages_share_the_modules_and_every_import_resolves(client):
    imports = {}
    for path in JS_FILES:
        text = path.read_text(encoding="utf-8")
        imports[path.name] = re.findall(r'from "\./([\w.]+)"', text)
        for target in imports[path.name]:
            assert (FRONTEND / "js" / target).is_file(), f"{path.name} imports missing {target}"
            assert client.get(f"/js/{target}").status_code == 200
    for entry in ("assistant.js", "system.js"):
        assert "shell.js" in imports[entry], f"{entry} must mount the shared header readout"
    assert "status.js" in imports["shell.js"] and "status.js" in imports["system.js"]  # one poller feeds both pages
    assert "dom.js" in imports["assistant.js"] and "dom.js" in imports["system.js"]
    assert "api.js" in imports["status.js"]  # one place knows where the API lives


# ---------------- API routes still work with the mount in place ----------------

def test_api_routes_survive_the_static_mount(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/llm/status").json()["enabled"] is False
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/query/does-not-exist").status_code == 404  # the API's 404, not the static handler's
    assert client.post("/query", json={"text": "  "}).status_code == 422
    r = client.post("/query", json={"text": "What's the weather?"})
    assert r.status_code == 202
    body = r.json()
    assert body["ws_url"] == f"/ws/{body['query_id']}"
    events = collect(client, body["ws_url"])
    assert [e["type"] for e in events] == ["accepted", "intent_detected", "final_answer"]
    assert [e["type"] for e in collect(client, "/ws/nope")] == ["error"]


def test_cors_still_present_with_the_mount(client):
    pre = client.options("/query", headers={"Origin": "null", "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "*"
    assert client.get("/services/health", headers={"Origin": "null"}).headers["access-control-allow-origin"] == "*"


# ---------------- GET /services/health: the System page's live health, checked over gRPC ----------------

def test_services_health_all_up(live):
    r = live.get("/services/health")
    assert r.status_code == 200
    body = r.json()
    assert body["all_up"] is True and body["checked_at"].endswith(("Z", "+00:00"))
    assert [s["name"] for s in body["services"]] == ["product", "inventory", "order"]
    for s in body["services"]:
        assert s["status"] == "up" and s["error"] is None
        assert isinstance(s["latency_ms"], float) and 0 <= s["latency_ms"] < 1500  # inside the probe's deadline
        assert re.fullmatch(r"[\w.\-]+:\d+", s["address"])
    assert {(s["service"], s["method"]) for s in body["services"]} == {
        ("ProductService", "GetProduct"), ("InventoryService", "GetStock"), ("OrderService", "GetOrder")}
    assert ".db" not in r.text  # nothing here comes from a database; the orchestrator only makes gRPC calls


def test_services_health_reports_one_service_down(grpc_services):
    settings = Settings(product_addr=grpc_services["product"], inventory_addr="127.0.0.1:1",  # nothing listens on port 1
                        order_addr=grpc_services["order"])
    with TestClient(create_app(settings)) as c:
        r = c.get("/services/health")
        assert r.status_code == 200  # the endpoint reports; it does not fail when a service is down
        body = r.json()
        by_name = {s["name"]: s for s in body["services"]}
        assert body["all_up"] is False
        assert by_name["inventory"]["status"] == "down" and by_name["inventory"]["latency_ms"] is None
        assert by_name["inventory"]["error"] in ("UNAVAILABLE", "DEADLINE_EXCEEDED")
        assert by_name["product"]["status"] == "up" and by_name["order"]["status"] == "up"  # the others are unaffected


def test_services_health_all_down(client):
    # the default Settings point at ports nothing listens on in the test run
    s = Settings(product_addr="127.0.0.1:1", inventory_addr="127.0.0.1:1", order_addr="127.0.0.1:1")
    with TestClient(create_app(s)) as c:
        body = c.get("/services/health").json()
        assert body["all_up"] is False and all(x["status"] == "down" for x in body["services"])


def test_llm_status_reports_the_caps_the_system_page_draws_against(client):
    body = client.get("/llm/status").json()
    assert body["limits"] == {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000}
    s = Settings(llm_limit_rpm=5, llm_limit_tpd=1234)
    with TestClient(create_app(s)) as c:
        limits = c.get("/llm/status").json()["limits"]
        assert limits["rpm"] == 5 and limits["tpd"] == 1234


# ---------------- static analysis of the frontend ----------------

def test_js_never_builds_dom_from_html_strings():
    # Server data (answers, product names, entities, key labels) is untrusted: textContent / createElement only.
    assert JS_FILES, "no frontend JS found"
    for path in JS_FILES:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\.innerHTML\s*=\s*(?![\"']{2}\s*[;,)])", text), f"{path.name}: innerHTML assignment"
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "srcdoc", "DOMParser"):
            assert sink not in text, f"{path.name}: {sink} must not appear"


def test_frontend_has_no_external_resources():
    for path in HTML_FILES + CSS_FILES + JS_FILES:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".html":
            assert not re.search(r"<(script|link|img|iframe)\b[^>]*\b(src|href)=[\"']?(https?:)?//", text, re.I), path.name
        if path.suffix == ".css":
            assert "@import" not in text and not re.search(r"url\(\s*[\"']?(https?:)?//", text), path.name
        for host in ("fonts.googleapis", "fonts.gstatic", "cdnjs", "jsdelivr", "unpkg", "cdn.tailwindcss", "googletagmanager"):
            assert host not in text, f"{path.name}: {host}"
        if path.suffix == ".js":
            assert not re.search(r"fetch\(\s*[\"'`]https?://", text) and not re.search(r"new WebSocket\(\s*[\"'`]wss?://", text)
    # every @font-face points at a file that is bundled here
    for css in CSS_FILES:
        for url in re.findall(r'url\("([^"]+)"\)', css.read_text(encoding="utf-8")):
            if not url.startswith("data:"):
                assert (FRONTEND / url).is_file(), f"{css.name}: missing {url}"


def test_frontend_is_plain_files_no_node_toolchain():
    banned = {"package.json", "package-lock.json", "node_modules", "vite.config.js", "tsconfig.json", "tailwind.config.js", "yarn.lock", "pnpm-lock.yaml"}
    present = {p.name for p in FRONTEND.rglob("*")} & banned
    assert not present, f"build-tool files in frontend/: {present}"
    assert not list(FRONTEND.rglob("*.ts")) and not list(FRONTEND.rglob("*.tsx")) and not list(FRONTEND.rglob("*.mjs"))
    for path in HTML_FILES:
        scripts = re.findall(r"<script\b([^>]*)>", path.read_text(encoding="utf-8"))
        assert all('type="module"' in s and 'src="js/' in s for s in scripts), f"{path.name}: only local ES modules"


def test_css_obeys_the_house_rules():
    # No gradients, glows, glass, resting shadows, tracked or uppercase labels, emoji, or violet/indigo hues.
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
    for path in CSS_FILES + HTML_FILES + JS_FILES:
        text = path.read_text(encoding="utf-8")
        stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.S) if path.suffix == ".css" else text
        if path.suffix == ".css":
            assert "gradient" not in stripped, f"{path.name}: gradient"
            assert "backdrop-filter" not in stripped and "blur(" not in stripped, f"{path.name}: glass"
            assert "box-shadow" not in stripped, f"{path.name}: shadow"
            assert "text-shadow" not in stripped, f"{path.name}: glow"
            assert "text-transform: uppercase" not in stripped, f"{path.name}: uppercase label"
            assert not re.search(r"letter-spacing:\s*0?\.\d+em|letter-spacing:\s*[1-9]", stripped), f"{path.name}: tracked text"
        assert not emoji.search(text), f"{path.name}: emoji"
    palette = " ".join(p.read_text(encoding="utf-8") for p in CSS_FILES).lower()
    for violet in ("#4f46e5", "#6366f1", "#7c3aed", "#8b5cf6", "#a78bfa", "#6d28d9"):
        assert violet not in palette, f"violet/indigo {violet}"


def test_system_page_event_names_are_real():
    """The explanation lists the real event names; each must be one the orchestrator actually sends."""
    html = (FRONTEND / "system.html").read_text(encoding="utf-8")
    sent = "".join((REPO / "orchestrator" / f).read_text(encoding="utf-8") for f in ("app.py", "dispatch.py", "store.py"))
    named = re.findall(r"<code>(\w+)</code>", html)
    assert set(named) == {"accepted", "intent_detected", "service_call", "partial_result", "order_update", "final_answer", "error"}
    for name in named:
        assert f'"{name}"' in sent or f"'{name}'" in sent, f"{name} is not emitted by the orchestrator"


# ---------------- contract: the data shapes the renderers read exist in real final_answer events ----------------

def _final(client, text):
    r = client.post("/query", json={"text": text})
    events = collect(client, r.json()["ws_url"])
    assert events[-1]["type"] == "final_answer", events[-1]
    return events


def test_final_answer_shapes_match_what_the_renderers_read(live):
    data = _final(live, "Where is my order 1042?")[-1]["data"]["data"]
    order = data["order"]
    assert {"order_id", "customer_name", "status", "items", "total", "updated_at"} <= set(order)
    assert {"product_id", "name", "size", "quantity", "unit_price"} <= set(order["items"][0])

    events = _final(live, "Track order 1043 live")
    updates = [e["data"] for e in events if e["type"] == "order_update"]
    assert {"order_id", "status", "message", "timestamp"} <= set(updates[0])
    assert events[-1]["data"]["data"]["updates"] == updates and events[-1]["data"]["data"]["order_id"] == "1043"

    products = _final(live, "Show me headphones under $100")[-1]["data"]["data"]["products"]
    assert {"product_id", "name", "category", "color", "price", "description"} <= set(products[0])

    data = _final(live, "Do you have the blue hoodie in size M?")[-1]["data"]["data"]
    result = data["results"][0]
    assert {"product", "stock"} <= set(result) and {"product_id", "size", "quantity"} <= set(result["stock"][0])

    data = _final(live, "Tell me about P-1004")[-1]["data"]["data"]
    assert {"product", "stock"} <= set(data) and data["product"]["product_id"] == "P-1004"

    assert _final(live, "What's the weather?")[-1]["data"]["data"] == {}
    assert _final(live, "Where is my order 9999?")[-1]["data"]["data"] == {}  # the JS falls back to the answer text
    relaxed = _final(live, "Show me cans under $100 headphones")[-1]["data"]["data"]
    assert relaxed["relaxed"] is True
    # an out-of-stock size comes through with quantity 0 (not omitted), which the size tiles strike through
    levels = _final(live, "Is the blue hoodie in stock?")[-1]["data"]["data"]["results"][0]["stock"]
    assert (levels[2]["size"], levels[2]["quantity"]) == ("L", 0)


def test_route_events_carry_the_timestamps_the_route_measures_from(live):
    """The route draws '+N ms' per node from event timestamps, so every event must carry a parseable ts in order."""
    from datetime import datetime
    events = _final(live, "Where is my order 1042?")
    stamps = [datetime.fromisoformat(e["ts"]) for e in events]
    assert stamps == sorted(stamps)
    assert [e["type"] for e in events][:2] == ["accepted", "intent_detected"]
