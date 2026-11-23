# Auth hardening: factual change notes (branch `feat/auth-hardening`)

Two commits: (1) service-to-service auth on gRPC, (2) WebSocket first-message auth. Line numbers are as of the branch head.

## Task 1: gRPC service-to-service auth

**Problem.** Any local process could call ports 50051-50055 and set `x-user-id` to act as any user.

**Design.** One secret per *caller identity* (`SVC_KEY_ORCHESTRATOR`, `SVC_KEY_ORDER`) in `.env`. Every call (except health Check) carries
`x-svc-caller: <caller>` and `x-svc-token: <expiry>.<hex HMAC-SHA256(key, "caller\naudience\nmethod\nexpiry")>`. Tokens live 30 s;
the server rejects an expiry in the past or more than 60 s ahead. Shared-secret auth on one host, no mTLS, no encryption.

| File | What / why |
|---|---|
| `shopassist_proto/svcauth.py` (new) | Everything shared. Lives in `shopassist_proto` (not `services/common`) because `tests/test_isolation.py` forbids the orchestrator importing `services.*`. |
| `svcauth.py:28-31` `CALLERS` | Allowlist: `orchestrator` -> catalog, inventory, order, customer, payment; `order` -> catalog, inventory, payment. |
| `svcauth.py:38` `load_key` | Reads `SVC_KEY_<CALLER>`; missing -> `RuntimeError` naming the variable only (never a value). |
| `svcauth.py:53-69` `sign`, `auth_metadata`, `signed_metadata` | Build the token; the call's own metadata (`x-user-id`) is preserved. Audience defaults to the service in the method path. |
| `svcauth.py:71-97` `AioUnaryAuth`, `AioStreamAuth`, `aio_channel` | grpc.aio client interceptors; `aio_channel(addr, caller, audience)` is the only way the code opens a service channel. |
| `svcauth.py:98-135` `AuthInterceptor` | Server interceptor: `_check` then `intercept_service`. |
| `services/common/server.py:30-35` | `Service.__init__` installs `AuthInterceptor.for_service(name)`: every service has it, and a missing key stops startup (fail closed). |
| `orchestrator/clients.py:33-34` | Orchestrator channels are signed with the orchestrator key, audience = target service. |
| `services/order/server.py:193` | Order -> catalog/inventory/payment channels signed as `order`. |
| `scripts/db_setup.py:43,193` | Generates both keys into `.env` when missing (idempotent, never printed). `--reset` leaves existing keys alone. |
| `scripts/run_all.py:60` | Missing keys count like missing DB URLs: tells you to run `db_setup.py`. |
| `.env.example` | `SVC_KEY_ORCHESTRATOR=`, `SVC_KEY_ORDER=` (empty). |

**Server check order (`_check`).** (1) caller header names a known caller and the token starts with digits, else UNAUTHENTICATED;
(2) recompute the HMAC with *this service's own name as audience* and `hmac.compare_digest` it, else UNAUTHENTICATED (so a wrong key, wrong audience,
changed method or forged caller all look the same); (3) expiry window, else UNAUTHENTICATED; (4) caller allowed to reach this service, else
PERMISSION_DENIED. A signature is verified before the allowlist so an unauthenticated stranger cannot probe who may call what.
`grpc.health.v1.Health/Check` is the only open method. Because the interceptor runs before the servicer, `require_user` (`x-user-id`) is never reached
by an unauthenticated call. Rejection returns a handler of the same shape (unary or stream) that aborts, so streaming RPCs are covered.

```python
# svcauth.py
def sign(key, caller, audience, method, expiry):
    mac = hmac.new(key, f"{caller}\n{audience}\n{method}\n{expiry}".encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{mac}"
# AuthInterceptor._check
expected = sign(key, caller, self._service, method, expiry)  # the audience is us, whatever the token claims
if not hmac.compare_digest(expected.encode(), token.encode()): return UNAUTHENTICATED
...
if self._service not in CALLERS[caller]: return PERMISSION_DENIED
```

**Deviations from the brief.**
- `order` is also allowed to call **catalog**: the saga's validate step calls `catalog.GetSkus` (`services/order/saga.py:101`); denying it would break checkout. The "denied" test uses order -> customer.
- Every service loads *all* caller keys (not only the allowed ones) so a genuine but disallowed caller gets PERMISSION_DENIED instead of UNAUTHENTICATED. The keys share one `.env` already.

**Gotcha found.** grpc.aio registers a client interceptor under the *first* call type it is an instance of (`if/elif` in `grpc/aio/_channel.py:393-400`).
One class deriving from UnaryUnary and UnaryStream interceptors signed unary calls and silently skipped streams (Checkout, WatchOrders). Hence two classes. The proto has no client-streaming RPCs.

**Tests.** `tests/test_service_auth.py` (15): no token, caller without token, wrong key, expired, valid for >60 s, wrong audience, wrong method, unknown caller / garbage tokens,
order -> customer PERMISSION_DENIED, order -> catalog/inventory ok, valid call with `x-user-id`, `x-user-id` alone rejected, health without token,
stream rejected without token / accepted with one, missing key stops startup without leaking a value.
Existing tests: `tests/authchan.py` gives signed sync/aio channels (orchestrator by default); every direct `grpc.insecure_channel` in tests uses it (`tests/helpers.py`, `test_*.py`).
`tests/conftest.py:24-25` sets fixed test keys, never the ones in `.env`.

## Task 2: WebSocket auth without the query token

**Problem.** `/api/ws?token=<session token>` put the credential in URLs (server/proxy logs, browser history).

**Protocol.** Connect to `/api/ws?after=<n>`. Origin checked first. Server accepts, then reads ONE message within `ws_auth_timeout_seconds` (5 s):
`{"type":"auth","token":"..."}` or `{"type":"auth","guest":"<id>"}` (guest id must still match `[A-Za-z0-9_-]{8,64}`). Anything else (timeout, non-JSON, binary, >4096 chars, wrong
type, bad/expired token, bad guest id, unknown user) -> close **4401**. On success: `system/auth_ok` `{guest: bool}` (seq 0), then `hello`, optional `resync`, replay, live stream.
No other client message is ever read before auth; after auth the server still ignores client messages (it only watches for the close).

| File | What / why |
|---|---|
| `orchestrator/routes_ws.py:41` `origin_allowed` | Browser handshakes always carry `Origin`. Allowed: in `ws_allowed_origins`, or its host equals the request `Host` (the SPA served by the orchestrator itself). No `Origin` header (curl, tests, scripts) is let through: not a browser, so no cross-site hijack, and auth is still required. |
| `routes_ws.py:52` `read_auth` | `asyncio.wait_for(websocket.receive_text(), timeout)`, size cap, JSON object with `type == "auth"`. |
| `routes_ws.py:66` `session_socket` | Origin refused -> `close(1008)` before accept (browser sees HTTP 403). `token`/`guest` query params removed. Same verification and GetUser lookup as before, now on the first message. Invalid -> 4401; services down -> 1013 as before. |
| `orchestrator/settings.py:38-41,72-73` | `ws_auth_timeout_seconds` (`WS_AUTH_TIMEOUT_SECONDS`, 5.0), `ws_allowed_origins` (`WS_ALLOWED_ORIGINS`, default `http://localhost:5173`, `http://127.0.0.1:5173`). |
| `orchestrator/ws_schema.py` | New `SystemAuthOk` in the `WsMessage` union, so `GET /api/ws/schema` / openapi carry it. |
| `web/src/api/schema.d.ts` | Regenerated with `openapi-typescript` from the app's `/openapi.json` (only the new schema + description text changed). |
| `web/src/features/session/handshake.ts` (new) | `socketUrl` (only `?after=`), `authFrame(who)`, `isAuthOk`. |
| `web/src/features/session/SessionSocket.tsx:74-84` | `onopen` sends `authFrame`; the socket is reported "open" only when `system/auth_ok` arrives (not delivered to subscribers). 4401 handling in `socketMachine.ts` is unchanged: sign out, no retry. |
| `web/src/features/session/events.ts` | Parses `system/auth_ok`. |
| `loadtest/locustfile.py` | REST only, no WebSocket: unchanged. |

```python
# routes_ws.py
if not origin_allowed(websocket, settings.ws_allowed_origins):
    await websocket.close(code=POLICY_CLOSE, reason="origin not allowed"); return
await websocket.accept()
auth = await read_auth(websocket, settings.ws_auth_timeout_seconds)
if auth is None:
    await websocket.close(code=4401, reason="expected an auth message first"); return
```

**Tests.** `tests/test_api_shop.py`: `Shop.ws()` now sends the auth message and consumes `auth_ok` so every existing WS test is unchanged (`test_api_orders.py`, `test_agent_chat.py` go through it).
New: URL credentials ignored, no auth message -> 4401 after a 0.3 s timeout, bad/tampered/missing/short-guest/non-string/non-JSON/array/oversized first message -> 4401,
non-auth first message is not processed (no auth_ok, nothing subscribed), token auth -> `auth_ok` then `hello`, guest auth works, foreign origins -> 1008 (evil.example, other port, `null`, host-suffix trick),
own origin / dev-server origin / no Origin accepted, schema carries `SystemAuthOk`. Web: `handshake.test.ts` (URL has no credentials, frames, `auth_ok` detection).

## Verification (this branch)
- `pytest -q`: 452 passed (430 before the new tests existed -> +15 service auth, +8 WS tests, one replaced).
- `web`: `npm run typecheck` ok, `vitest run` 27 files / 243 passed / 1 skipped, `npm run build` ok.
- Live (run_all started and stopped by this work): REST sign-in; WS auth -> `auth_ok`, `hello`; bad token -> close 4401; `Origin: https://evil.example` -> HTTP 403;
  direct gRPC to catalog (50051) and customer (50054) with only `x-user-id` -> `UNAUTHENTICATED`.

## Notes
- vitest on this Windows box fails ("Cannot read properties of undefined (reading 'config')") when started from a path with a lower-case drive letter (`d:/...`); run it from `D:/...`. Pre-existing, unrelated.
- Not done / out of scope: transport encryption (mTLS), key rotation tooling (rotate by editing `.env` and restarting), rate limiting of unauthenticated WebSockets beyond the 5 s timeout.
