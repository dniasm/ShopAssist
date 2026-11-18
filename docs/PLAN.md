# ShopAssist — plan and contract

Owner: Opus (planner/reviewer). Sonnet = core coder. Haiku = support/tooling.
Work cheat sheet for everyone: `docs/fable-cheatsheet/SKILL.md` (read it first).

## Goal

An onboarding project that demonstrates, in one coherent flow:
REST (client → orchestrator), WebSocket streaming (orchestrator → client),
gRPC (orchestrator → microservices), and microservices that each own a database.
**The orchestrator never touches a database** — it only talks gRPC.

A user asks a store assistant things like:

| Example query | Intent | Services used |
|---|---|---|
| "Where is my order 1042?" | `order_status` | Order (GetOrder), Product (GetProduct for item names) |
| "Track order 1043 live" | `track_order` | Order (TrackOrder, server-streaming → forwarded to WebSocket) |
| "Show me headphones under $100" | `product_search` | Product (SearchProducts) |
| "Do you have the blue hoodie in size M?" | `stock_check` | Product (SearchProducts) → Inventory (GetStock) |
| "Tell me about P-1003" | `product_info` | Product (GetProduct) + Inventory (GetStock) |
| "What's the weather?" | `unknown` | none — polite fallback listing what it can do |

## Architecture

```
Client --REST POST /query--> Orchestrator (FastAPI :8000) --gRPC--> ProductService   :50051 -> data/products.db
   ^                              |                         --gRPC--> InventoryService :50052 -> data/inventory.db
   +----WebSocket /ws/{query_id}--+                         --gRPC--> OrderService     :50053 -> data/orders.db
```

Ports/addresses come from env vars with these defaults:
`PRODUCT_ADDR=localhost:50051`, `INVENTORY_ADDR=localhost:50052`, `ORDER_ADDR=localhost:50053`, `ORCHESTRATOR_PORT=8000`.

## Repo layout and file ownership

Only edit files you own. Need a change in someone else's file → say so in your report.

| Path | Owner |
|---|---|
| `protos/**`, `scripts/gen_protos.py`, `docs/PLAN.md` | Opus (done; generated code lives in `shopassist_proto/`, do not hand-edit) |
| `orchestrator/**` (app, intent, llm client, grpc clients, query store, composer) | Sonnet |
| `services/product/**`, `services/inventory/**`, `services/order/**` | Sonnet |
| `services/common/**` (shared db/seed helpers if needed) | Sonnet |
| `tests/**` | Sonnet |
| `requirements.txt`, `.env.example`, `.gitignore` | Haiku |
| `data/seed/products.json`, `data/seed/inventory.json`, `data/seed/orders.json` | Haiku |
| `scripts/run_all.py` (starts the 3 services + orchestrator, Ctrl+C stops all) | Haiku |
| `client/cli.py` (REST + WebSocket demo client), `client/index.html` (minimal browser client) | Haiku |
| `README.md` | Haiku |

## REST + WebSocket contract (orchestrator)

- `GET /health` → `200 {"status":"ok"}`
- `POST /query` body `{"text": "<user query>"}` → `202 {"query_id": "<uuid4 hex>", "ws_url": "/ws/<query_id>"}`.
  Empty/whitespace text → `422`.
  Processing runs in the background after the response is sent.
- `GET /query/{query_id}` → `200 {"query_id","status","intent","entities","answer","data"}`
  where `status` ∈ `pending|running|streaming|done|error`. Unknown id → `404`.
- `WS /ws/{query_id}` → server sends JSON events, then closes normally:
  `{"type": <event>, "query_id": "...", "data": {...}, "ts": "<ISO-8601 UTC>"}`
  Event types, in order: `accepted` → `intent_detected` (`{intent, entities, source: "llm"|"rules"}`)
  → one or more `service_call` (`{service, method}`) → zero or more `partial_result`
  → zero or more `order_update` (track_order only, one per gRPC stream message)
  → exactly one terminal event: `final_answer` (`{answer, data}`) or `error` (`{message}`).
  Unknown query id → send one `error` event, then close.
- **Replay requirement:** the client opens the WebSocket *after* POST returns, so early
  events may already have happened. Each query keeps an ordered event log; a WebSocket
  that connects late first receives all past events, then live ones. Two sockets on
  the same query both get the full sequence.

## Orchestrator behaviour

1. **Intent + entities.** Entities: `order_id` (digits), `product_id` (`P-\d+`),
   `category`, `color`, `size` (S/M/L/XL), `max_price` (number), `keywords`.
   - If `LLM_API_KEY` is set: call an OpenAI-compatible Chat Completions endpoint
     (`LLM_BASE_URL`, `LLM_MODEL`) with `httpx`, ask for strict JSON
     `{"intent":..., "entities":{...}}`, validate it, timeout `LLM_TIMEOUT` (default 10 s).
   - If no key, a timeout, an HTTP error or invalid JSON: use the deterministic **rule-based**
     parser. The LLM path must never be required for tests to pass.
   - Report which path was used in `intent_detected.source`.
2. **Dispatch** per the table above using async gRPC stubs (`grpc.aio`).
   gRPC `NOT_FOUND` → a friendly answer ("I couldn't find order 9999"), not an `error` event.
   Service unreachable (`UNAVAILABLE`) → `error` event with a clear message.
3. **Compose the answer** with a deterministic template. (Optional, behind the same LLM
   config: LLM rephrasing, falling back to the template.)

## Order tracking simulation (Order service)

`TrackOrder` sends the current status first. For orders not yet DELIVERED/CANCELLED it then
advances the order one step at a time (PLACED→PACKED→SHIPPED→OUT_FOR_DELIVERY→DELIVERED),
**writing each step to orders.db**, sleeping `ORDER_SIM_STEP_SECONDS` (default 2; tests use ~0.05)
between steps, and ends the stream at DELIVERED. CANCELLED orders: one update, then end.
Unknown order → `NOT_FOUND`.

## Seed data contract (Haiku writes JSON, Sonnet's services load it)

Each service creates its SQLite DB at startup from its seed file if the DB is missing or empty.
DB paths: `data/products.db`, `data/inventory.db`, `data/orders.db` (overridable by env var
`PRODUCT_DB`, `INVENTORY_DB`, `ORDER_DB` — tests point these at temp files).

- `products.json`: list of `{"product_id","name","category","color","price","description"}`.
  ~15 products, categories at least: `headphones`, `hoodies`, `shoes`, `backpacks`.
  Must include a **blue hoodie** and at least 3 headphones under $100 and 1 over $100.
  IDs `P-1001`.. sequential.
- `inventory.json`: list of `{"product_id","size","quantity"}`. Hoodies/shoes use S/M/L/XL;
  others use `"ONE"`. Include at least one size with quantity 0. Every product appears.
- `orders.json`: list of `{"order_id","customer_name","status","items":[{"product_id","size","quantity","unit_price"}],"updated_at"}`.
  `status` is the enum name (e.g. `"SHIPPED"`). Order IDs `1040`–`1049`. Must include
  `1042` = SHIPPED, `1043` = PLACED, `1044` = DELIVERED, `1045` = CANCELLED.
  `unit_price` must equal the product's price. Total is computed, not stored.

## Stack

Python 3.14 (local), venv at `.venv`. FastAPI + uvicorn, `websockets`, grpcio/grpcio-tools,
httpx, python-dotenv, pytest + pytest-asyncio. SQLite via stdlib `sqlite3`. No Docker.
Windows + PowerShell/Git Bash: use `pathlib`, no POSIX-only calls.

## Definition of done (Opus will check every item)

1. `python scripts/gen_protos.py` then `python scripts/run_all.py` starts all 4 processes.
2. `python client/cli.py "Where is my order 1042?"` prints streamed events and a correct answer.
3. `python client/cli.py "Track order 1043 live"` shows several `order_update` events ending at DELIVERED.
4. `pytest` passes with no network and no LLM key: unit tests (rule parser, composer),
   service tests against temp DBs, and at least one end-to-end test (real gRPC servers on
   free ports + FastAPI TestClient REST + WebSocket).
5. `orchestrator/` contains no `sqlite3` import and no DB path (a test asserts this).
6. Each intent in the table above works end to end.
7. README explains the architecture, setup, run and the LLM env vars.
