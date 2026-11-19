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


---

# Phase 5 — ShopAssist v2: a working shop (supersedes the v1 contract above)

v1 was retrieval only. v2 adds a browsable catalog, a cart, a checkout that writes across
services as a saga, event-driven tracking, and an assistant that uses the services as tools.
The v1 `/query` + `/ws/{id}` API and the `frontend/` folder are removed when v2 replaces them.
gRPC contract: `protos/shopassist_proto/shopassist.proto` (package `shopassist.v2`) — read its header comment first.

## Architecture

```
Browser: web/ (Vite + React + TypeScript + React Router, single-page app)
  | REST /api/*   +   ONE WebSocket per signed-in tab: /api/ws
  v
Orchestrator (FastAPI :8011): gateway + agent. NO database driver, ever.
  | gRPC (+ metadata x-user-id)
  +-- CatalogService    :50051  db "catalog"    products, skus, full-text search
  +-- InventoryService  :50052  db "inventory"  stock per sku, reservations (TTL)
  +-- OrderService      :50053  db "orders"     orders, checkout saga, fulfilment worker, outbox
  |        +-- gRPC --> Catalog (prices), Inventory (reserve/commit/release/restock), Payment
  +-- CustomerService   :50054  db "customer"   demo users, carts
  +-- PaymentService    :50055  db "payment"    simulated charges/refunds
```

## Stack changes

- **PostgreSQL 17.12**, native binaries (no Docker, no Windows service) at `PG_BIN`, cluster at `PG_DATA`
  (both in `.env`), listening on `localhost:5432`. EDB installer downloads are blocked on this network; the
  binaries came from Maven Central (zonky, SHA1-verified). `scripts/pg.py start|stop|status` manages it.
- One server, **five databases** (`catalog, inventory, orders, customer, payment`), one login role per service
  that can connect ONLY to its own database (`REVOKE CONNECT ON DATABASE … FROM PUBLIC`, grant to its role).
- Driver: `psycopg[binary]` 3.3 + `psycopg-pool` (async; verified importable on Python 3.14). All services become
  **`grpc.aio` servers**. Windows: psycopg async needs a selector loop —
  `asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)`. Spike this with grpc.aio FIRST; if it fails,
  use the sync pool via `asyncio.to_thread` and report.
- Schema: numbered SQL files `services/<svc>/migrations/NNN_name.sql`, applied by `services/common/migrate.py`,
  recorded in `schema_migrations`.
- Health: every gRPC server registers `grpc.health.v1` (`grpcio-health-checking`).
- Frontend: Node 22, Vite, React 18, TypeScript strict, React Router 6. Plain CSS with the tokens from
  `frontend/styles.css` (taste-skill app-screen rules still apply). API types generated with `openapi-typescript`
  from `/openapi.json` — no hand-written API shapes.
- FastAPI serves `web/dist` at `/` with SPA fallback (non-`/api` unknown paths → `index.html`).

## Env vars (.env, never committed; `.env.example` lists names with empty secrets)

| Var | Default / meaning |
|---|---|
| `PG_ADMIN_URL` | superuser URL; used only by `scripts/db_setup.py` and the test fixture |
| `PG_BIN`, `PG_DATA` | Postgres binaries and cluster directory, used by `scripts/pg.py` |
| `CATALOG_DATABASE_URL`, `INVENTORY_DATABASE_URL`, `ORDER_DATABASE_URL`, `CUSTOMER_DATABASE_URL`, `PAYMENT_DATABASE_URL` | per-service role URLs, written to `.env` by `db_setup.py` with random passwords |
| `CATALOG_ADDR` … `PAYMENT_ADDR` | `localhost:50051` … `localhost:50055` |
| `ORCHESTRATOR_PORT` | `8011` |
| `SESSION_SECRET` | random, written by `db_setup.py` if missing; signs session tokens |
| `RESERVATION_TTL_SECONDS` | `600` |
| `FULFILMENT_STEP_SECONDS` | `8` (tests ~0.05) |
| `PAYMENT_LATENCY_MS` | `300` |
| `LLM_*` | unchanged from Phase 2 |

## Database rules

- Money: `bigint` cents. Timestamps: `timestamptz`. IDs: prefixed text (`P-`, `SKU-`, `U-`, `O-`, `PAY-`, `RF-`).
- Stock invariant: `on_hand - reserved >= 0` enforced by a `CHECK` constraint AND by the atomic update
  `UPDATE stock SET reserved = reserved + $q WHERE sku_id = $s AND on_hand - reserved >= $q`.
  Reserve is all-or-nothing in one transaction; touch rows in `sku_id` order to avoid deadlocks.
- Idempotency: every write table has a `UNIQUE` idempotency key; a repeat returns the stored result.
- Outbox (orders): `order_events(event_id bigserial, order_id, user_id, status, message, created_at)` written in the
  SAME transaction as the status change, followed by `NOTIFY order_events, '<event_id>'`.
  `WatchOrders` = replay `event_id > after_event_id`, then `LISTEN` for new ones.
- Saga state persists on the order row (`saga_step`, `payment_id`; reservation_id = order_id). On startup and every
  30 s a recovery task finishes or compensates PENDING orders older than 60 s.
- Fulfilment worker: takes due orders with `FOR UPDATE SKIP LOCKED`; advances CONFIRMED→PACKED→SHIPPED→
  OUT_FOR_DELIVERY→DELIVERED one step per `FULFILMENT_STEP_SECONDS`, each step an outbox event.
  **Reading an order never writes.**

## Auth (demo, no passwords)

`POST /api/session {"user_id"}` → user must exist (`CustomerService.GetUser`) → `{"token","user"}`. Token =
`itsdangerous`-signed `{user_id}` with 12 h max age, key `SESSION_SECRET`. All `/api/*` except `/api/health*`,
`/api/users`, `/api/session`, `/api/products*` need `Authorization: Bearer <token>`; the WebSocket takes `?token=`.
The orchestrator forwards the user as gRPC metadata `x-user-id`; services enforce ownership.

## REST contract (orchestrator, all under /api, JSON)

Errors: HTTP status + `{"error": {"code", "message", "details"?}}`. Codes: `invalid` 422, `unauthenticated` 401,
`not_found` 404, `conflict` 409, `out_of_stock` 409, `payment_declined` 402, `unavailable` 503
(`details.service` names the gRPC service). Money fields are integer cents named `*_cents`; the client formats.

| Method + path | Body / query | Response |
|---|---|---|
| GET `/api/health` | | `{"status":"ok"}` |
| GET `/api/health/services` | | `{"all_up","checked_at","services":[{"name","service","address","status":"up"/"down","latency_ms","error"}]}` via grpc.health.v1 |
| GET `/api/llm/status` | | as Phase 2 |
| GET `/api/users` | | `{"users":[User]}` |
| POST `/api/session` | `{"user_id"}` | `{"token","user"}` |
| GET `/api/me` | | `{"user"}` |
| GET `/api/products` | `q, category*, brand*, color*, size*, min_price_cents, max_price_cents, sort` (`relevance, price_asc, price_desc, rating, newest`), `page_size, page_token`; `*` = repeatable | `{"products":[ProductCard],"total","next_page_token","facets":[{"field","values":[{"value","count"}]}],"price_min_cents","price_max_cents"}`; ProductCard = Product without `skus` plus `"in_stock"` (one batched GetStock per page) |
| GET `/api/products/{id}` | | `{"product": Product, "stock":[{"sku_id","size","color","available"}]}` |
| GET `/api/cart` | | `{"lines":[{"sku_id","product_id","name","size","color","quantity","unit_price_cents","line_total_cents","available"}],"subtotal_cents","shipping_cents","total_cents","version"}` |
| PUT `/api/cart/lines/{sku_id}` | `{"quantity"}` (0 removes, max 10) | cart |
| DELETE `/api/cart/lines/{sku_id}` | | cart |
| POST `/api/checkout` | `{"idempotency_key","payment_method"}` (lines come from the cart) | 202 `{"order_id"}`; progress on WS channel `checkout`; on success the cart is cleared with `if_version` |
| GET `/api/orders` | `page_size, page_token` | `{"orders":[Order without history],"next_page_token"}` |
| GET `/api/orders/{id}` | | `{"order": Order}` |
| POST `/api/orders/{id}/cancel` | `{"idempotency_key"}` | `{"order"}` |
| POST `/api/chat` | `{"message","context":{"path","product_id"?}}` | 202 `{"turn_id"}`; progress on WS channel `chat` |
| POST `/api/chat/proposals/{proposal_id}/confirm` | | 202 `{"ok":true}`; runs the proposed write as the user; result on WS |
| POST `/api/chat/proposals/{proposal_id}/dismiss` | | `{"ok":true}` |

## WebSocket `/api/ws?token=…&after=<seq>`

One socket per signed-in tab, server → client. Envelope: `{"seq","channel","type","data","ts"}` with channel
`chat | checkout | order | system`. `seq` is per user and strictly increasing. The orchestrator keeps the last 500
events per user in memory (dropped 1 h after that user's last socket closes); `after=N` replays `seq > N` first;
if N is older than the buffer it sends `system/resync` and the client refetches. While a user has a socket open the
orchestrator holds one `OrderService.WatchOrders` stream for them.

| channel | types → data |
|---|---|
| `checkout` | `step {order_id, step, state, message, shortages?}` · `done {order}` · `failed {order_id, reason, shortages?}` · `interrupted {order_id, message}` (the stream broke; the outcome is unknown and will be settled by recovery — the order's final status arrives later on channel `order`; never claim success or failure on `interrupted`) |
| `order` | `status {order_id, status, message, event_id}` |
| `chat` | `turn_started {turn_id}` · `tool_call {turn_id, tool, args}` · `tool_result {turn_id, tool, summary}` · `message {turn_id, text, cards:[{kind, data}]}` (kind: products, product, stock, cart, order, orders) · `proposal {turn_id, proposal_id, action, args, summary}` · `turn_done {turn_id, source:"llm"/"rules"}` · `error {turn_id, message}` |
| `system` | `hello {user, seq}` · `resync` |

## Agent (5.4)

Per turn, at most 4 steps; the model returns strict JSON `{"action": "<tool>" | "reply", "args": {…}, "reply": "…"}`.
Tools: `search_products, get_product, get_stock, get_cart, add_to_cart, remove_from_cart, list_orders, get_order,
propose_checkout, propose_cancel_order`. Cart edits run directly (reversible and visible). **Money / irreversible
actions are proposals only — the user's click confirms; the LLM cannot execute them.** Memory per user: last 6 turns
plus context (`path`, `product_id`, cart summary). No key / limits / errors → rules parser (`source:"rules"`).

Tool signatures (the contract shared by the agent, the rules fallback and the eval set; money in cents):

| Tool | Args | Kind |
|---|---|---|
| `search_products` | `query?, category?, color?, size?, brand?, min_price_cents?, max_price_cents?, sort?` (`relevance, price_asc, price_desc, rating, newest`) | read |
| `get_product` | `product_id` | read |
| `get_stock` | `product_id, size?, color?` | read |
| `get_cart` | — | read |
| `add_to_cart` | `product_id, size, color, quantity=1` (the server resolves the SKU; a missing size/colour on a multi-variant product → the agent asks) | write (direct) |
| `remove_from_cart` | `product_id, size?, color?` | write (direct) |
| `list_orders` | — | read |
| `get_order` | `order_id` | read |
| `propose_checkout` | `payment_method?` | proposal |
| `propose_cancel_order` | `order_id` | proposal |
| `reply` | — (answer or ask a clarifying question with no tool) | — |

Eval set `tests/eval/queries.jsonl`, one JSON object per line:
`{"id", "query", "history":[{"user","assistant"}], "context":{"path","product_id"?, "signed_in": bool}, "expect":{"tool", "args":{…}}, "tags":[…], "reason"}`.
`expect.tool` is the FIRST action the agent should take; `expect.args` lists only the args that must match (subset, case-insensitive;
money exact). Tags from: `basic, slang, typo, context, follow_up, multi_constraint, out_of_scope, ambiguous, order, cart, write, proposal, safety`.

## Phases and acceptance (Opus verifies each one live before committing)

- **5.1 Data + catalog + shop shell.** ~400 products, 10 categories, size×colour SKUs; Postgres setup; Catalog,
  Inventory and Customer (users) services; `/api/users, /api/session, /api/me, /api/products*, /api/health*`;
  SPA with sign-in picker, catalog (search, facets with counts, sort, pagination, URL-synced filters) and product
  page (variant picker, live stock).
- **5.2 Cart + checkout saga.** Carts, Payment, reservations, Order.Checkout with compensation and recovery;
  session WebSocket `/api/ws` with replay (channels `checkout` and `system`); cart drawer; checkout page with live saga steps. Tests: 20 concurrent checkouts for 1 unit → exactly 1 success
  and stock never negative; decline → reservation released; repeated idempotency key → one charge;
  crash mid-saga → compensated by recovery.
- **5.3 Events + tracking.** Outbox NOTIFY, fulfilment worker, WatchOrders, cancel; WS channel `order`;
  orders list and live order page. Two viewers → each step happens once; refresh → no gaps, no duplicates.
- **5.4 Agent.** Tools, memory, proposals; chat drawer on every page; eval set (~120 labelled queries) + scorer.
- **5.5 Proof.** Locust load-test numbers, README, docs; old `frontend/`, v1 API and v1 tests removed.

## Ownership (v2)

| Path | Owner |
|---|---|
| `protos/**`, `docs/PLAN.md` | Opus |
| `services/**`, `orchestrator/**`, `tests/**` (except `tests/eval/queries.jsonl`) | Sonnet-A (backend) |
| `web/**` | Sonnet-B (frontend) |
| `scripts/gen_data.py`, `scripts/validate_data.py`, `scripts/db_setup.py`, `scripts/pg.py`, `scripts/run_all.py`, `data/seed/**`, `tests/eval/queries.jsonl`, `loadtest/**`, `README.md`, `requirements.txt`, `.env.example`, `.gitignore` | Haiku |

Nobody commits or pushes except Opus. Reports must paste the commands run and their real output.

## Status (2026-11-19) and next steps

Done and verified live: 5.1 (catalog, search with typo fallback, product pages), 5.2 (carts, checkout saga with
compensation, recovery that never starts a charge, session WebSocket), 5.3 (outbox + NOTIFY, fulfilment worker,
WatchOrders with overlap resume, cancel, orders pages), the System page, and the eval set (124 labelled queries,
validated, not yet scored). The project was stopped here by the user's decision; nothing below is started.

Next steps:
1. 5.4 DONE (2026-11-19, MVP): agent with 10 tools, per-conversation memory, page context, proposals, chat drawer;
   eval 117/124 (LLM mode, 50/124 answered by the model, the rest rate-limited to rules), 121/124 rules (rules were
   written after seeing the set, so that number is optimistic). Next: pace the LLM eval, more keys, move chat memory
   and the WS event log out of process.
2. 5.5: Locust load test with recorded numbers; delete `frontend/`, the v1 API leftovers, `client/` and v1 seed files.
3. Known gaps: no real auth (demo "sign in as"); the per-user WebSocket event log is in memory, so the
   orchestrator is still a single process; the test suite takes 2–8 minutes depending on machine load.

## 5.4 resumed (2026-11-19) — scorer interface

The agent module exposes one pure-ish entry point used by both the chat loop and the eval scorer:

```python
# orchestrator/agent/planner.py
async def plan_next_action(query: str, history: list[dict], context: dict, *, llm=None, tool_results: list[dict] = ()) -> Action
# Action = {"tool": str, "args": dict, "reply": str | None, "source": "llm" | "rules"}
# llm=None -> rules parser. No gRPC calls inside: context may carry a `product` summary
# {product_id, name, category, price_cents, colors, sizes} and a `cart` summary, filled by the caller.
```

Scorer `scripts/eval_agent.py --mode rules|llm [--limit N]`: for each line of `tests/eval/queries.jsonl` calls
`plan_next_action` with no tool results and compares the FIRST action: tool must match exactly; `expect.args` must be a
subset of the actual args — strings case-insensitive, `query` by token containment (expected tokens ⊆ actual tokens,
lower-cased, simple plural strip), integers exact. Prints overall accuracy, accuracy per tag and per tool, and the
failures; writes `tests/eval/results/<mode>-<date>.json`.
