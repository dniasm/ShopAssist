"""Fact rules, kept free of I/O: which keys hold one value, how a batch of changes turns into inserts and deletes,
and how stored facts are ranked against a query. Facts are plain dicts (the columns of the facts table)."""
import math
import re
import secrets
from datetime import datetime, timedelta, timezone

SINGLE_KEYS = {"preferred_name", "size_top", "size_bottom", "shoe_size", "height", "weight", "budget", "favorite_color", "gender_fit"}
ALIASES = {"size": "size_top", "name": "preferred_name"}
TEMPORAL_DAYS = 30
_TOKEN = re.compile(r"[a-z0-9]+")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalise_key(key: str) -> str:
    """snake_case, 'favourite' spelled 'favorite', and the short aliases ('size', 'name') mapped to their full key."""
    key = "_".join(_TOKEN.findall(key.lower()))
    key = "_".join("favorite" if part == "favourite" else part for part in key.split("_"))
    return ALIASES.get(key, key)


def is_single(key: str) -> bool:
    """A key that holds one value (a new one replaces it); every other key collects values."""
    return key in SINGLE_KEYS or key.startswith(("favorite_", "preferred_"))


def new_fact_id() -> str:
    return "F-" + secrets.token_hex(6)


def _same(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def plan_changes(existing: list[dict], changes: list[dict], now: datetime | None = None) -> tuple[list[dict], list[str]]:
    """Apply `changes` in order to a copy of `existing`; return (rows to insert, ids of existing facts to delete).

    remember on a single key replaces it; on a list key it adds unless that value is already there (any case).
    forget with value '*' removes the whole key; with a value, only the facts holding it. A temporal fact expires
    TEMPORAL_DAYS after `now`. A fact added and forgotten within one batch never reaches the database.
    """
    now = now or utcnow()
    working = [dict(f) for f in existing]
    deleted: list[str] = []
    inserted: list[dict] = []

    def remove(match) -> None:
        for f in [f for f in working if match(f)]:
            working.remove(f)
            if f in inserted:
                inserted.remove(f)
            else:
                deleted.append(f["fact_id"])

    for change in changes:
        key = normalise_key(change["key"])
        value = change.get("value", "").strip()
        if change["op"] == "forget":
            remove(lambda f: f["key"] == key and (value == "*" or _same(f["value"], value)))
            continue
        if is_single(key):
            remove(lambda f: f["key"] == key)
        elif any(f["key"] == key and _same(f["value"], value) for f in working):
            continue
        temporal = change.get("kind") == "temporal"
        row = {
            "fact_id": new_fact_id(), "kind": "temporal" if temporal else "permanent", "key": key, "value": value,
            "category": change.get("category", "").strip().lower(), "embedding": list(change.get("embedding") or []) or None,
            "expires_at": now + timedelta(days=TEMPORAL_DAYS) if temporal else None,
        }
        working.append(row)
        inserted.append(row)
    return inserted, deleted


def is_expired(fact: dict, now: datetime) -> bool:
    return fact.get("expires_at") is not None and fact["expires_at"] <= now


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def keyword_score(fact: dict, query_text: str) -> float:
    """Token Jaccard between the query and the fact's key and value."""
    a, b = _tokens(query_text), _tokens(fact["key"] + " " + fact["value"])
    return len(a & b) / len(a | b) if a and b else 0.0


def rank(facts: list[dict], query_text: str, query_embedding: list[float] | None, limit: int, min_score: float,
         now: datetime | None = None) -> list[dict]:
    """Best `limit` live facts scoring at least `min_score` (and above zero), best first, each with its `score`.

    Cosine when the query and the fact both have an embedding of the same size; otherwise keyword overlap.
    Expired temporal facts are never returned.
    """
    now = now or utcnow()
    scored = []
    for f in facts:
        if is_expired(f, now):
            continue
        emb = f.get("embedding")
        if query_embedding and emb and len(emb) == len(query_embedding):
            score = cosine(query_embedding, emb)
        else:
            score = keyword_score(f, query_text)
        if score > 0 and score >= min_score:
            scored.append({**f, "score": score})
    scored.sort(key=lambda f: (-f["score"], f["created_at"] if f.get("created_at") else now, f["fact_id"]))
    return scored[:limit]
