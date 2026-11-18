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

## Tests

```powershell
pytest
```

The tests need no network and no LLM key. They cover the rule-based parser, the answer
composer, each service against a temporary database, and an end-to-end run with real gRPC
servers and the REST and WebSocket endpoints.
