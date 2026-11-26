"""Caller identity. The orchestrator verifies the session token, then forwards the user as `x-user-id` metadata."""
import grpc

USER_HEADER = "x-user-id"


def metadata_value(context: grpc.aio.ServicerContext, name: str, default: str = "") -> str:
    """One metadata value of the call (the last one if repeated), or `default`."""
    found = default
    for key, value in context.invocation_metadata() or ():
        if key == name and value.strip():
            found = value.strip()
    return found


def user_field(context: grpc.aio.ServicerContext) -> dict:
    """`{"user": id}` when the caller sent one, else `{}` - for splatting into an audit line."""
    user = metadata_value(context, USER_HEADER)
    return {"user": user} if user else {}


async def require_user(context: grpc.aio.ServicerContext) -> str:
    """The caller's user id, or abort the call with UNAUTHENTICATED when the header is absent or blank."""
    for key, value in context.invocation_metadata() or ():
        if key == USER_HEADER and value.strip():
            return value.strip()
    await context.abort(grpc.StatusCode.UNAUTHENTICATED, f"missing {USER_HEADER} metadata")
