"""The API error envelope: `{"error": {"code", "message", "details"?}}` with one HTTP status per code.

Why one place: every failure (validation, auth, a downed service, an unknown URL) must look the same to the
frontend, so handlers for all of them are installed here and nothing else builds error bodies.
"""
import logging

import grpc
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from shared_common.common.log import get_request_id

log = logging.getLogger(__name__)

STATUS = {
    "invalid": 422, "unauthenticated": 401, "forbidden": 403, "rate_limited": 429, "not_found": 404, "conflict": 409, "out_of_stock": 409,
    "payment_declined": 402, "unavailable": 503, "internal": 500,
}
GRPC_TO_CODE = {
    grpc.StatusCode.INVALID_ARGUMENT: "invalid",
    grpc.StatusCode.NOT_FOUND: "not_found",
    grpc.StatusCode.UNAUTHENTICATED: "unauthenticated",
    grpc.StatusCode.FAILED_PRECONDITION: "conflict",
    grpc.StatusCode.ABORTED: "conflict",
    grpc.StatusCode.ALREADY_EXISTS: "conflict",
    grpc.StatusCode.UNAVAILABLE: "unavailable",
    grpc.StatusCode.DEADLINE_EXCEEDED: "unavailable",
}


class ApiError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


def from_grpc(err: grpc.aio.AioRpcError, service: str) -> ApiError:
    """Translate a failed gRPC call; the service name goes in `details` so the UI can say who is down."""
    code = GRPC_TO_CODE.get(err.code(), "internal")
    if code == "unavailable":
        return ApiError(code, f"The {service} service is not responding.", {"service": service})
    if code == "internal":
        log.error("%s returned %s: %s", service, err.code().name, err.details())
        return ApiError(code, f"The {service} service failed unexpectedly.", {"service": service})
    return ApiError(code, err.details() or code.replace("_", " "), {"service": service})


def envelope(code: str, message: str, details: dict | None = None, status: int | None = None) -> JSONResponse:
    body: dict = {"code": code, "message": message}
    if details:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status or STATUS[code])


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return envelope(e.code, e.message, e.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        problems = [{"field": ".".join(str(p) for p in err["loc"][1:]), "message": err["msg"]} for err in e.errors()]
        return envelope("invalid", "The request is not valid.", {"problems": problems})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        """Framework-raised errors (unknown URL, wrong method) keep their HTTP status but use our envelope."""
        code = {404: "not_found", 401: "unauthenticated"}.get(e.status_code, "invalid" if e.status_code < 500 else "internal")
        message = "Not found." if e.status_code == 404 else str(e.detail)
        return envelope(code, message, status=e.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, e: Exception):
        log.exception("unhandled %s %s", request.method, request.url.path)  # the one traceback line for this request
        response = envelope("internal", "Something went wrong on our side.")
        response.headers["X-Request-Id"] = get_request_id()  # this response leaves outside RequestLogMiddleware
        return response
