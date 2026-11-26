# ShopAssist

ShopAssist is a small online shop: browse and search a catalog, keep a cart, check out, and follow your
orders live. Each service owns its own Postgres database. Everything runs locally on Windows.

## ShopAssist v2 (current)

### Architecture

- The browser app (`web/`, React + TypeScript) calls REST under `/api` and holds one WebSocket per signed-in tab (`/api/ws`, authenticated by its first message).
- The orchestrator (FastAPI, port 8011) is the gateway and the shopping agent. It has no database driver; it only calls gRPC.
- Six gRPC services, each with its own Postgres database and login role: catalog (50051), inventory (50052), order (50053), customer (50054), payment (50055), assistant (50056: saved chats and the memory facts).
- Checkout is a saga: reserve stock, charge, confirm. A failure compensates the steps already done, and a recovery task finishes or compensates orders left pending.
- gRPC calls between processes are authenticated: each caller (orchestrator, order) signs every call with its own `SVC_KEY_*` secret (short-lived HMAC token in metadata). A service rejects missing/bad tokens (`UNAUTHENTICATED`) and callers it does not allow (`PERMISSION_DENIED`); only `grpc.health.v1` Check is open. `x-user-id` is therefore trusted only from an authenticated caller. Keys are written to `.env` by `scripts/db_setup.py`.
- Order status changes go to an outbox table in the same transaction as the change, then `NOTIFY` announces them. `WatchOrders` replays missed events and then listens.
- Postgres 17 runs from native binaries (no Docker): one server, six databases.

### Prerequisites

- **Python 3.14** virtual environment in `.venv`, then `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
- **Node 22** (`node --version` shows v22.x) for `web/`.
- **PostgreSQL 17 binaries.** Copy `.env.example` to `.env` and set `PG_BIN` (the folder with `pg_ctl.exe`), `PG_DATA` (the cluster folder) and `PG_ADMIN_URL` (a superuser URL). Never commit `.env`.

### Run it

```powershell
.venv\Scripts\python.exe scripts/pg.py start          # starts Postgres on localhost:5432
.venv\Scripts\python.exe scripts/db_setup.py          # first time only: creates the 6 databases and roles, writes the URLs and secrets to .env
.venv\Scripts\python.exe scripts/run_all.py           # starts the services and the orchestrator; Ctrl+C stops them all
```

- `run_all.py --reset` drops the six databases first, so the services re-seed on start.
- `run_all.py --web` also starts the Vite dev server on port 5173.
- `run_all.py` refuses to start if a port it needs is busy. It never kills other processes.

### Sign-in

Accounts sign in with email and password (**Sign in** at the top right, or **Create an account** there).

- **Demo accounts.** The six seeded customers share the password `DEMO_PASSWORD` (default `shopassist-demo`), for
  example `priya.raman@example.com`. In the sign-in dialog, "Use the demo account" fills them in one per click (Priya,
  then Tomas, then Hana, ... back to Priya). Set `DEMO_PASSWORD=` (empty)
  to turn demo logins off; accounts that already have a password keep it.
- **New accounts** are open by default. Set `SIGNUP_CODE` to require an invite code.
- **How it is stored.** The Customer service keeps salted PBKDF2-SHA256 hashes (600,000 rounds), never passwords. The
  orchestrator throttles wrong passwords (5 per email per address, 20 per address, per 15 minutes) and issues the signed
  session token the rest of the API and the WebSocket use. The rules and code come from the day7 project's accounts.
- **WebSocket auth.** `/api/ws?after=<n>` takes no credentials in the URL. The first message must be `{"type":"auth","token":...}` (or `{"type":"auth","guest":"<id>"}`) within 5 s; the server replies `system/auth_ok`, otherwise it closes with 4401. A browser `Origin` must be the orchestrator's own host or in `WS_ALLOWED_ORIGINS` (default: the Vite dev server).

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

The agent eval set `tests/eval/queries.jsonl` (129 cases) is checked with `scripts/validate_eval.py` and scored with `scripts/eval_agent.py --mode rules|llm` (see the assistant section below).

The first version (v1: SQLite, `POST /query`, `frontend/`, `client/`) is no longer in the tree; it is in git history (everything before the "v2 phase 5.5" commit).

### Run with Docker

Everything (Postgres 17, the six services, the orchestrator and the web app) runs in containers; only port 8011 is published.

```powershell
python scripts/docker_env.py        # once: random secrets into the gitignored .docker-env/ (never printed, never committed)
docker compose up --build           # first start builds the image and creates the databases
```

Open http://localhost:8011 and sign in as `priya.raman@example.com` / `shopassist-demo`. Add `LLM_API_KEYS=...` to
`.docker-env/orchestrator.env` for the optional LLM (without it the rule-based parser answers).

`docker compose down` stops everything and keeps the data (Postgres volume `pgdata`); `docker compose down -v` also deletes
it, so the next `up` re-creates and re-seeds the databases. After `docker_env.py --force` run `down -v` first (the volume keeps the old passwords).
On Windows run compose from PowerShell, not Git Bash (path-case problem with `docker compose build`).

### Reset the data

```powershell
.venv\Scripts\python.exe scripts/run_all.py --reset
```

`--reset` drops the six databases before the services start, so they are re-created and re-seeded from `data/seed/v2/`.
Use it after a load test or a live-tracking demo (orders keep moving to DELIVERED and stay there). The seed JSON is never changed.

### Load test

`loadtest/locustfile.py` simulates shoppers (browse, search, filter, product pages, cart, checkout, orders). See
`loadtest/README.md` for how to run it and `loadtest/RESULTS.md` for the recorded numbers. Chat is off by default
because it spends the LLM quota.

## LLM configuration (optional)

The shopping agent uses Groq's `openai/gpt-oss-120b` through its OpenAI-compatible API. It is optional: with no keys
(or when every key is rate limited) the agent falls back to a rule-based planner that answers read-only questions (it never changes the cart or orders) and the shop works fully. Set these in `.env`
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
another key); if no key can serve, the rule-based fallback answers. Keys from the same Groq account share limits, so
extra capacity needs keys from different accounts. Keys are never printed or returned: logs and the status endpoint
call them `key#1`, `key#2`, and so on.

`GET /api/llm/status` shows `enabled`, `model`, `limits` and, per key, its masked form (`key#1 …abcd`), state (`ok`,
`cooling`, `disabled`), what is left in each window and when a cooling key returns. The web app's System page shows the same.

**Keep keys out of git.** Put keys only in `.env` (listed in `.gitignore`), never in `.env.example`.

## Shopping assistant

The agent runs on native tool calling: each turn the model gets a short system prompt, 8 tool schemas
(`search`, `product`, `cart`, `orders`, `cancel_order`, `checkout`, `open_page`, `recall_facts`), a memory block and the
message, and may call tools for up to 6 steps. It never pays or cancels: `checkout` and `cancel_order` only create a
proposal that the person confirms with a button (a confirmed checkout opens `/checkout`).

- **Assistant service** (gRPC, port 50056, database `assistant`) stores conversations, messages and facts. Set `ASSISTANT_ADDR`
  (default `localhost:50056`) and `ASSISTANT_DATABASE_URL` (written by `db_setup.py`).
- **Chat history.** Signed-in people get saved chats: a list in the chat drawer (title = first question; rename, delete),
  the full transcript is loaded from the server, and a new chat starts clean. Guests keep only the current temporary chat.
- **Memory.** Facts about the person are `permanent` (name, sizes, height, budget, colour and brand tastes; a new value
  replaces the old one, "forget my size" deletes) or `temporal` (a current need such as "I need a new shirt"; expires after
  30 days or as soon as an order with a product of that category is placed). A cheap model (`MEMORY_MODEL`, default
  `openai/gpt-oss-20b`) extracts them in the background after each reply; it also keeps a rolling summary of at most
  ~200 words per chat. Each turn only the top 5 facts scoring at least 0.45 against the message are put in the prompt;
  `recall_facts` covers what that missed. Embeddings come from Voyage (`VOYAGE_API_KEY`, `VOYAGE_MODEL`, default
  `voyage-4-lite`); with no Voyage key facts are matched by keyword overlap.
- **Token budget.** Groq's free tier allows 8,000 tokens per minute per key, so a turn stays small: the system prompt is
  about 500 tokens, tool definitions about 600, facts about 150, summary about 270, the latest exchange about 250 and tool
  results about 600. A simple turn (one model call) is about 1,850 tokens, one that reasons about a result about 4,300,
  and the average about 3,000. Each turn logs its model calls and tokens.
- **Eval.** `scripts/eval_agent.py --mode llm` scores the first tool call of the loop (same messages the loop builds, one
  `chat` call per case); `--mode rules` scores the rule-based planner mapped to the same tools. The reported-bug cases
  (exact line removal, ambiguous removal asks, add with size and colour, names instead of ids, "the second one") are tagged
  `bug`. Results go to `tests/eval/results/`.

| Variable | Default | Meaning |
|---|---|---|
| `ASSISTANT_ADDR` | `localhost:50056` | Where the orchestrator finds the assistant service |
| `ASSISTANT_DATABASE_URL` | written by `db_setup.py` | The assistant service's database role |
| `VOYAGE_API_KEY` | empty | Voyage key for fact embeddings (secret; empty = keyword matching) |
| `VOYAGE_MODEL` | `voyage-4-lite` | Embedding model |
| `MEMORY_MODEL` | `openai/gpt-oss-20b` | Groq model that extracts facts and writes the chat summary |

## Other settings

All settings are environment variables; `.env.example` lists them with comments: the service addresses
(`CATALOG_ADDR` … `PAYMENT_ADDR`, `ASSISTANT_ADDR`), `ORCHESTRATOR_PORT` (8011), `RESERVATION_TTL_SECONDS`, `FULFILMENT_STEP_SECONDS`
(seconds between order status steps), `PAYMENT_LATENCY_MS`, `SESSION_SECRET`, `SVC_KEY_ORCHESTRATOR`/`SVC_KEY_ORDER`, `WS_AUTH_TIMEOUT_SECONDS`, `WS_ALLOWED_ORIGINS`, `DEMO_PASSWORD` and `SIGNUP_CODE`.

## Logs

Every service and the orchestrator write one line format to the console and to files: `logs/<service>/<YYYY-MM-DD>.log`
(all lines) and `logs/audit/<service>-<YYYY-MM-DD>.log` (business events and one `assistant_turn` line per chat turn).
Each request carries an id (`[request_id]`) across all services. Docker bind-mounts `./logs`. Secrets are masked.

| Setting | Default | |
|---|---|---|
| `LOG_DIR` | `<repo>/logs` | where the files go (`/app/logs` in Docker) |
| `LOG_LEVEL` | `INFO` | console and service files; audit lines are always written |
| `LOG_RETENTION_DAYS` | `30` | service files older than this are deleted; audit files are kept forever |

```powershell
python scripts/logs.py --since 30m --level WARNING        # all services merged by time
python scripts/logs.py --audit --user <id> --since 1d     # business events of one customer (also --order <id>)
python scripts/logs.py --request <id>                     # every line of one request, across services
python scripts/logs.py --turn <turn_id>                   # replay one assistant turn: message, tools, reply, tokens
python scripts/logs.py --follow --service order --grep cancel
```

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
| `test_service_auth.py` | Service-to-service auth: no/forged/expired/wrong-audience tokens, caller allowlist, health stays open. |
| `test_api.py`, `test_api_shop.py`, `test_api_orders.py`, `test_events_hub.py` | The REST gateway end to end (auth, catalog, cart, checkout, orders, WebSocket replay and resync). |
| `test_agent_rules.py`, `test_agent_tools.py`, `test_agent_chat.py`, `test_eval_scorer.py` | The agent: the rules fallback, the 8 tools, proposals, the chat turn over WebSocket, and the eval scorer. |
| `test_llm.py`, `test_keypool.py` | The LLM client with mocked HTTP (key rotation, retries, budgets, no key in logs) and the key pool's limits and cooling. |
| `test_seed_v2.py`, `test_run_all.py` | The seed data checks and the process runner. |

Run one file with `.venv\Scripts\python.exe -m pytest tests/test_api.py`.

### CI

Seven GitHub Actions workflows in `.github/workflows/` run on pushes and pull requests (plus the reusable `_python-tests.yml`
they share): Services gRPC (`services.yml`), Gateway API (`gateway.yml`), Assistant AI (`assistant-ai.yml`), Storefront Web
(`web.yml`), Ops tooling (`ops.yml`), Security (`security.yml`) and Docker (`docker.yml`). `scripts/ci_test_coverage.py` checks
that every test file runs in one of them.
