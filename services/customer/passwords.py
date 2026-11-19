"""Password hashing, taken from day7's accounts (backend/library/auth.py): salted PBKDF2-SHA256, never stored as text.

The stored form is "pbkdf2_sha256$<rounds>$<salt hex>$<digest hex>", so the round count travels with each hash and can
change later without breaking older ones. PASSWORD_ROUNDS lowers it for the test suite only (each check costs CPU).
Hashing is CPU-bound: the servicer runs it in a worker thread so one login never stalls other requests.
"""
import hashlib
import hmac
import os
import secrets

ROUNDS = int(os.environ.get("PASSWORD_ROUNDS") or 600_000)
MIN_PASSWORD, MAX_PASSWORD = 8, 256


def hash_password(password: str, rounds: int = ROUNDS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    """False for a wrong password and for an account without one (stored is empty)."""
    try:
        scheme, rounds, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        mine = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(mine.hex(), digest)
