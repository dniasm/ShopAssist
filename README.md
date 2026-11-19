# ShopAssist

ShopAssist is a small online shop: browse and search a catalog, keep a cart, check out, and follow your
orders live. Each service owns its own Postgres database. Everything runs locally on Windows.

## ShopAssist v2 (current)

### Architecture

- The browser app (`web/`, React + TypeScript) calls REST under `/api` and holds one WebSocket per signed-in tab (`/api/ws`).
- The orchestrator (FastAPI, port 8011) is the gateway and the shopping agent. It has no database driver; it only calls gRPC.
- Five gRPC services, each with its own Postgres database and login role: catalog (50051), inventory (50052), order (50053), customer (50054), payment (50055).
- Checkout is a saga: reserve stock, charge, confirm. A failure compensates the steps already done, and a recovery task finishes or compensates orders left pending.
- Order status changes go to an outbox table in the same transaction as the change, then `NOTIFY` announces them. `WatchOrders` replays missed events and then listens.
- Postgres 17 runs from native binaries (no Docker): one server, five databases.

### Prerequisites

- **Python 3.14** virtual environment in `.venv`, then `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
- **Node 22** (`node --version` shows v22.x) for `web/`.
- **PostgreSQL 17 binaries.** Copy `.env.example` to `.env` and set `PG_BIN` (the folder with `pg_ctl.exe`), `PG_DATA` (the cluster folder) and `PG_ADMIN_URL` (a superuser URL). Never commit `.env`.

### Run it

```powershell
.venv\Scripts\python.exe scripts/pg.py start          # starts Postgres on localhost:5432
.venv\Scripts\python.exe scripts/db_setup.py          # first time only: creates the 5 databases and roles, writes the URLs to .env
.venv\Scripts\python.exe scripts/run_all.py           # starts the services and the orchestrator; Ctrl+C stops them all
```

- `run_all.py --reset` drops the five databases first, so the services re-seed on start.
- `run_all.py --web` also starts the Vite dev server on port 5173.
- `run_all.py` refuses to start if a port it needs is busy. It never kills other processes.

### Sign-in

Accounts sign in with email and password (**Sign in** at the top right, or **Create an account** there).

- **Demo accounts.** The six seeded customers share the password `DEMO_PASSWORD` (default `shopassist-demo`), for
  example `priya.raman@example.com`. The sign-in dialog has a "Use the demo account" button. Set `DEMO_PASSWORD=` (empty)
  to turn demo logins off; accounts that already have a password keep it.
- **New accounts** are open by default. Set `SIGNUP_CODE` to require an invite code.
- **How it is stored.** The Customer service keeps salted PBKDF2-SHA256 hashes (600,000 rounds), never passwords. The
  orchestrator throttles wrong passwords (5 per email per address, 20 per address, per 15 minutes) and issues the signed
  session token the rest of the API and the WebSocket use. The rules and code come from the day7 project's accounts.

Web app:

```powershell
cd web
npm install
npm run dev            # dev server at http://localhost:5173
npm run build          # writes web\dist; the orchestrator serves it at http://127.0.0.1:8011 while run_all.py runs
```

Data (the seed in `data/seed/v2/` is already in the repo):

```powershell
.venv\Scripts\python.exe scripts/gen_data.py          # regenerates the seed; byte-identical on every run
.venv\Scripts\python.exe scripts/validate_data.py     # checks the seed and prints a summary
```

Tests (needs Postgres running; the tests create and drop their own throwaway databases):

```powershell
.venv\Scripts\python.exe -m pytest
```

The agent eval set `tests/eval/queries.jsonl` is kept for a later phase; check it with `scripts/validate_eval.py`.

The first version (v1: SQLite, `POST /query`, `frontend/`, `client/`) is no longer in the tree; it is in git history (everything before the "v2 phase 5.5" commit).

### Reset the data

```powershell
.venv\Scripts\python.exe scripts/run_all.py --reset
```

`--reset` drops the five databases before the services start, so they are re-created and re-seeded from `data/seed/v2/`.
Use it after a load test or a live-tracking demo (orders keep moving to DELIVERED and stay there). The seed JSON is never changed.

### Load test

`loadtest/locustfile.py` simulates shoppers (browse, search, filter, product pages, cart, checkout, orders). See
`loadtest/README.md` for how to run it and `loadtest/RESULTS.md` for the recorded numbers. Chat is off by default
because it spends the LLM quota.

## LLM configuration (optional)

The shopping agent uses Groq's `openai/gpt-oss-120b` through its OpenAI-compatible API. It is optional: with no keys
(or when every key is rate limited) the agent uses a rule-based planner and the shop works fully. Set these in `.env`
(copy `.env.example` to start):

| Variable | Default | Meaning |
|---|---|---|
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | API base URL |
| `LLM_API_KEYS` | empty | Comma-separated keys. Empty means rules only. Legacy `LLM_API_KEY` is merged in. |
| `LLM_MODEL` | `openai/gpt-oss-120b` | Model name |
| `LLM_REASONING_EFFORT` | `low` | `low`, `medium` or `high` |
| `LLM_MAX_TOKENS` | `1024` | Maximum completion tokens per request |
| `LLM_TIMEOUT` | `10` | Total seconds for one call, across all key attempts |
| `LLM_LIMIT_RPM` / `RPD` / `TPM` / `TPD` | `30` / `1000` / `8000` / `200000` | Per-key limits (Groq developer-tier defaults); match them to your organisation's limits |

**Key rotation.** Before each request the app picks a key with room on all four limits (the one with the most room
left; equal keys take turns). A 429 cools that key until Groq's reset time and the request retries at once on the next
key; a 401/403 disables the key until restart; a timeout, 5xx or malformed reply is not blamed on the key (one retry on
another key); if no key can serve, the rule-based planner answers. Keys from the same Groq account share limits, so
extra capacity needs keys from different accounts. Keys are never printed or returned: logs and the status endpoint
call them `key#1`, `key#2`, and so on.

`GET /api/llm/status` shows `enabled`, `model`, `limits` and, per key, its masked form (`key#1 …abcd`), state (`ok`,
`cooling`, `disabled`), what is left in each window and when a cooling key returns. The web app's System page shows the same.

**Keep keys out of git.** Put keys only in `.env` (listed in `.gitignore`), never in `.env.example`.

## Other settings

All settings are environment variables; `.env.example` lists them with comments: the service addresses
(`CATALOG_ADDR` … `PAYMENT_ADDR`), `ORCHESTRATOR_PORT` (8011), `RESERVATION_TTL_SECONDS`, `FULFILMENT_STEP_SECONDS`
(seconds between order status steps), `PAYMENT_LATENCY_MS`, `SESSION_SECRET`, `DEMO_PASSWORD` and `SIGNUP_CODE`.

## Troubleshooting

- **A port is busy.** `run_all.py` refuses to start if a port it needs is taken and never kills other processes. Find the owner
  with `netstat -ano | findstr :8011` (or the port in the message), or change `ORCHESTRATOR_PORT` in `.env`.
- **Services cannot connect to Postgres.** Run `scripts/pg.py status`, then `scripts/pg.py start`.
- **`/api/health/services` shows a service down.** Look at that service's log in the `run_all.py` window; the System page
  shows the same table.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest        # needs Postgres running; takes 2-8 minutes
cd web; npm run typecheck; npx vitest run
```

No network and no LLM key are needed: HTTP to the LLM is mocked and each test file creates and drops its own throwaway
databases. The test files in `tests/`:

| File | What it checks |
|---|---|
| `test_common.py`, `test_isolation.py` | Shared service helpers (migrations, idempotency, seeding); the orchestrator has no database driver and only talks gRPC. |
| `test_catalog.py`, `test_catalog_query.py`, `test_inventory.py`, `test_customer.py`, `test_payment.py` | Each gRPC service through a real channel against a temporary database; catalog search, filters, facets and typo fallback. |
| `test_order_saga.py`, `test_order_recovery.py`, `test_order_events.py`, `test_order_fulfilment.py`, `test_order_migrations.py` | The checkout saga and its compensation (including concurrent checkouts for one unit), crash recovery, the outbox and `WatchOrders`, the fulfilment worker and cancel, the order migrations. |
| `test_api.py`, `test_api_shop.py`, `test_api_orders.py`, `test_events_hub.py` | The REST gateway end to end (auth, catalog, cart, checkout, orders, WebSocket replay and resync). |
| `test_agent_planner.py`, `test_agent_rules.py`, `test_agent_chat.py`, `test_eval_scorer.py` | The agent: LLM and rules planning, tools, proposals, the chat turn over WebSocket, and the eval scorer. |
| `test_llm.py`, `test_keypool.py` | The LLM client with mocked HTTP (key rotation, retries, budgets, no key in logs) and the key pool's limits and cooling. |
| `test_seed_v2.py`, `test_run_all.py` | The seed data checks and the process runner. |

Run one file with `.venv\Scripts\python.exe -m pytest tests/test_api.py`.
