"""Caller identity. The orchestrator verifies the session token, then forwards the user as `x-user-id` metadata."""
import grpc

USER_HEADER = "x-user-id"


async def require_user(context: grpc.aio.ServicerContext) -> str:
    """The caller's user id, or abort the call with UNAUTHENTICATED when the header is absent or blank."""
    for key, value in context.invocation_metadata() or ():
        if key == USER_HEADER and value.strip():
            return value.strip()
    await context.abort(grpc.StatusCode.UNAUTHENTICATED, f"missing {USER_HEADER} metadata")
