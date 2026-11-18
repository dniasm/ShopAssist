# ShopAssist

ShopAssist is a small store-assistant demo. You type a question such as
"Where is my order 1042?" and the system answers it by calling three
microservices over gRPC, then streams the progress back to you.

It is built as an onboarding project to show, in one flow:

- **REST**: the client sends a query with `POST /query`.
- **WebSocket streaming**: the orchestrator pushes progress events to `/ws/{query_id}`.
- **gRPC**: the orchestrator calls the product, inventory and order services.
- **Microservices with their own database**: each service owns one SQLite file.
  The orchestrator never touches a database; it only talks gRPC.

## Architecture

```
Client --REST POST /query--> Orchestrator (FastAPI :8000) --gRPC--> ProductService   :50051 -> data/products.db
   ^                              |                         --gRPC--> InventoryService :50052 -> data/inventory.db
   +----WebSocket /ws/{query_id}--+                         --gRPC--> OrderService     :50053 -> data/orders.db
```

Event flow for one query: `accepted` → `intent_detected` → `service_call` (one or more)
→ optional `partial_result` / `order_update` → `final_answer` or `error`.

## Setup

Requires Python 3.14 (the project uses a virtual environment in `.venv`).

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts/gen_protos.py
```

`scripts/gen_protos.py` generates the gRPC code in `shopassist_proto/` from `protos/`.
Run it once after install, and again whenever a `.proto` file changes.

Optional: copy `.env.example` to `.env` and change what you need. The defaults work as they are.

## Run

```powershell
python scripts/run_all.py
```

This starts the product, inventory and order services and the orchestrator. Each
process's output is prefixed with its name. Press **Ctrl+C** to stop everything; if any
one process exits, the others are stopped too.

The orchestrator listens on `http://localhost:8000` by default. Check it with:

```powershell
curl http://localhost:8000/health
```

`GET /services/health` checks each gRPC service and returns its status and latency. The System page
reads it, as described under **Frontend** below.

### Frontend

With `python scripts/run_all.py` running, open **http://localhost:8000/** in a browser, or use the port
from `ORCHESTRATOR_PORT` if you set one. The orchestrator serves two pages from `frontend/`: the
**Assistant** at `/` and the **System** page at `/system.html`. Both are plain HTML, CSS and JavaScript
modules. There is no build step and no Node. Everything is served locally, including the IBM Plex Sans
font (SIL Open Font License, in `frontend/fonts/`), so the pages need no internet connection. They do
need the local servers that `run_all.py` starts. To point a page at another orchestrator, add
`?api=http://localhost:8010` to its URL.

The header on both pages has a one-line status readout, such as `3 services up · Groq 4/4 keys`. It
says when a service is down or the backend can't be reached. Clicking it opens the System page.

**Assistant** (`/`) asks questions and shows the answers:

- **Route.** Beside each answer is a route with one node per real event:
  - *Question accepted* (REST, `POST /query`).
  - *Intent detected* (WebSocket), with an `llm` or `rules` badge and the entities it found.
  - One node per gRPC call to a service, such as *Product service*, with its method.
  - *Answer sent* (WebSocket, `final_answer`).

  Each node shows its time in milliseconds. The route can be collapsed. On a phone-width screen it
  folds away once the answer arrives.
- **Answer cards**, one for each kind of question:
  - **Order status:** a progress line from Placed to Delivered, a table of items with unit prices,
    subtotals and the total, and a status badge. A cancelled order shows "This order was cancelled."
  - **Live tracking:** the same progress line, a live status line and a timeline. Each order update
    moves the progress and adds a timeline row. Updates appear at least half a second apart, so a
    burst of replayed updates still animates. The badge ends on *Delivered* or *Cancelled*.
  - **Product search:** a grid of product tiles. Clicking a tile asks for that product's details. If a
    keyword matched nothing, a note says so and shows the closest products.
  - **Stock check:** for each product, a tile per size. In-stock tiles show the quantity. Low stock
    (2 or fewer) is shown in the warning colour. Out-of-stock sizes are dashed, greyed and struck through.
  - **Product details:** the price, colour, category, description and size availability.
  - **Question not supported:** a plain reply, then a "Questions it can answer" list to try.
  - **Errors:** a *Failed* card with the message and a **Retry** button. This covers an unreachable
    backend, a dropped connection and an error from the server.
- **Questions to try.** A list beside the conversation, and the same list on the empty page, which
  also shows a faint route to hint at what an answer brings. The questions are: order status, live
  tracking, product search, stock check, product details, and one unsupported question to see the fallback.

**System** (`/system.html`) shows how the system works, live:

- **Where a question goes.** A diagram of the browser, the orchestrator and the three services, each
  with its own database. The orchestrator has no database. Each service box is coloured Up or Down by
  the live check.
- **Service health.** Each service's address, status, latency, its average latency during this visit,
  and how many checks have run. The page checks every 10 seconds, and **Check now** runs a check at
  once. A check is one short gRPC call per service. A "not found" reply counts as up, because it shows
  the service answered.
- **Groq key pool.** Each key with its state (OK, Cooling or Disabled) and four meters: requests and
  tokens per minute and per day, each showing what is left against its cap. A meter under 20% left
  turns to the warning colour. A cooling key shows when it comes back. A disabled key is marked as
  rejected by Groq.
- **What happens to a question.** A table of the events, when each one is sent, and what the Assistant
  draws from it.

A sentence at the top of the System page sums up the state, for example "All 3 services answer, the
slowest in 12 ms."

Animations and transitions are turned off when your system asks for reduced motion. Both pages follow
your system's light or dark setting.

### Reset the data

```powershell
python scripts/run_all.py --reset
```

`--reset` deletes the three SQLite databases (`data/products.db`, `data/inventory.db`,
`data/orders.db`, plus any leftover `-wal`, `-shm` or `-journal` files) before the services
start. The services then rebuild them from `data/seed/`. The script prints each file it removes.

Use it when you want to repeat a live-tracking demo. Order tracking writes each status step
(PLACED → PACKED → SHIPPED → OUT_FOR_DELIVERY → DELIVERED) to `data/orders.db` permanently. Without
a reset, order 1043 stays DELIVERED after the first "Track order 1043 live", and the demo can't
show the steps again. `--reset` only touches the database files. The seed JSON files are never changed.

The reset uses the same database settings as the services (`PRODUCT_DB`, `INVENTORY_DB`,
`ORDER_DB`, read from `.env` if present). Relative paths are resolved against the repo root.

## Ask a question

From the command line (in a second terminal, while `run_all.py` is running):

```powershell
python client/cli.py "Where is my order 1042?"
python client/cli.py "Track order 1043 live"
```

Each event is printed on one line, and the final answer is printed at the end. Add
`--base http://localhost:8000` to point at a different orchestrator.

`client/index.html` is a minimal debug client. It shows the raw WebSocket events as a plain list,
so you can see exactly what the orchestrator sends. Open it straight from disk (`file://`), type a
question and press Send. Its **Orchestrator URL** box defaults to `http://localhost:8000`, and you
can change it to another base URL. `client/cli.py` remains the terminal client.

## How a query flows

Take `Track order 1043 live` as an example. The client makes two requests, and the server
pushes events over the WebSocket as the work proceeds.

1. **Submit.** The client sends `POST /query` with `{"text": "Track order 1043 live"}`. The
   orchestrator replies `202` with a `query_id` and a `ws_url` (`/ws/<query_id>`). It starts
   the work in the background, so the reply comes back at once.
2. **Connect.** The client opens the WebSocket. Events that happened before the connection
   are replayed first, so a late connection still sees the whole sequence.
3. **Events, in order:**
   - `accepted`: the query was received.
   - `intent_detected`: `{intent, entities, source}`. Here `intent` is `track_order`,
     `entities` has `order_id: "1043"`, and `source` is `llm` or `rules`.
   - `service_call`: `{service, method}`, e.g. `OrderService.TrackOrder`. There is one per gRPC
     call. Some intents need two, such as stock checks, which call Product and then Inventory.
   - `partial_result`: intermediate data from a service call, when there is any.
   - `order_update`: one per status change in the tracking stream, e.g. PLACED, then PACKED,
     then DELIVERED. These arrive about `ORDER_SIM_STEP_SECONDS` apart.
   - `final_answer`: `{answer, data}`. This is the last event on a successful query, and the CLI
     prints `answer` at the end.
   - Or `error`: `{message}`, which is the only terminal event on failure. For example, a service
     that can't be reached gives an `error`. A missing order does not: it gives a friendly
     `final_answer`, such as "I couldn't find order 9999".

The server then closes the socket. Each query has exactly one terminal event, either
`final_answer` or `error`, and it is always the last event.

## Example queries

| Example query | Intent | Services used |
|---|---|---|
| "Where is my order 1042?" | `order_status` | Order (GetOrder), Product (GetProduct for item names) |
| "Track order 1043 live" | `track_order` | Order (TrackOrder, server-streaming, forwarded as `order_update` events) |
| "Show me headphones under $100" | `product_search` | Product (SearchProducts) |
| "Do you have the blue hoodie in size M?" | `stock_check` | Product (SearchProducts), then Inventory (GetStock) |
| "Tell me about P-1003" | `product_info` | Product (GetProduct), Inventory (GetStock) |
| "What's the weather?" | `unknown` | None; the assistant replies with a list of what it can do |

Sample data is in `data/seed/`: 15 products, their stock by size, and orders 1040 to 1049.
Order 1042 is shipped, 1043 is placed, 1044 is delivered and 1045 is cancelled.

## How questions are understood

The orchestrator first asks the Groq LLM (`openai/gpt-oss-120b`) to detect the intent and
entities. If no key can serve the request, or the call fails or times out, it uses a
deterministic rule-based parser. The `intent_detected` event's `source` field says which path
answered: `llm` or `rules`.

The rule-based path needs no key and no network, so the whole demo works offline.

## LLM configuration (optional)

The LLM is Groq's `openai/gpt-oss-120b`, called through Groq's OpenAI-compatible API. It is
optional. With no keys, the app uses the rule-based parser and works fully.

### Settings

Set these in `.env`. Copy `.env.example` to start.

| Variable | Default | Meaning |
|---|---|---|
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | Groq API base URL |
| `LLM_API_KEYS` | empty | Comma-separated Groq keys. Empty means rules only. |
| `LLM_MODEL` | `openai/gpt-oss-120b` | Model name |
| `LLM_REASONING_EFFORT` | `low` | `low`, `medium` or `high` |
| `LLM_MAX_TOKENS` | `1024` | Maximum completion tokens per request |
| `LLM_TIMEOUT` | `10` | Total seconds for one intent detection, across all key attempts |
| `LLM_LIMIT_RPM` | `30` | Requests per minute, per key |
| `LLM_LIMIT_RPD` | `1000` | Requests per day, per key |
| `LLM_LIMIT_TPM` | `8000` | Tokens per minute, per key |
| `LLM_LIMIT_TPD` | `200000` | Tokens per day, per key |

The four `LLM_LIMIT_*` values are Groq's base limits for a developer key. Your organisation may
have different ones. Check console.groq.com/settings/limits and match them. The app uses these
numbers to decide when a key has room, so wrong values make it wait too long or hit Groq's limits.

The legacy single-key variable `LLM_API_KEY` still works. Its value is merged into `LLM_API_KEYS`.

### Adding several keys

Put all the keys on one line, separated by commas:

```
LLM_API_KEYS=gsk_first_key,gsk_second_key,gsk_third_key
```

Keys from the same Groq account share the same limits, so they add no capacity. To get more
capacity, use keys from different accounts or organisations.

### How key rotation works

Before each request, the app picks a key that has room on all four limits: requests per minute
and per day, and tokens per minute and per day. If several keys have room, it uses the one with
the most room left. Equal keys take turns.

- **Skipping:** a key is skipped before it runs out on any of the four limits.
- **Rate limited (429):** that key cools down until Groq's reset time, and the same request is
  retried at once on the next key.
- **Invalid key (401/403):** that key is disabled until the app restarts.
- **Timeout, server error or malformed reply:** the key is not blamed. The app tries one more key
  if time remains.
- **No key can serve:** the rule-based parser answers, so the app never breaks.

Keys are never printed or returned. Logs and the status endpoint refer to them as `key#1`,
`key#2` and so on.

### Checking the key pool

`GET /llm/status` shows the state of each key:

```powershell
curl http://localhost:8000/llm/status
```

The response has `enabled`, `model`, `limits` (the four caps: `rpm`, `rpd`, `tpm` and `tpd`) and a
list of `keys`. Each key shows its masked form (for example `key#1 …abcd`), its state (`ok`, `cooling`
or `disabled`), the requests and tokens left in each window, and when a cooling key comes back. Full
keys are never shown.

### Keep your keys out of git

Never commit a real key. Put keys only in `.env`, which is listed in `.gitignore`. Never put them
in `.env.example`, because git tracks that file. Before you commit, run `git status` and check
that `.env` is not listed.

## Other settings

All settings are environment variables; see `.env.example` for the full list with comments. The
main ones are the service addresses (`PRODUCT_ADDR`, `INVENTORY_ADDR`, `ORDER_ADDR`), the
orchestrator port (`ORCHESTRATOR_PORT`), the database files (`PRODUCT_DB`, `INVENTORY_DB`,
`ORDER_DB`) and `ORDER_SIM_STEP_SECONDS`, which sets how fast order tracking steps through
its statuses.

Databases are created on first start from `data/seed/` if they are missing or empty.

## Troubleshooting

**Port 8000 is already in use.** If another program holds port 8000, the orchestrator fails to
start with an error such as `[Errno 10048] ... address ... already in use`. `run_all.py` then
stops every other process, so nothing is left running. Pick another port:

1. Set `ORCHESTRATOR_PORT` in `.env`, e.g. `ORCHESTRATOR_PORT=8010`.
2. Run the CLI against that port:
   ```powershell
   python client/cli.py "Where is my order 1042?" --base http://localhost:8010
   ```
3. In `client/index.html`, change the **Orchestrator URL** box to `http://localhost:8010`.

To find what is using port 8000 on Windows, run `netstat -ano | findstr :8000` and look up the
PID in Task Manager.

If `http://localhost:8000/` shows a different app, another server owns that port. Set
`ORCHESTRATOR_PORT=8011` in `.env`, restart `run_all.py`, and open `http://localhost:8011/`.

## Tests

```powershell
pytest
```

There are 136 tests, and they need no network and no LLM key. Everything runs locally against
temporary files, a fake clock and mocked HTTP. The test files in `tests/` are:

| File | What it checks |
|---|---|
| `test_intent.py` | The rule-based parser: each intent from the example table, entity extraction, and edge cases such as a price being mistaken for an order ID. |
| `test_llm.py` | The LLM intent path with the HTTP call mocked: a successful reply, and each failure that must fall back to rules. |
| `test_keypool.py` | The key pool: parsing Groq's reset durations such as `2m59.56s`, headroom from the rate-limit headers, the local per-minute and per-day counters, cooling after a 429, disabling a key after a 401/403, round-robin between equal keys, and keys never appearing in status, logs or settings output. |
| `test_composer.py` | The answer templates, as pure functions of the entities and data. |
| `test_relaxed_search.py` | The relaxed product search: when a keyword matches nothing, it is dropped once and the answer says so, and colour and other filters are kept. |
| `test_services.py` | Each gRPC service through a real channel, against a temporary database. Includes order tracking to DELIVERED and the seed-only-when-empty rule. |
| `test_e2e.py` | End to end: real gRPC servers on free ports, with the REST and WebSocket endpoints through FastAPI's test client. Covers the event order, late and two-socket replay, and the friendly not-found and unreachable-service paths. |
| `test_frontend.py` | Both pages (`/` and `/system.html`), the shared header and module imports, the fonts, the `/services/health` and `/llm/status` endpoints, and CORS. It also checks the frontend statically: no DOM built from HTML strings, no external resources, no Node toolchain, the CSS house rules, and that the System page's event names and the final-answer shapes match the real code. |
| `test_isolation.py` | The orchestrator has no `sqlite3` import and no database paths. It only talks gRPC. |

Run a single file with `pytest tests/test_e2e.py`.
