"""Request id and one access line per HTTP request (pure ASGI, so it also sees errors raised inside the app)."""
import logging
import time

from shared_common.common.log import set_request_id, valid_request_id

log = logging.getLogger(__name__)
REQUEST_ID_HEADER = "x-request-id"


def _user_of(scope) -> str:
    """The signed-in user's id when the request carries a valid bearer token, else '-'."""
    app = scope.get("app")
    tokens = getattr(getattr(app, "state", None), "tokens", None)
    if tokens is None:
        return "-"
    for key, value in scope.get("headers") or ():
        if key == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer":
                try:
                    return tokens.verify(token.strip()) or "-"
                except Exception:  # noqa: BLE001 - logging must never break a request
                    return "-"
    return "-"


class RequestLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = next((v.decode("latin-1") for k, v in scope.get("headers") or () if k == REQUEST_ID_HEADER.encode()), None)
        rid = set_request_id(valid_request_id(incoming))
        method, path = scope["method"], scope["path"]
        started = time.perf_counter()
        status = 500

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", ()), (b"x-request-id", rid.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            log.exception("unhandled %s %s", method, path)
            raise
        finally:
            ms = int((time.perf_counter() - started) * 1000)
            level = logging.ERROR if status >= 500 else logging.DEBUG if path == "/api/health" else logging.INFO
            log.log(level, "%s %s -> %d %dms user=%s", method, path, status, ms, _user_of(scope))
