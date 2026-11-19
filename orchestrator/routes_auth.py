"""Sign-in, sign-up and the signed-in user. Passwords are checked and stored by the Customer service (salted PBKDF2);
this layer throttles wrong passwords (login_limits.py), checks the optional invite code and issues the session token
that every other route and the WebSocket accept. `x-user-id` is forwarded to services by the routes that need it.

Login flow and rules come from day7's accounts (backend/library/auth.py, backend/api/routes/auth.py).
"""
import hmac

from fastapi import APIRouter, Depends, Request

from orchestrator import serialize
from orchestrator.clients import Clients
from orchestrator.deps import current_user_id, get_clients, get_tokens
from orchestrator.errors import ApiError
from orchestrator.schemas import MeOut, SessionIn, SessionOut, SignUpIn, SignupsOut
from orchestrator.session import SessionTokens
from shopassist_proto import shopassist_pb2 as pb

router = APIRouter()
TOO_MANY = "Too many wrong tries. Wait a few minutes and try again."


def _address(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("/signups", response_model=SignupsOut)
async def signups(request: Request):
    """Whether creating an account needs the invite code (SIGNUP_CODE)."""
    return {"signups": "invite" if request.app.state.settings.signup_code else "open"}


@router.post("/session", response_model=SessionOut)
async def create_session(body: SessionIn, request: Request, clients: Clients = Depends(get_clients),
                         tokens: SessionTokens = Depends(get_tokens)):
    """Sign in with email and password. A wrong email and a wrong password get the same answer."""
    limits, address, email = request.app.state.login_limits, _address(request), body.email.strip()
    if limits.blocked(email, address):
        raise ApiError("rate_limited", TOO_MANY)
    try:
        u = await clients.call("customer", clients.customer.Authenticate, pb.AuthenticateRequest(email=email, password=body.password))
    except ApiError as e:
        if e.code == "unauthenticated":
            limits.failed(email, address)
            raise ApiError("unauthenticated", "Wrong email or password.") from e
        raise
    return {"token": tokens.issue(u.user_id), "user": serialize.user(u)}


@router.post("/signup", response_model=SessionOut, status_code=201)
async def sign_up(body: SignUpIn, request: Request, clients: Clients = Depends(get_clients),
                  tokens: SessionTokens = Depends(get_tokens)):
    """Create an account and sign in as it."""
    code, limits, address = request.app.state.settings.signup_code, request.app.state.login_limits, _address(request)
    if code:
        if limits.address_blocked(address):
            raise ApiError("rate_limited", TOO_MANY)
        if not hmac.compare_digest(body.invite.strip().encode(), code.encode()):
            limits.failed("", address)  # wrong invite codes count against the address, like wrong passwords
            raise ApiError("forbidden", "That invite code isn't right.")
    request_ = pb.CreateUserRequest(name=body.name.strip(), email=body.email.strip(), password=body.password, city=body.city.strip())
    try:
        u = await clients.call("customer", clients.customer.CreateUser, request_)
    except ApiError as e:
        if e.code == "conflict":
            raise ApiError("conflict", "An account with that email already exists. Sign in instead.") from e
        raise
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
