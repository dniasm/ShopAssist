# Logging (based on the user's stdlib snippet)

Date: 2026-11-26. Status: approved (location `shared_common/common/log.py`, all five additions kept).
The earlier structlog design and plan are parked on branch `wip/logging-structlog` (user's choice: use their snippet).

## Why
Logs must be kept, so the whole system can be audited: errors across services, checkouts and cancellations, logins,
and what the assistant did on each turn. Today everything is printed to the console only and lost on restart.

## Base: the user's snippet, used as given
`shared_common/common/log.py`, a new shared package (two empty `__init__.py` files and a `COPY shared_common/`
line in the `Dockerfile`). Kept as written:
- `_request_id` ContextVar (default `-`), `_Context` filter adding `service` and `request_id` to every record.
- `setup_logging(service)` called once in each entry point; console handler on stdout; format
  `%(asctime)s %(levelname)-7s [%(service)s] [%(request_id)s] %(name)s: %(message)s`, time `%H:%M:%S`;
  `LOG_LEVEL` env; `basicConfig(force=True)`.
- `set_request_id(value=None)` (start or continue a trace), `get_request_id()`.
- Call style in code: `logger = logging.getLogger(__name__)` at the top; `logger.info("Thing happened: x=%s", x)`;
  `logger.exception("Thing failed: x=%s", x)` in `except` blocks.

## Additions needed to meet the goals (each small, inside or next to the snippet; all kept by the user)
1. **Kept on disk.** `setup_logging` adds a second handler with the same format and filter, writing
   `logs/<service>/<YYYY-MM-DD>.log` (date-named, a new file each day, no renaming so it is safe on Windows).
   Service logs older than 30 days are deleted (`LOG_RETENTION_DAYS`). `LOG_DIR` env (default `<repo>/logs`).
   `logs/` is git-ignored.
2. **Audit file.** Business events go through `logging.getLogger("audit")`; a third handler takes only that logger
   and writes `logs/audit/<service>-<YYYY-MM-DD>.log`, kept forever. Audit lines also go to the service file.
   Messages use `key=value` so they can be searched, e.g. `logger.info("order_cancelled order=%s user=%s", ...)`.
3. **Request id across services.** The orchestrator calls `set_request_id(...)` per HTTP request (from an
   `X-Request-Id` header or a new id) and per chat turn; the gRPC client interceptor in `shopassist_proto/svcauth.py`
   sends `get_request_id()` as `x-request-id` metadata; a server interceptor in `services/common/server.py` calls
   `set_request_id(incoming)` for each call. Background jobs (order recovery, watchers, memory worker) start their own.
4. **Secrets never logged.** A filter next to `_Context` masks API keys (`gsk_…`), passwords in DB URLs and
   `password=`/`token=` values in the final message text.
5. **Errors.** Unhandled HTTP and gRPC errors are logged with `logger.exception(...)`; each HTTP request and gRPC call
   gets one access line (method, path/RPC, status, ms).

## Audit events (logger "audit")
Login ok/failed (email, never the password), signup, cart line set/removed (sku, qty, web or assistant),
checkout started, stock reserved/released, payment charged/failed, order placed/confirmed/failed,
cancel requested, order cancelled, refund issued/failed, assistant proposals created/confirmed/dismissed,
facts added/replaced/removed, temporal facts cleared, conversations created/renamed/deleted, and one
`assistant_turn` line per turn: message, each tool call with its arguments and a short result, reply, llm calls, tokens.

## Inspecting
- Open the files in any editor, or grep: `grep "\[a1b2c3d4e5f6\]" logs/*/2026-11-26.log` follows one request
  across every service.
- `scripts/logs.py` (small, stdlib): merges all services' files for a day by time and filters by `--service`,
  `--level`, `--request`, `--user`, `--audit`, `--since`, `--grep`, `--follow`.

## Approaches
- User's snippet + additions above (chosen by the user).
- structlog with JSON lines (parked on `wip/logging-structlog`).

## Left to the builder
Exact wording of each log message, which existing log lines change, handler class details.
