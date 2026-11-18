"""FastAPI dependencies: shared objects from app.state and the signed-in user."""
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from orchestrator.clients import Clients
from orchestrator.errors import ApiError
from orchestrator.session import SessionTokens

_bearer = HTTPBearer(auto_error=False)  # auto_error off so a missing header uses our error envelope, not FastAPI's


def get_clients(request: Request) -> Clients:
    return request.app.state.clients


def get_tokens(request: Request) -> SessionTokens:
    return request.app.state.tokens


def current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    tokens: SessionTokens = Depends(get_tokens),
) -> str:
    if credentials is None:
        raise ApiError("unauthenticated", "Sign in first: send `Authorization: Bearer <token>`.")
    return tokens.verify(credentials.credentials)
