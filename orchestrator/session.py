"""Demo session tokens: a signed, expiring `{user_id}` (no passwords in this app).

Why itsdangerous: the orchestrator is stateless about sessions, so a token must carry and prove its own user id.
"""
from itsdangerous import BadSignature, URLSafeTimedSerializer

from orchestrator.errors import ApiError


class SessionTokens:
    def __init__(self, secret: str, max_age_seconds: int):
        self._serializer = URLSafeTimedSerializer(secret, salt="shopassist.session")
        self._max_age = max_age_seconds

    def issue(self, user_id: str) -> str:
        return self._serializer.dumps({"user_id": user_id})

    def verify(self, token: str) -> str:
        """The user id inside a valid, unexpired token; otherwise 401 `unauthenticated`."""
        try:
            data = self._serializer.loads(token, max_age=self._max_age)
            return data["user_id"]
        except (BadSignature, KeyError, TypeError) as e:  # SignatureExpired is a BadSignature
            raise ApiError("unauthenticated", "Sign in again: the session is missing, invalid or expired.") from e
