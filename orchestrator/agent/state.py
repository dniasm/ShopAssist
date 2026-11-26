"""The agent's two small stores: conversation memory (last 6 turns, 1 h, in memory) and proposals (10 min, confirm once; the id is
signed, so an open proposal survives a restart)."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

from orchestrator.errors import ApiError

MAX_TURNS = 6
MEMORY_TTL_SECONDS = 3600
PROPOSAL_TTL_SECONDS = 600


class ConversationMemory:
    """Per user (or guest): the last few turns as {"user", "assistant", "entities"} and when they were last touched."""

    def __init__(self, ttl: float = MEMORY_TTL_SECONDS, clock=time.monotonic):
        self._ttl, self._clock = ttl, clock
        self._turns: dict[str, deque] = {}
        self._touched: dict[str, float] = {}

    def history(self, owner: str) -> list[dict]:
        self._purge()
        return list(self._turns.get(owner, ()))

    def add(self, owner: str, user: str, assistant: str, entities: dict | None = None) -> None:
        self._turns.setdefault(owner, deque(maxlen=MAX_TURNS)).append({"user": user, "assistant": assistant, "entities": entities or {}})
        self._touched[owner] = self._clock()

    def _purge(self) -> None:
        now = self._clock()
        for owner in [o for o, t in self._touched.items() if now - t > self._ttl]:
            self._turns.pop(owner, None)
            self._touched.pop(owner, None)


@dataclass
class Proposal:
    proposal_id: str
    user_id: str
    action: str  # "checkout_redirect" | "cancel_order"
    args: dict
    summary: str
    turn_id: str
    expires_at: float
    state: str = field(default="open")  # open -> confirmed | dismissed


ACTION_CODES = {"checkout_redirect": "c", "cancel_order": "o"}


class ProposalStore:
    """A proposal is what the assistant offers and the person confirms. It lives 10 minutes, belongs to one user and can be
    confirmed exactly once: `claim` is the single place that turns "open" into "confirmed".

    The id carries everything needed to act on it (user, action, args, expiry) and is signed with the server secret, so a proposal
    saved with its chat message can still be confirmed after a reopen or a restart: `_visible` rebuilds it from a valid, unexpired id.
    Only "confirmed/dismissed" lives in memory (until the proposal expires); the order and cart checks in the route cover the rest."""

    def __init__(self, secret: str | None = None, ttl: float = PROPOSAL_TTL_SECONDS, clock=time.time):
        self._secret = (secret or secrets.token_urlsafe(32)).encode()
        self._ttl, self._clock = ttl, clock
        self._items: dict[str, Proposal] = {}

    def _sign(self, body: str) -> str:
        return base64.urlsafe_b64encode(hmac.new(self._secret, body.encode(), hashlib.sha256).digest()[:12]).decode().rstrip("=")

    def create(self, user_id: str, action: str, args: dict, summary: str, turn_id: str) -> Proposal:
        self._purge()
        expires_at = int(self._clock() + self._ttl)
        payload = {"u": user_id, "a": ACTION_CODES.get(action, action), "x": args, "e": expires_at}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        proposal = Proposal(f"PR-{body}.{self._sign(body)}", user_id, action, args, summary, turn_id, expires_at)
        self._items[proposal.proposal_id] = proposal
        return proposal

    def _restore(self, proposal_id: str, user_id: str) -> Proposal | None:
        """An open proposal rebuilt from its signed id (after a restart); None when it is forged, someone else's or expired."""
        body, _, signature = proposal_id.removeprefix("PR-").partition(".")
        if not proposal_id.startswith("PR-") or not hmac.compare_digest(signature, self._sign(body)):
            return None
        try:
            payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            action = next((k for k, v in ACTION_CODES.items() if v == payload["a"]), None)
            if action is None or payload["u"] != user_id or payload["e"] < self._clock() or not isinstance(payload["x"], dict):
                return None
            proposal = Proposal(proposal_id, user_id, action, payload["x"], "", "", payload["e"])
        except (ValueError, KeyError, TypeError):
            return None
        self._items[proposal_id] = proposal
        return proposal

    def _visible(self, proposal_id: str, user_id: str) -> Proposal:
        """Someone else's proposal, an unknown one and an expired one all look the same: not found."""
        proposal = self._items.get(proposal_id) or self._restore(proposal_id, user_id)
        if proposal is None or proposal.user_id != user_id or proposal.expires_at < self._clock():
            raise ApiError("not_found", "That proposal does not exist or has expired.")
        return proposal

    def is_open(self, proposal_id: str, user_id: str) -> bool:
        """Can the person still confirm it? (Used when a saved chat is reopened.)"""
        try:
            return self._visible(proposal_id, user_id).state == "open"
        except ApiError:
            return False

    def claim(self, proposal_id: str, user_id: str) -> Proposal:
        proposal = self._visible(proposal_id, user_id)
        if proposal.state != "open":
            raise ApiError("conflict", f"That proposal was already {proposal.state}.")
        proposal.state = "confirmed"
        return proposal

    def release(self, proposal: Proposal) -> None:
        """Execution failed before anything happened: let the person try again."""
        proposal.state = "open"

    def dismiss(self, proposal_id: str, user_id: str) -> Proposal:
        proposal = self._visible(proposal_id, user_id)
        if proposal.state == "open":
            proposal.state = "dismissed"
        return proposal

    def _purge(self) -> None:
        now = self._clock()
        for key in [k for k, p in self._items.items() if p.expires_at < now]:
            del self._items[key]
