# Logging and Audit Trail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every process keeps structured JSON-lines logs on disk, one request id follows a request across services, business events form an audit trail, each assistant turn is replayable, and `scripts/logs.py` reads it all.

**Architecture:** One shared module `shopassist_proto/logs.py` configures structlog over stdlib logging (console + per-process date-named JSON files + audit files). Context (request id, user, turn) lives in structlog contextvars; gRPC client interceptors forward it as metadata and a server interceptor binds it. Audit events are emitted at the points that already perform each action.

**Tech Stack:** Python 3.14, structlog 25.5.0, FastAPI/uvicorn, grpc.aio, pytest.

**Spec:** `docs/superpowers/specs/2026-11-26-logging-audit-design.md`

## Global Constraints
- Dependency: `structlog==25.5.0` in `requirements.txt`; no other new runtime dependency. Viewer uses stdlib only.
- Env: `LOG_DIR` (default `<repo>/logs`), `LOG_LEVEL` (default `INFO`), `LOG_RETENTION_DAYS` (default 30, service logs), `AUDIT_RETENTION_DAYS` (default 0 = never delete).
- Files: `logs/<service>/<YYYY-MM-DD>.jsonl`, `logs/audit/<service>-<YYYY-MM-DD>.jsonl`; date = local time; never rename files.
- Every event: `ts` (ISO 8601 with offset), `level`, `service`, `event`, `logger`; when bound: `request_id`, `user_id`, `conversation_id`, `turn_id`; exceptions add `exc`.
- Audit events have `audit: true`, ignore `LOG_LEVEL`, go to both the service file and the audit file.
- Secrets never reach any output (console or file). Logging failures never raise into a request.
- Metadata keys: `x-request-id`, `x-turn-id` (existing: `x-user-id`). Request id: `[A-Za-z0-9._-]{1,64}`, else replaced by a new one.
- Fact key field in events is `fact_key`.
- Tests never call `setup_logging` except with a `tmp_path` `LOG_DIR`; they capture with `structlog.testing.capture_logs()`.
- Files are CRLF: edit with the Edit tool, not `str.replace` on `\n`.
- New test files are named `tests/test_logs*.py` and covered by `ops.yml` (`scripts/ci_test_coverage.py` must pass).
- Branch per task (`logging/01-core` … `logging/05-viewer`), each pushed and green on all CI workflows before the next; stacked on the previous; merge to main at the end.

## Review Focus
1. Secret in a nested value (a dict arg in a tool call holding `password`, a DB URL inside an exception message) → masked. Test in Task 1 (`test_redacts_nested_and_exception_text`).
2. Log dir not writable / disk error → request still succeeds, one stderr warning. Test in Task 1 (`test_unwritable_dir_does_not_raise`).
3. Client sends a hostile `X-Request-Id` (300 chars, newlines, `../`) → replaced by a generated id, not written to files. Test in Task 2 (`test_bad_request_id_replaced`).
4. Context leaking between concurrent requests/turns (user A's id on user B's line) → each request/turn sees only its own context. Test in Task 2 (`test_context_isolated_between_concurrent_calls`).
5. Viewer on a half-written last line or a non-JSON line → skips it, keeps going. Test in Task 5 (`test_skips_bad_lines`).

---

### Task 1: Core logging module (branch `logging/01-core`)

**Files:**
- Create: `shopassist_proto/logs.py`
- Modify: `orchestrator/main.py:14`, `services/{catalog,inventory,order,customer,payment,assistant}/server.py` (the `logging.basicConfig(...)` line in each `main`), `requirements.txt`, `.gitignore` (add `logs/`), `.github/workflows/ops.yml` (add `tests/test_logs*.py` to its `tests:` line)
- Test: `tests/test_logs_core.py`

**Interfaces:**
- Produces:
  - `setup_logging(service: str, *, log_dir: Path | None = None, level: str | None = None, clock: Callable[[], datetime] | None = None) -> None` — idempotent (second call reconfigures); reads env when args are None.
  - `get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger`
  - `audit(event: str, **fields) -> None` — emits with `audit=True` at INFO regardless of level.
  - `bind(**fields) -> None`, `unbind(*names) -> None`, `bound() -> dict` (thin wrappers on `structlog.contextvars`), and `context(**fields)` — a context manager that binds and restores on exit.
  - `redact(value: Any) -> Any` — pure function used by the processor.
  - `class DailyFile(logging.Handler)`: `__init__(self, directory: Path, pattern: str, retention_days: int, clock)`; `pattern` is `"{date}.jsonl"` or `"<service>-{date}.jsonl"`; opens the file for the current date lazily, switches when the date changes, prunes files older than `retention_days` (0 = never) on open; `handleError` writes one line to stderr and never raises.
- Processor chain: `merge_contextvars` → add `service` → `add_log_level` → `TimeStamper(fmt="iso", utc=False)` → `format_exc_info` (into `exc`) → redaction → render (JSON for files, `ConsoleRenderer(colors=False)` for stdout). Stdlib records go through the same chain via `ProcessorFormatter(foreign_pre_chain=...)`, so existing `logging.getLogger(__name__)` calls get context and JSON for free.
- Redaction rules: whole-name match (case-insensitive) on `password, passwd, secret, token, api_key, apikey, authorization, cookie, session, key_material, database_url, dsn` and any name ending in `_password`, `_secret`, `_token`, `_api_key`, `_database_url` → `"***"`. Not `prompt_tokens`, `completion_tokens`, `fact_key`. In every string (recursive through dicts/lists/tuples): `gsk_[A-Za-z0-9]{10,}` → `gsk_***`; `pa-[A-Za-z0-9_-]{20,}` → `pa-***`; `(postgres(?:ql)?://[^:/@\s]+):[^@\s]+@` → `\1:***@`; `SVC_KEY_[A-Z_]+=\S+` → `SVC_KEY_…=***`.

- [ ] **Step 1: Write failing tests** in `tests/test_logs_core.py`:
  - `test_writes_service_and_audit_files(tmp_path)`: `setup_logging("order", log_dir=tmp_path, clock=fixed(2026-11-26 10:00))`; `get_logger("x").info("hello", order_id="O-1")`; `audit("order_placed", order_id="O-1")`; assert `tmp_path/"order"/"2026-11-26.jsonl"` has 2 JSON lines with `service=="order"`, `event` in `{"hello","order_placed"}`; `tmp_path/"audit"/"order-2026-11-26.jsonl"` has exactly 1 line with `audit is True`.
  - `test_stdlib_logger_gets_json_and_context(tmp_path)`: `bind(request_id="r1")`; `logging.getLogger("services.x").warning("boom %s", 1)`; line has `event=="boom 1"`, `request_id=="r1"`, `level=="warning"`.
  - `test_audit_ignores_level(tmp_path)`: `setup_logging(..., level="ERROR")`; `audit("login_failed")` written; `get_logger().info("x")` not written.
  - `test_switches_file_at_midnight(tmp_path)`: clock moves 23:59 → 00:01 next day; two events land in two date files.
  - `test_retention(tmp_path)`: pre-create `order/2026-10-01.jsonl` (56 days old) and `order/2026-11-20.jsonl`, `audit/order-2026-01-01.jsonl`; after setup with retention 30 / audit 0: first gone, second and audit kept.
  - `test_redacts_fields`: `redact({"password":"p","prompt_tokens":5,"fact_key":"size_top","authorization":"Bearer x"}) == {"password":"***","prompt_tokens":5,"fact_key":"size_top","authorization":"***"}`.
  - `test_redacts_nested_and_exception_text(tmp_path)`: log `args={"user":{"password":"p"}}` and an exception whose message contains `postgresql://svc:hunter2@localhost/db` and `gsk_abcdefghijklmnop`; file text contains none of `hunter2`, `gsk_abcdefghijklmnop`, `"p"` as password value.
  - `test_unwritable_dir_does_not_raise(tmp_path, capsys)`: `log_dir` points at a file (not a dir); `get_logger().info("x")` does not raise; stderr has one warning line.
  - `test_console_line_readable(tmp_path, capsys)`: stdout line contains `hello` and `order_id=O-1`, no `{`.
- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/test_logs_core.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement `shopassist_proto/logs.py` per the Interfaces block; add `structlog==25.5.0` to `requirements.txt` and install it.
- [ ] **Step 4:** Replace each of the 7 `logging.basicConfig(...)` lines with `setup_logging("<service>")` (orchestrator: `"orchestrator"`). Add `logs/` to `.gitignore`; add `tests/test_logs*.py` to the `tests:` line of `.github/workflows/ops.yml`.
- [ ] **Step 5:** Run `.venv/Scripts/python -m pytest tests/test_logs_core.py -q` → PASS; `.venv/Scripts/python scripts/ci_test_coverage.py` → exit 0; full suite `.venv/Scripts/python -m pytest -q -x` → no new failures (known flaky: reconnect tests in `tests/test_api_shop.py`).
- [ ] **Step 6:** Start the stack (`scripts/run_all.py --web`), hit `/api/health`, confirm `logs/orchestrator/<today>.jsonl` and `logs/catalog/<today>.jsonl` exist; stop it.
- [ ] **Step 7:** Commit "Logging: structlog setup, daily JSON files, audit files, redaction"; push `logging/01-core`; wait for all CI workflows green.

### Task 2: Request id, turn id and access logs (branch `logging/02-context`, from 01)

**Files:**
- Create: `orchestrator/request_log.py` (HTTP middleware)
- Modify: `orchestrator/app.py` (register middleware), `orchestrator/main.py` (`uvicorn.run(..., access_log=False)`), `shopassist_proto/svcauth.py` (`_Signer._details` appends context metadata), `services/common/server.py` (server interceptor before `AuthInterceptor`), `orchestrator/routes_chat.py` (bind `turn_id`/`conversation_id` for the background turn), `orchestrator/agent/memory.py`, `orchestrator/order_watch.py`, `services/order/{recovery,fulfilment,watch}.py` (bind `request_id=f"job-{uuid4().hex[:12]}"` plus the order/turn id per unit of work)
- Test: `tests/test_logs_context.py`

**Interfaces:**
- Consumes: `bind`, `context`, `bound`, `get_logger` from Task 1.
- Produces:
  - `shopassist_proto/logs.py`: `new_request_id() -> str` (12 hex chars), `valid_request_id(value: str | None) -> str | None`, `context_metadata() -> tuple[tuple[str, str], ...]` (returns `x-request-id` / `x-turn-id` entries for whatever is bound).
  - `services/common/server.py`: `class LogContextInterceptor(grpc.aio.ServerInterceptor)` — wraps the method handler (unary-unary and unary-stream behaviours, like `_denying_handler` in `svcauth.py`) so that inside the call it binds `request_id` (valid metadata or new), `turn_id`, `user_id` (`x-user-id`), `caller` (`x-caller`), and after it logs `grpc_call` (`method`, `caller`, `code`, `ms`): OK → INFO, non-OK status → WARNING, unexpected exception → ERROR with traceback (then re-raise). Health `Check` → DEBUG.
  - `orchestrator/request_log.py`: `class RequestLogMiddleware` (pure ASGI) — binds `request_id` from `X-Request-Id` (validated) or new; sets `X-Request-Id` response header; logs `http_request` (`method`, `path` without query, `status`, `ms`, `user_id` if the route bound one); `/api/health` → DEBUG; 5xx → ERROR. Context cleared after each request.
  - Session dependency in `orchestrator/session.py` (or wherever the signed-in user is resolved) calls `bind(user_id=...)`.

- [ ] **Step 1: Write failing tests** in `tests/test_logs_context.py`:
  - `test_http_sets_and_returns_request_id`: app client GET `/api/health` with `X-Request-Id: abc123` → response header `abc123`; without header → 12-hex header.
  - `test_bad_request_id_replaced`: header of 300 `a`s, and `"x\ny"`, and `"../../etc"` → response header is a fresh 12-hex id; captured `http_request` event uses it.
  - `test_grpc_carries_request_id`: in-process catalog service (`tests/harness.start`), call through `aio_channel` inside `context(request_id="rid-1", turn_id="t-1")`; captured server-side `grpc_call` event has `request_id=="rid-1"`, `turn_id=="t-1"`, `code=="OK"`.
  - `test_grpc_error_logged_as_warning`: GetProduct for a missing id → `grpc_call` with `code=="NOT_FOUND"`, `log_level=="warning"`.
  - `test_context_isolated_between_concurrent_calls`: two concurrent gRPC calls bound with different `request_id`/`user_id` → each server event carries its own pair.
  - `test_chat_turn_events_carry_turn_id`: POST `/api/chat` with a fake LLM; every captured event from the turn (`llm_call`, `grpc_call`, the turn log) has the same `turn_id` and the POST's `request_id`.
- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/test_logs_context.py -q` → FAIL.
- [ ] **Step 3:** Implement per Interfaces. Register `LogContextInterceptor()` first in `grpc.aio.server(interceptors=[LogContextInterceptor(), auth])` so denied calls are logged too. Convert the existing `"llm call model=..."` log in `orchestrator/llm.py` to `log.info("llm_call", model=..., key_index=..., prompt_tokens=..., completion_tokens=..., ms=..., retry=...)`.
- [ ] **Step 4:** Run the new tests → PASS; full suite → no new failures; `scripts/ci_test_coverage.py` → 0.
- [ ] **Step 5:** Commit "Logging: request/turn ids across HTTP and gRPC, access logs"; push `logging/02-context`; wait for CI green.

### Task 3: Service audit events (branch `logging/03-audit-services`, from 02)

**Files:**
- Modify: `services/customer/server.py` (Authenticate, CreateUser, SetCartLine, ClearCart), `services/order/{server,saga,cancel,fulfilment}.py`, `services/payment/server.py` (Charge, Refund), `services/inventory/server.py` (Reserve, Commit, Release, Restock), `services/assistant/server.py` (CreateConversation, RenameConversation, DeleteConversation, ApplyFacts, ClearTemporalFacts)
- Test: `tests/test_logs_audit_services.py`

**Interfaces:**
- Consumes: `audit`, `bound` from Task 1; `turn_id` binding from Task 2.
- Produces (event → required fields; `user_id` comes from context):
  - customer: `login_ok` (email), `login_failed` (email, reason), `signup` (email), `cart_line_set` (sku_id, qty_before, qty_after, source), `cart_line_removed` (sku_id, qty_before, source), `cart_cleared` (lines, source). `source = "assistant" if bound().get("turn_id") else "web"`.
  - order: `checkout_started` (order_id, lines, total_cents), `order_placed`, `order_confirmed`, `order_failed` (reason), `cancel_requested`, `order_cancelled` (order_id, reason) — emitted where the status change is committed, once per change.
  - payment: `payment_charged` / `payment_failed` (order_id, amount_cents, method, reason), `refund_issued` / `refund_failed`.
  - inventory: `stock_reserved`, `stock_committed`, `stock_released` (order_id, skus), `restock` (sku_id, qty).
  - assistant: `conversation_created|renamed|deleted` (conversation_id, title), `facts_changed` (added/replaced/removed: lists of `{fact_key, value, kind, category}`), `temporal_facts_cleared` (categories, count).

- [ ] **Step 1: Write failing tests** in `tests/test_logs_audit_services.py`, each driving the real in-process service (harness) inside `capture_logs()`:
  - `test_checkout_success_audit`: successful checkout → events in order include `checkout_started`, `stock_reserved`, `payment_charged`, `order_placed`/`order_confirmed`, each with the same `order_id`; amounts in cents match the order total.
  - `test_checkout_payment_failure_audit`: `method="card_decline"` (whatever the payment service's decline method is, see `tests/test_payment.py`) → `payment_failed`, `stock_released`, `order_failed`.
  - `test_cancel_audit`: cancel a confirmed order → `cancel_requested`, `order_cancelled`, `refund_issued`.
  - `test_cart_audit_source`: SetCartLine qty 0→2 → `cart_line_set` with `qty_before==0, qty_after==2, source=="web"`; same under `context(turn_id="t1")` → `source=="assistant"`.
  - `test_login_failed_never_logs_password`: Authenticate wrong password → `login_failed` with email; the password string is not in any captured value.
  - `test_facts_changed_audit`: ApplyFacts add `liked_colors=navy` then replace `size_top` → `facts_changed` with `fact_key` entries; ClearTemporalFacts → `temporal_facts_cleared` with count.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Add the `audit(...)` calls at the commit points listed in Interfaces.
- [ ] **Step 4:** Run new tests → PASS; full suite → no new failures.
- [ ] **Step 5:** Commit "Logging: audit events in services"; push `logging/03-audit-services`; wait for CI green.

### Task 4: Orchestrator audit and replayable assistant turns (branch `logging/04-audit-assistant`, from 03)

**Files:**
- Modify: `orchestrator/routes_auth.py` (rate-limit hit → `login_rate_limited` (email, address)), `orchestrator/routes_chat.py` (`proposal_confirmed`, `proposal_dismissed`), `orchestrator/agent/loop.py` (proposal emit in `run_tool` → `proposal_created`; turn trace; replace `log.info("chat turn ...")`), `orchestrator/agent/memory.py` (`memory_update`)
- Test: `tests/test_logs_assistant.py`

**Interfaces:**
- Consumes: `audit`, `get_logger`, `context` (Tasks 1–2).
- Produces:
  - `proposal_created|confirmed|dismissed` (proposal_id, action, args, summary).
  - In `AgentLoop`: a per-turn list `trace: list[dict]`, appended in `run_tool` and `fallback` with `{"name", "args", "result": (out["final"] or out["text"])[:300], "ms", "error": bool}`; repeated calls appended with `"result": "repeated"`.
  - One `audit("assistant_turn", conversation_id, turn_id, message, tools=trace, facts_recalled=[keys], reply=text, llm_calls, tokens, ms, outcome)` at the end of the turn, where `outcome` ∈ `{"ok","llm_down","max_steps","rules"}`. `facts_recalled` = keys returned by `recall_facts` calls plus keys included by the keyword pass.
  - `memory_update` (INFO, not audit): conversation_id, summary_words, facts_proposed (count).

- [ ] **Step 1: Write failing tests** in `tests/test_logs_assistant.py` (scripted fake LLM, like `tests/test_agent_chat.py`):
  - `test_turn_event_replays_tools`: script = search(category="headphones", brand="Mirelle") then reply text → one `assistant_turn` with `tools[0].name=="search"`, `tools[0].args=={"category":"headphones","brand":"Mirelle"}`, non-empty `result`, `reply` equal to the sent text, `llm_calls==2`, `outcome=="ok"`.
  - `test_turn_event_on_llm_down`: fake LLM raises `LLMError` → `assistant_turn` with `outcome=="llm_down"` (or `"rules"` when the planner answered).
  - `test_result_truncated`: a tool result of 1000 chars → `len(result) <= 300`.
  - `test_proposal_audit`: checkout tool → `proposal_created(action="checkout_redirect")`; POST confirm → `proposal_confirmed` with same `proposal_id`; dismiss path → `proposal_dismissed`.
  - `test_recalled_facts_listed`: recall_facts(keys=["size_top"]) → `facts_recalled` contains `"size_top"`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement per Interfaces.
- [ ] **Step 4:** Run new tests → PASS; full suite → no new failures; existing token-budget tests still pass.
- [ ] **Step 5:** Commit "Logging: proposal audit and replayable assistant turns"; push `logging/04-audit-assistant`; wait for CI green.

### Task 5: Viewer, Docker and docs (branch `logging/05-viewer`, from 04)

**Files:**
- Create: `scripts/logs.py`, `tests/fixtures/logs/` (small fixture tree, or build it in the test with `tmp_path`)
- Modify: `docker-compose.yml` (each app service: `LOG_DIR=/app/logs`, `TZ=${TZ:-UTC}`, bind mount `./logs:/app/logs`), `README.md` (short "Logs" section: where files are, env vars, viewer examples)
- Test: `tests/test_logs_viewer.py`

**Interfaces:**
- Produces: `scripts/logs.py` CLI, stdlib only, with importable `load(log_dir: Path, *, audit_only: bool) -> Iterator[dict]`, `matches(event: dict, args: argparse.Namespace) -> bool`, `format_line(event: dict) -> str`, `format_turn(event: dict) -> str`, `main(argv: list[str] | None = None) -> int`.
- Flags: `--dir` (default `LOG_DIR` or `<repo>/logs`), `--service`, `--level` (minimum: debug<info<warning<error<critical), `--audit`, `--user`, `--request`, `--turn`, `--order` (matches `order_id`), `--event` (exact or `prefix*`), `--since`/`--until` (`30m`, `2h`, `1d`, or ISO), `--grep TEXT` (case-insensitive over the raw line), `--json`, `--follow` (poll every 1 s for appended lines and new files), `--limit N` (last N, default 200; 0 = all).
- Output line: `HH:MM:SS service LEVEL event key=value …` (key fields first: user_id, order_id, turn_id, request_id). `--turn <id>` prints `format_turn` for the `assistant_turn` event: message, each tool `name(args) -> result`, reply, `llm_calls / tokens / ms`, then the other events of that turn as lines.
- Merge: events from all files sorted by `ts`; when both a service file and the audit file hold the same audit event, show it once (skip audit-file copies unless `--audit`).

- [ ] **Step 1: Write failing tests** in `tests/test_logs_viewer.py` on a `tmp_path` tree (2 services, 2 dates, audit files):
  - `test_merges_by_time_across_services`, `test_filter_user_and_level`, `test_filter_since_relative` (fixed now), `test_event_prefix`, `test_audit_not_duplicated`, `test_turn_block` (contains message, `search(`, reply, `tokens`), `test_skips_bad_lines` (truncated last line + `not json` line → other events still printed, exit 0), `test_json_output_is_raw`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `scripts/logs.py`; update `docker-compose.yml` and `README.md`.
- [ ] **Step 4:** Run new tests → PASS; `docker compose config -q` → exit 0; full suite → no new failures.
- [ ] **Step 5:** Manual: start stack, sign in, add to cart, chat one turn, checkout, cancel; `python scripts/logs.py --audit --since 10m` shows the chain; `--turn <id>` replays the turn; `grep -r "gsk_\|:.*@localhost" logs/` finds no secrets.
- [ ] **Step 6:** Commit "Logging: viewer script, Docker log dir, README"; push `logging/05-viewer`; wait for CI green.
- [ ] **Step 7:** Open PR `logging/05-viewer` → main (contains all 5 commits), wait for all workflows green, merge, confirm main CI green.
