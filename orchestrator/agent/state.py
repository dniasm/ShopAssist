"""The agent's two small in-memory stores: conversation memory (last 6 turns, 1 h) and proposals (10 min, confirm once)."""
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


class ProposalStore:
    """A proposal is what the assistant offers and the person confirms. It lives 10 minutes, belongs to one user and can be
    confirmed exactly once: `claim` is the single place that turns "open" into "confirmed"."""

    def __init__(self, ttl: float = PROPOSAL_TTL_SECONDS, clock=time.monotonic):
        self._ttl, self._clock = ttl, clock
        self._items: dict[str, Proposal] = {}

    def create(self, user_id: str, action: str, args: dict, summary: str, turn_id: str) -> Proposal:
        self._purge()
        proposal = Proposal("PR-" + secrets.token_hex(6), user_id, action, args, summary, turn_id, self._clock() + self._ttl)
        self._items[proposal.proposal_id] = proposal
        return proposal

    def _visible(self, proposal_id: str, user_id: str) -> Proposal:
        """Someone else's proposal, an unknown one and an expired one all look the same: not found."""
        self._purge()
        proposal = self._items.get(proposal_id)
        if proposal is None or proposal.user_id != user_id:
            raise ApiError("not_found", "That proposal does not exist or has expired.")
        return proposal

    def claim(self, proposal_id: str, user_id: str) -> Proposal:
        proposal = self._visible(proposal_id, user_id)
        if proposal.state != "open":
            raise ApiError("conflict", f"That proposal was already {proposal.state}.")
        proposal.state = "confirmed"
        return proposal

    def release(self, proposal: Proposal) -> None:
        """Execution failed before anything happened: let the person try again."""
        proposal.state = "open"

    def dismiss(self, proposal_id: str, user_id: str) -> None:
        proposal = self._visible(proposal_id, user_id)
        if proposal.state == "open":
            proposal.state = "dismissed"

    def _purge(self) -> None:
        now = self._clock()
        for key in [k for k, p in self._items.items() if p.expires_at < now]:
            del self._items[key]
