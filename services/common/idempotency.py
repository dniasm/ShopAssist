"""Idempotency keys must mean "the same request", not just "the same string".

A client bug that reuses a key for a different request would otherwise be told "done" (and believe it
holds stock or has paid) for something that never happened. Every idempotent write stores a fingerprint of
its request next to the key; a replay with another fingerprint is refused with ALREADY_EXISTS.
"""
import hashlib
import json


class KeyReused(Exception):
    """The key exists but was first used for a different request. Servers map this to ALREADY_EXISTS."""


def fingerprint(request: object) -> str:
    """sha256 of a canonical JSON form. Callers pass normalised data (e.g. merged, sorted lines) so equal
    requests hash equal regardless of line order."""
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def check_replay(key: str, stored: str | None, current: str) -> None:
    """Raise KeyReused when a stored fingerprint differs. Rows from before fingerprints existed (None) pass."""
    if stored is not None and stored != current:
        raise KeyReused(f"idempotency key reused with a different request: {key}")
