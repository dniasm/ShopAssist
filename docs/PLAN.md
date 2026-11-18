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
  → zero or more `service_call` (`{service, method}`, proto names e.g. `OrderService`/`GetOrder`; none for `unknown`
  or when a required id is missing) → zero or more `partial_result`
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
Steps persist, so a repeated demo shows an already-delivered order; `python scripts/run_all.py --reset` recreates the DBs from seed.

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
httpx, python-dotenv, pytest (async code is driven with `asyncio.run`, so no pytest-asyncio). SQLite via stdlib `sqlite3`. No Docker.
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

## Phase 2 — Groq gpt-oss-120b with multi-key rotation

Provider facts (checked against console.groq.com docs on 2026-11-18):
- Base URL `https://api.groq.com/openai/v1`, model `openai/gpt-oss-120b`.
- Four per-key limits (Developer base values; the user's org may differ): **30 RPM, 1000 RPD, 8000 TPM, 200000 TPD**.
- Every response carries `x-ratelimit-remaining-requests` / `x-ratelimit-reset-requests` (that is **RPD**) and
  `x-ratelimit-remaining-tokens` / `x-ratelimit-reset-tokens` (that is **TPM**). Reset format is a Go-style duration,
  e.g. `2m59.56s`, `7.66s`, `1h2m3s`, `250ms`. A 429 adds `retry-after` (seconds). **RPM and TPD are not in headers.**
- gpt-oss supports `reasoning_effort` (`low|medium|high`), `include_reasoning` (bool), strict `json_schema`
  structured outputs (all props required, nullable via `["string","null"]`, `additionalProperties:false`; no streaming/tools).
  `reasoning_format` is NOT supported for gpt-oss and must not be sent.

Env vars (exact names):
| Var | Default |
|---|---|
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` |
| `LLM_API_KEYS` | empty; comma-separated keys. Legacy `LLM_API_KEY` (single) still accepted and merged. No keys → rules only. |
| `LLM_MODEL` | `openai/gpt-oss-120b` |
| `LLM_REASONING_EFFORT` | `low` |
| `LLM_MAX_TOKENS` | `1024` (sent as `max_completion_tokens`) |
| `LLM_TIMEOUT` | `10` — total wall-clock budget for one detection, across all key attempts |
| `LLM_LIMIT_RPM` / `LLM_LIMIT_RPD` / `LLM_LIMIT_TPM` / `LLM_LIMIT_TPD` | `30` / `1000` / `8000` / `200000` |

Key-pool behaviour:
- Before a call, a key is eligible only if it has headroom on **all four** limits for this request
  (estimated tokens = prompt chars/4 + `LLM_MAX_TOKENS`). Headroom comes from the latest headers where Groq gives
  them (RPD, TPM, with their reset times) and from local sliding counters for RPM (60 s) and TPD (24 h, fed by
  `usage.total_tokens`). Pick the eligible key with the most remaining headroom; ties → round-robin.
- 429 → cool the key until `retry-after` (or the relevant reset), immediately retry the same request on the next
  eligible key. 401/403 → disable that key for the process lifetime. Timeout/5xx/bad JSON → don't blame the key; at
  most one retry on another key if budget remains, else fall back to rules.
- No eligible key, or budget exhausted → `LLMError` → rules (`intent_detected.source = "rules"`).
- Keys are never logged, returned, or put in exceptions; refer to keys as `key#<index>` (+ last 4 chars at most).
- `GET /llm/status` → `{"enabled", "model", "limits":{"rpm","rpd","tpm","tpd"}, "keys":[{"key":"key#1 …abcd","state":"ok|cooling|disabled","rpm_left","rpd_left","tpm_left","tpd_left","cooling_until"}]}`.
  `limits` are the configured caps each key's `*_left` counts down from.

## Phase 3–4 — Frontend and service health

- The orchestrator serves `frontend/` (plain HTML/CSS/JS, no build step): Assistant at `/`, System at `/system.html`.
- `GET /services/health` → one short gRPC call per service (a lookup of an id that cannot exist; NOT_FOUND counts as up)
  → `{"all_up", "checked_at", "services":[{"name","service","method","address","status":"up|down","latency_ms","error"}]}`.
  No database access; the orchestrator still reaches data only through gRPC.
