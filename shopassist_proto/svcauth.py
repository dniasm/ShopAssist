"""Service-to-service authentication for gRPC: who is calling, and may they call this service?

Every caller identity (orchestrator, order) has its own secret in `.env` (SVC_KEY_<CALLER>). A client attaches two
metadata entries to each call:

    x-svc-caller: orchestrator
    x-svc-token:  <expiry>.<hex HMAC-SHA256 over "caller\\naudience\\nmethod\\nexpiry" with the caller's key>

The server (`AuthInterceptor`, installed by `services.common.server.Service`) checks the signature with a constant-time
compare, the expiry, that the audience is itself, and that CALLERS allows this caller to reach this service. It runs before
any servicer code, so `x-user-id` is never read from a call that is not authenticated. Only `grpc.health.v1.Health/Check`
is open. This is shared-secret authentication between processes on one host; it is not transport encryption.
"""
import hashlib
import hmac
import os
import re
import time
from collections.abc import Mapping

import grpc

CALLER_HEADER = "x-svc-caller"
TOKEN_HEADER = "x-svc-token"
TOKEN_TTL_SECONDS = 30  # how long a freshly signed token is valid
MAX_LIFETIME_SECONDS = 60  # the server rejects an expiry further out than this (and any in the past)
HEALTH_CHECK_METHOD = "/grpc.health.v1.Health/Check"

# caller -> services it may call. The Order saga reads prices from the catalog, so `order` may reach it.
CALLERS: dict[str, tuple[str, ...]] = {
    "orchestrator": ("catalog", "inventory", "order", "customer", "payment", "assistant"),
    "order": ("catalog", "inventory", "payment"),
}
KEY_VARS = {caller: f"SVC_KEY_{caller.upper()}" for caller in CALLERS}
_SERVICE_IN_METHOD = re.compile(r"^/shopassist\.v2\.(\w+?)Service/")


def load_key(caller: str, env: Mapping[str, str] | None = None) -> bytes:
    """The caller's secret from the environment; RuntimeError (naming the variable, never a value) when it is missing."""
    var = KEY_VARS[caller]
    value = (env if env is not None else os.environ).get(var, "").strip()
    if not value:
        raise RuntimeError(f"{var} is not set: run `python scripts/db_setup.py` to generate the service keys in .env")
    return value.encode()


def audience_of(method: str) -> str | None:
    """'/shopassist.v2.CatalogService/GetSkus' -> 'catalog' (None for anything that is not one of our services)."""
    m = _SERVICE_IN_METHOD.match(method)
    return m.group(1).lower() if m else None


def sign(key: bytes, caller: str, audience: str, method: str, expiry: int) -> str:
    mac = hmac.new(key, f"{caller}\n{audience}\n{method}\n{expiry}".encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{mac}"


def auth_metadata(key: bytes, caller: str, audience: str, method: str, now: float | None = None) -> tuple[tuple[str, str], tuple[str, str]]:
    expiry = int((time.time() if now is None else now) + TOKEN_TTL_SECONDS)
    return ((CALLER_HEADER, caller), (TOKEN_HEADER, sign(key, caller, audience, method, expiry)))


def signed_metadata(details, key: bytes, caller: str, audience: str | None) -> tuple[tuple[str, str], ...]:
    """The call's own metadata (e.g. x-user-id) plus the auth entries. A method that is not one of ours gets no token."""
    method = details.method.decode() if isinstance(details.method, bytes) else details.method
    aud = audience or audience_of(method)
    extra = auth_metadata(key, caller, aud, method) if aud else ()
    return tuple(details.metadata or ()) + extra


class _Signer:
    def __init__(self, caller: str, key: bytes, audience: str | None = None):
        self._caller, self._key, self._audience = caller, key, audience

    def _details(self, details: grpc.aio.ClientCallDetails) -> grpc.aio.ClientCallDetails:
        metadata = signed_metadata(details, self._key, self._caller, self._audience)
        return grpc.aio.ClientCallDetails(details.method, details.timeout, grpc.aio.Metadata(*metadata), details.credentials, details.wait_for_ready)


# grpc.aio registers an interceptor object under the FIRST call type it is an instance of (an if/elif chain), so one class
# deriving from both would sign unary calls and silently skip streams. One class per call type; the proto has no client streams.
class AioUnaryAuth(_Signer, grpc.aio.UnaryUnaryClientInterceptor):
    async def intercept_unary_unary(self, continuation, client_call_details, request):
        return await continuation(self._details(client_call_details), request)


class AioStreamAuth(_Signer, grpc.aio.UnaryStreamClientInterceptor):
    async def intercept_unary_stream(self, continuation, client_call_details, request):
        return await continuation(self._details(client_call_details), request)


def aio_channel(address: str, caller: str, audience: str | None = None, key: bytes | None = None) -> grpc.aio.Channel:
    """An insecure grpc.aio channel whose calls are signed as `caller` (key from the environment unless given)."""
    key = key if key is not None else load_key(caller)
    return grpc.aio.insecure_channel(address, interceptors=[AioUnaryAuth(caller, key, audience), AioStreamAuth(caller, key, audience)])


class AuthInterceptor(grpc.aio.ServerInterceptor):
    """Server side: reject a call before any servicer sees it unless it carries a valid token from an allowed caller."""

    def __init__(self, service: str, keys: Mapping[str, bytes], clock=time.time):
        self._service, self._keys, self._clock = service, dict(keys), clock

    @classmethod
    def for_service(cls, service: str, env: Mapping[str, str] | None = None) -> "AuthInterceptor":
        """Load every caller's key (so a known caller that is not allowed here is told apart from a forger).
        A missing key stops the service from starting: it fails closed."""
        return cls(service, {caller: load_key(caller, env) for caller in CALLERS})

    def _check(self, method: str, metadata) -> tuple[grpc.StatusCode, str] | None:
        """None when the call is allowed, else (status, message). The message never says which part was wrong."""
        md = {k: v for k, v in (metadata or ()) if isinstance(v, str)}
        caller, token = md.get(CALLER_HEADER, ""), md.get(TOKEN_HEADER, "")
        key = self._keys.get(caller)
        expiry_text = token.partition(".")[0]
        if key is None or not expiry_text.isdigit():
            return grpc.StatusCode.UNAUTHENTICATED, "missing or invalid service credentials"
        expiry = int(expiry_text)
        expected = sign(key, caller, self._service, method, expiry)  # the audience is us, whatever the token claims
        if not hmac.compare_digest(expected.encode(), token.encode()):
            return grpc.StatusCode.UNAUTHENTICATED, "missing or invalid service credentials"
        now = self._clock()
        if expiry < now or expiry > now + MAX_LIFETIME_SECONDS:
            return grpc.StatusCode.UNAUTHENTICATED, "expired service credentials"
        if self._service not in CALLERS[caller]:  # authentic, but not allowed to reach this service
            return grpc.StatusCode.PERMISSION_DENIED, f"{caller} may not call {self._service}"
        return None

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None or handler_call_details.method == HEALTH_CHECK_METHOD:
            return handler
        denied = self._check(handler_call_details.method, handler_call_details.invocation_metadata)
        return handler if denied is None else _denying_handler(handler, *denied)


def _denying_handler(handler, code: grpc.StatusCode, message: str):
    """A handler of the same shape as `handler` that ends the call with `code` without running any servicer code."""
    async def deny_unary(request, context):
        await context.abort(code, message)

    async def deny_stream(request, context):
        await context.abort(code, message)
        yield  # makes this an async generator, as a streaming handler must be

    if handler.request_streaming:
        make = grpc.stream_stream_rpc_method_handler if handler.response_streaming else grpc.stream_unary_rpc_method_handler
    else:
        make = grpc.unary_stream_rpc_method_handler if handler.response_streaming else grpc.unary_unary_rpc_method_handler
    return make(deny_stream if handler.response_streaming else deny_unary)
