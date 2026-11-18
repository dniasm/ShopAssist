"""Tiny helpers every service needs: ISO timestamps and opaque offset page tokens."""
import base64
import binascii
from datetime import datetime, timezone

DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100


def iso(dt: datetime | None) -> str:
    """ISO-8601 UTC string, which is the timestamp format of the whole API ("" when there is no value)."""
    return dt.astimezone(timezone.utc).isoformat() if dt else ""


def page_size(requested: int) -> int:
    """0 means the default; anything above the cap is clamped rather than rejected."""
    if requested <= 0:
        return DEFAULT_PAGE_SIZE
    return min(requested, MAX_PAGE_SIZE)


def encode_offset(offset: int) -> str:
    """Opaque on purpose: clients must not build tokens, so we can switch to keyset paging later."""
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def decode_offset(token: str) -> int:
    """Offset behind a token; ValueError (to become INVALID_ARGUMENT) for anything we did not issue."""
    if not token:
        return 0
    try:
        kind, _, value = base64.urlsafe_b64decode(token.encode()).decode().partition(":")
        offset = int(value)
    except (binascii.Error, UnicodeDecodeError, ValueError) as e:
        raise ValueError("invalid page_token") from e
    if kind != "o" or offset < 0:
        raise ValueError("invalid page_token")
    return offset
