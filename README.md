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

### Reset the data

```powershell
python scripts/run_all.py --reset
```

`--reset` deletes the three SQLite databases (`data/products.db`, `data/inventory.db`,
`data/orders.db`, plus any leftover `-wal`, `-shm` or `-journal` files) before the services
start. The services then rebuild them from `data/seed/`. The script prints each file it removes.

Use it when you want to repeat a live-tracking demo. Order tracking writes each status step
(PLACED → PACKED → SHIPPED → OUT_FOR_DELIVERY → DELIVERED) to `orders.db` permanently. Without
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

Or open `client/index.html` in a browser, type a question, and press Send. It shows the
same event stream in a list.

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

The orchestrator first tries an LLM to detect the intent and entities. If no LLM is
configured, or the call times out or fails, it uses a deterministic rule-based parser.
The `intent_detected` event says which path was used in its `source` field (`llm` or `rules`).

The rule-based path needs no key and no network, so the whole demo works offline.

## LLM configuration (optional)

Any OpenAI-compatible Chat Completions provider works. Set these in `.env`:

| Variable | Meaning |
|---|---|
| `LLM_BASE_URL` | Base URL of the provider, e.g. `https://api.openai.com/v1` |
| `LLM_API_KEY` | API key. Leave it empty to use the rule-based parser only. |
| `LLM_MODEL` | Model name, e.g. `gpt-4o-mini` |
| `LLM_TIMEOUT` | Seconds to wait for the LLM before falling back to rules (default `10`) |

Never commit a real key. `.env` is in `.gitignore`.

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

## Tests

```powershell
pytest
```

There are 60 tests, and they need no network and no LLM key. Everything runs locally against
temporary files and mocked HTTP. The test files in `tests/` are:

| File | What it checks |
|---|---|
| `test_intent.py` | The rule-based parser: each intent from the example table, entity extraction, and edge cases such as a price being mistaken for an order ID. |
| `test_llm.py` | The LLM intent path with the HTTP call mocked: a successful reply, and each failure that must fall back to rules. |
| `test_composer.py` | The answer templates, as pure functions of the entities and data. |
| `test_services.py` | Each gRPC service through a real channel, against a temporary database. Includes order tracking to DELIVERED and the seed-only-when-empty rule. |
| `test_e2e.py` | End to end: real gRPC servers on free ports, with the REST and WebSocket endpoints through FastAPI's test client. Covers the event order, late and two-socket replay, and the friendly not-found and unreachable-service paths. |
| `test_isolation.py` | The orchestrator has no `sqlite3` import and no database paths. It only talks gRPC. |

Run a single file with `pytest tests/test_e2e.py`.
