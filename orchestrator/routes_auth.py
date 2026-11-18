"""Demo sign-in: pick a user, get a signed token. `x-user-id` is forwarded to services by the routes that need it."""
from fastapi import APIRouter, Depends

from orchestrator import serialize
from orchestrator.clients import Clients
from orchestrator.deps import current_user_id, get_clients, get_tokens
from orchestrator.errors import ApiError
from orchestrator.schemas import MeOut, SessionIn, SessionOut, UsersOut
from orchestrator.session import SessionTokens
from shopassist_proto import shopassist_pb2 as pb

router = APIRouter()


@router.get("/users", response_model=UsersOut)
async def list_users(clients: Clients = Depends(get_clients)):
    reply = await clients.call("customer", clients.customer.ListUsers, pb.ListUsersRequest())
    return {"users": [serialize.user(u) for u in reply.users]}


@router.post("/session", response_model=SessionOut)
async def create_session(body: SessionIn, clients: Clients = Depends(get_clients), tokens: SessionTokens = Depends(get_tokens)):
    u = await clients.call("customer", clients.customer.GetUser, pb.GetUserRequest(user_id=body.user_id))
    return {"token": tokens.issue(u.user_id), "user": serialize.user(u)}


@router.get("/me", response_model=MeOut)
async def me(user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)):
    try:
        u = await clients.call("customer", clients.customer.GetUser, pb.GetUserRequest(user_id=user_id), user_id=user_id)
    except ApiError as e:
        if e.code == "not_found":  # a valid token for a user that no longer exists is just not a session
            raise ApiError("unauthenticated", "That user no longer exists; sign in again.") from e
        raise
    return {"user": serialize.user(u)}
