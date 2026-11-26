# Logging and audit trail (structlog)

Date: 2026-11-26. Status: approved (structlog, audit kept forever, service logs 30 days).

## Why
Today every process prints plain text to the console and nothing is kept: a restart loses it all. The assistant logs
only token counts, so a bad chat (e.g. Hana's "Mirelle headphones" turn, where a search returned 0 results) cannot be
diagnosed from evidence. The user wants to audit the system as a whole: errors across services, checkouts and
cancellations, and what the assistant did on each turn.

## Goals
- Every process (catalog, inventory, order, customer, payment, assistant, orchestrator) writes structured JSON-lines
  logs to disk that survive restarts, plus the readable console output it has today.
- One request id follows a request across all services, so one chat turn or one checkout can be read end to end.
- An audit trail of business events (list below), kept separately from noisy service logs.
- Every assistant turn is replayable from the logs: message, each tool call with arguments and a short result,
  facts used and saved, reply, llm calls and tokens.
- A viewer script to search and follow logs.

## Non-goals
- No log server, dashboard or Docker log stack (Loki/ELK/OpenTelemetry). Can come later.
- No audit table in Postgres (option B). Can come later on top of the same events.
- No change to how the web app logs.

## Anti-goals (a failure even if it works)
- A secret (password, API key, DB URL with password, HMAC key, session token) ever appears in a log file.
- Logging slows or breaks a request: a log write failure must never fail the request.
- Two processes writing or rotating the same file (breaks on Windows).

## Approaches considered
- A. JSON-lines files + audit file + viewer script, own JSON formatter on stdlib `logging`.
- A on structlog (chosen by the user): same as A; structlog does the structured events, JSON rendering and the
  request-id context (contextvars). Existing `logging.getLogger(...)` calls and uvicorn/grpc logs keep working because
  structlog renders stdlib records too (`ProcessorFormatter`).
- B. Same, with audit events in a Postgres table. More work and a new failure point; deferred.
- C. Grafana Loki / Elasticsearch in Docker. Too heavy for this project; deferred.
- loguru (replaces stdlib logging; uvicorn/grpc need glue), python-json-logger (formatter only), OpenTelemetry
  (needs a backend) were rejected.

## Design

### Shared setup: `shopassist_proto/logs.py` (importable by services and the orchestrator)
- `setup_logging(service: str)` called once in each `main()` instead of `logging.basicConfig`.
- Settings from env: `LOG_DIR` (default `<repo>/logs`), `LOG_LEVEL` (default INFO), `LOG_RETENTION_DAYS` (default 30).
- Two outputs:
  - console: readable one line per event (today's format plus key=value fields), no colour; run_all keeps its prefix.
  - file: one JSON object per line.
- Files per process, named by date, no renaming (Windows-safe):
  `logs/<service>/<YYYY-MM-DD>.jsonl` for all events of that process, and
  `logs/audit/<service>-<YYYY-MM-DD>.jsonl` for audit events of that process.
  The handler switches to the new date's file at midnight (local time). On start and at each date switch, service log
  files older than `LOG_RETENTION_DAYS` are deleted. Audit files are kept forever by default (`AUDIT_RETENTION_DAYS`,
  default 0 = never delete); they are a few KB per day at this scale (user decision, 2026-11-26).
- Every event has: `ts` (ISO, local tz), `level`, `service`, `event` (short snake_case name), `logger`, and when known
  `request_id`, `user_id`, `conversation_id`, `turn_id`; exceptions add `exc` (traceback text).
- `audit(event, **fields)` helper: logs at INFO with `audit=true`; routed to both the service file and the audit file.
- Redaction processor (last before rendering): drops or masks fields named like password, token, secret, key,
  authorization, cookie, database_url; masks known secret shapes in any string (Groq `gsk_...`, Voyage keys,
  `postgresql://user:pass@` → `postgresql://user:***@`). Unit-tested.
- A failing file write is swallowed (stdlib handler error handling), never raised into the request.

### Request id
- Orchestrator HTTP middleware: takes `X-Request-Id` from the request if present (validated, ≤64 chars) or makes one;
  binds it (and `user_id` once the session is known) with structlog contextvars; returns it in the `X-Request-Id`
  response header.
- Chat turns run after the 202 response: the turn gets its own `turn_id`, bound for the whole turn (and inherited by
  the background tasks it starts); the `request_id` of the POST is kept on the turn's events.
- gRPC: the client interceptors in `shopassist_proto/svcauth.py` (`_Signer`) add `x-request-id` metadata from the
  current context; a server interceptor added next to `AuthInterceptor` in `services/common/server.py` binds it (and
  `x-user-id`) for the call. Service-to-service calls (order → inventory/payment/catalog) carry it on automatically.
- Background work with no request (order recovery, fulfilment, order watch, memory worker) binds its own id
  (`job-...`), plus the order id or turn id it works on.

### Access logs (service files, not audit)
- HTTP: one `http_request` event per request: method, path (no query values), status, ms, user_id. Uvicorn's own
  access log is turned off to avoid duplicates; health checks at DEBUG.
- gRPC server: one `grpc_call` event per call: method, caller, status code, ms. Errors (non-OK) at WARNING, unexpected
  exceptions at ERROR with traceback.

### Audit events (where they are emitted)
| Area | Events |
|---|---|
| Auth (orchestrator routes_auth, customer service) | `login_ok`, `login_failed` (email, reason; never the password), `signup`, login rate-limit hit |
| Cart (customer SetCartLine/ClearCart) | `cart_line_set` (sku, qty, before→after), `cart_line_removed`, `cart_cleared`; field `source` = web or assistant |
| Checkout (orchestrator checkout_flow, order service saga) | `checkout_started` (cart total, lines), `stock_reserved`, `payment_charged` / `payment_failed` (amount, reason), `order_placed`, `order_confirmed`, `order_failed`, `stock_released` |
| Cancel / refund (order cancel, payment Refund) | `cancel_requested`, `order_cancelled`, `refund_issued` / `refund_failed` |
| Inventory | `restock` |
| Assistant proposals (routes_chat) | `proposal_created` (checkout_redirect / cancel_order), `proposal_confirmed`, `proposal_dismissed` |
| Assistant memory (assistant service ApplyFacts/ClearTemporalFacts) | `facts_changed` (added/replaced/removed: key, value, kind, category), `temporal_facts_cleared` (categories, count) |
| Conversations | `conversation_created`, `conversation_renamed`, `conversation_deleted` |
| Assistant turn (agent loop) | `assistant_turn`: conversation_id, turn_id, user message, tool calls `[{name, args, result (first 300 chars), ms, error}]`, facts recalled (keys), reply text, llm_calls, prompt/completion tokens, total ms, outcome (ok / llm_down / max_steps) |

Each assistant LLM call also logs `llm_call` (model, key index not the key, prompt/completion tokens, ms, retry) at
INFO in the orchestrator file, and the memory worker logs `memory_update` (summary length, facts proposed).

Chat text and user emails are in the logs on purpose (the audit needs them). The `logs/` folder is in `.gitignore`.

### Viewer: `scripts/logs.py`
- Reads `logs/**/*.jsonl`, merges by `ts`, prints one readable line per event (time, service, level, event, key fields).
- Filters (combinable): `--service`, `--level` (minimum), `--audit`, `--user`, `--request`, `--turn`, `--order`,
  `--event`, `--since 2h|1d|<ISO>`, `--until`, `--grep TEXT`, `--json` (raw lines), `--follow` (tail new lines).
- `--turn <id>` prints the turn as a block: message, each tool call with args and result, reply, tokens.
- Works on Windows; standard library only.

### Docker
- (Builder call) Services get `LOG_DIR=/app/logs` and a bind mount `./logs:/app/logs`, so `scripts/logs.py` works on
  the host the same way as locally; `TZ` is passed through so file dates and the midnight switch match the host.
  Console output unchanged for `docker logs`.

### Builder calls settled during review
- Audit events ignore `LOG_LEVEL` (always written), so raising the level never drops audit records.
- Redaction matches whole field names, not substrings (so `prompt_tokens` stays); the fact key field is `fact_key`.
- Cart `source` is `assistant` when a `turn_id` is bound (forwarded as `x-turn-id` metadata), else `web`.
- Per-process files are safe: each service is one process (one container, one run_all child). Tests never call
  `setup_logging`, so they write no files.

## Tests (pytest, files named so the CI coverage guard picks them up)
- Redaction: each secret shape and each sensitive field name is masked in file and console output.
- File handler: date-named path, switch at a date change (fake clock), retention deletes only old files,
  a write failure does not raise.
- Request id: HTTP middleware sets and returns it; gRPC client metadata carries it; server interceptor binds it;
  a log line from inside a service call carries the orchestrator's request id (in-process gRPC test).
- Audit: checkout, cancel, cart change, login failure, proposal confirm, facts change each emit their event with the
  expected fields (capture structlog output).
- Assistant turn: a scripted fake-LLM turn produces one `assistant_turn` event with the tool calls, args and reply.
- Viewer: filters on a fixture log folder, `--turn` block, `--since`.

## Left to the builder
- Exact module layout inside `shopassist_proto/logs.py`, processor order, console line format.
- Which existing `log.info(...)` calls become structured events (keep messages, add fields where cheap).
- Field names beyond those listed.
