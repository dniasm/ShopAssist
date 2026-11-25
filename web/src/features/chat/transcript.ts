/* The chat transcript as a pure reducer over what the shopper does (send, confirm, dismiss) and what the socket delivers (the `chat`
 * channel, plus `order` events for a confirmed cancellation). No React, no sockets: the provider feeds actions in.
 *
 * Rules (each has a test):
 *  - every socket event carries a seq and is applied once: a replay after a reconnect or a refresh never duplicates anything
 *  - events belong to a turn by turn_id, so turns that interleave on the wire stay separate on the screen
 *  - events may arrive in any order: a tool_result before its tool_call, a message before turn_started, any of them before the POST that
 *    started the turn has returned. The turn is created by whichever comes first and the rest fill it in
 *  - the shopper's own words are not on the socket, so they are remembered by turn_id (the provider persists `texts` for the tab)
 *  - a proposal can be decided once: Confirm (a cancellation then follows the order events) or Dismiss; a server "already done"
 *    answer is shown as such, never as a second action */

import type { ChatCard, ChatContext, ChatWsMessage, OrderStatus, OrderStatusData } from "../../api/types";
import { parseChatCards } from "../session/events";
import { advanceStatus, asOrderStatus } from "../orders/timeline";

export type ChatSource = "llm" | "rules";

export interface ToolStep {
  tool: string;
  /** null when only the result was seen so far (it arrived first). */
  args: Record<string, unknown> | null;
  summary: string | null;
  state: "running" | "done";
}

export type ProposalState = "pending" | "confirming" | "confirmed" | "dismissed" | "handled";

/** What a confirmed proposal turns into on screen. Only a cancellation has one (checkout_redirect just navigates). */
export type FollowUp = { kind: "order"; orderId: string; status: OrderStatus | null; message: string };

export interface ChatProposal {
  id: string;
  action: string;
  args: Record<string, unknown>;
  summary: string;
  state: ProposalState;
  /** A line under the buttons: a failed attempt, or "already handled". */
  note: string | null;
  /** The highest seq seen when it was confirmed: only later order events can be its result. */
  confirmedAfterSeq: number | null;
  follow: FollowUp | null;
}

export interface ChatMessage {
  text: string;
  cards: ChatCard[];
  /** Said after the cards (a follow-up question under a product list); empty when there is none. */
  closing: string;
}

export interface ChatTurn {
  /** Stable React key: the local id for turns the shopper started here, else the turn id. */
  key: string;
  turnId: string | null;
  request: { text: string; context: ChatContext } | null;
  /** The POST has not returned yet. */
  sending: boolean;
  tools: ToolStep[];
  messages: ChatMessage[];
  proposals: ChatProposal[];
  source: ChatSource | null;
  done: boolean;
  /** The turn failed: the server's `error` event, or the POST itself. */
  error: string | null;
}

export interface Decided { state: "confirmed" | "dismissed"; afterSeq: number | null; orderId: string | null }

export interface ChatState {
  turns: ChatTurn[];
  /** seqs already applied (replays are ignored). */
  seen: ReadonlySet<number>;
  maxSeq: number;
  /** turn_id -> what the shopper typed: persisted by the provider so a refresh can show the questions again. */
  texts: Readonly<Record<string, string>>;
  /** proposal_id -> what the shopper decided: persisted so a refresh does not offer Confirm a second time. */
  decided: Readonly<Record<string, Decided>>;
  /** Events of turns that are not part of this conversation (yet): the socket replays every conversation of the user, and an event can
   *  beat the POST that names its turn. They wait here; `accepted` for that turn brings them in, and the rest are never shown. */
  parked: Readonly<Record<string, ChatWsMessage[]>>;
}

export function initialChat(restored: { texts?: Record<string, string>; decided?: Record<string, Decided> } = {}): ChatState {
  return { turns: [], seen: new Set(), maxSeq: 0, texts: restored.texts ?? {}, decided: restored.decided ?? {}, parked: {} };
}

export type ChatAction =
  | { type: "user_sent"; localId: string; text: string; context: ChatContext }
  | { type: "accepted"; localId: string; turnId: string }
  | { type: "send_failed"; localId: string; message: string }
  | { type: "chat"; message: ChatWsMessage }
  | { type: "order_status"; seq: number; data: OrderStatusData }
  | { type: "proposal_confirming"; id: string }
  | { type: "proposal_confirmed"; id: string }
  | { type: "proposal_dismissed"; id: string }
  | { type: "proposal_handled"; id: string; note: string }
  | { type: "proposal_failed"; id: string; note: string }
  | { type: "loaded"; turns: ChatTurn[] }
  | { type: "reset" };

const MAX_TURNS = 100;
const MAX_PARKED_TURNS = 50;

function blankTurn(key: string, turnId: string | null): ChatTurn {
  return { key, turnId, request: null, sending: false, tools: [], messages: [], proposals: [], source: null, done: false, error: null };
}

/** Finds the turn with this id, or adds one (with the shopper's words, if they were remembered). */
function ensureTurn(state: ChatState, turnId: string): { state: ChatState; index: number } {
  const found = state.turns.findIndex((t) => t.turnId === turnId);
  if (found >= 0) return { state, index: found };
  const text = state.texts[turnId];
  const turn: ChatTurn = { ...blankTurn(turnId, turnId), request: text === undefined ? null : { text, context: { path: "" } } };
  const turns = [...state.turns, turn].slice(-MAX_TURNS);
  return { state: { ...state, turns }, index: turns.length - 1 };
}

function updateTurn(state: ChatState, index: number, change: (t: ChatTurn) => ChatTurn): ChatState {
  return { ...state, turns: state.turns.map((t, i) => (i === index ? change(t) : t)) };
}

function mapProposals(state: ChatState, change: (p: ChatProposal) => ChatProposal): ChatState {
  return { ...state, turns: state.turns.map((t) => (t.proposals.length ? { ...t, proposals: t.proposals.map(change) } : t)) };
}

function withProposal(state: ChatState, id: string, change: (p: ChatProposal) => ChatProposal): ChatState {
  return mapProposals(state, (p) => (p.id === id ? change(p) : p));
}

/** Marks an event seq as seen. Returns null when it was already applied. */
function markSeen(state: ChatState, seq: number): ChatState | null {
  if (!Number.isFinite(seq) || seq <= 0) return state;      // control messages carry no seq: nothing to dedupe on
  if (state.seen.has(seq)) return null;
  return { ...state, seen: new Set(state.seen).add(seq), maxSeq: Math.max(state.maxSeq, seq) };
}

/** A turn belongs to this conversation when the shopper sent it here (it is on screen) or this tab remembered its question. */
function isOwned(state: ChatState, turnId: string): boolean {
  return Object.prototype.hasOwnProperty.call(state.texts, turnId) || state.turns.some((t) => t.turnId === turnId);
}

function park(state: ChatState, m: ChatWsMessage): ChatState {
  const id = m.data.turn_id;
  const parked: Record<string, ChatWsMessage[]> = { ...state.parked, [id]: [...(state.parked[id] ?? []), m] };
  const ids = Object.keys(parked);
  for (const old of ids.slice(0, Math.max(0, ids.length - MAX_PARKED_TURNS))) delete parked[old];
  return { ...state, parked };
}

function applyChat(state: ChatState, m: ChatWsMessage): ChatState {
  if (m.type === "conversation" || m.type === "navigate") return state;   // acted on by the provider, nothing to show
  const { state: s0, index } = ensureTurn(state, m.data.turn_id);
  switch (m.type) {
    case "turn_started":
      return s0;
    case "tool_call": {
      const { tool, args } = m.data;
      return updateTurn(s0, index, (t) => {
        const orphan = t.tools.findIndex((x) => x.tool === tool && x.args === null);   // its result came first: complete that entry
        if (orphan >= 0) return { ...t, tools: t.tools.map((x, i) => (i === orphan ? { ...x, args } : x)) };
        return { ...t, tools: [...t.tools, { tool, args, summary: null, state: "running" }] };
      });
    }
    case "tool_result": {
      const { tool, summary } = m.data;
      return updateTurn(s0, index, (t) => {
        const running = t.tools.findIndex((x) => x.tool === tool && x.state === "running");
        if (running >= 0) return { ...t, tools: t.tools.map((x, i) => (i === running ? { ...x, summary, state: "done" } : x)) };
        return { ...t, tools: [...t.tools, { tool, args: null, summary, state: "done" }] };
      });
    }
    case "message":
      return updateTurn(s0, index, (t) => ({ ...t, messages: [...t.messages, { text: m.data.text, cards: m.data.cards, closing: m.data.closing }] }));
    case "proposal": {
      const { proposal_id: id, action, args, summary } = m.data;
      if (s0.turns.some((t) => t.proposals.some((p) => p.id === id))) return s0;
      const decided = s0.decided[id];
      const proposal: ChatProposal = {
        id, action, args, summary,
        state: decided ? decided.state : "pending",
        note: null,
        confirmedAfterSeq: decided?.afterSeq ?? null,
        follow: decided?.state === "confirmed" ? followFor(action, args) : null,
      };
      return updateTurn(s0, index, (t) => ({ ...t, proposals: [...t.proposals, proposal] }));
    }
    case "turn_done":
      return updateTurn(s0, index, (t) => ({ ...t, done: true, source: m.data.source }));
    case "error":
      return updateTurn(s0, index, (t) => ({ ...t, done: true, error: m.data.message || "Something went wrong." }));
  }
}

function followFor(action: string, args: Record<string, unknown>): FollowUp | null {
  if (action === "cancel_order" && typeof args.order_id === "string") return { kind: "order", orderId: args.order_id, status: null, message: "" };
  return null;
}

function applyOrderStatus(state: ChatState, seq: number, data: OrderStatusData): ChatState {
  return mapProposals(state, (p) => {
    const f = p.follow;
    if (!f || f.kind !== "order" || f.orderId !== data.order_id || p.confirmedAfterSeq === null || seq <= p.confirmedAfterSeq) return p;
    const incoming = asOrderStatus(data.status);
    const status = f.status ? advanceStatus(f.status, data) : incoming;
    if (status === f.status) return p;                    // not forward (a repeat, or something after the end): nothing changes, message included
    return { ...p, follow: { ...f, status, message: data.message || f.message } };
  });
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "reset":
      return initialChat();
    case "loaded":
      return { ...initialChat(), turns: action.turns };

    case "user_sent": {
      const turn: ChatTurn = { ...blankTurn(action.localId, null), request: { text: action.text, context: action.context }, sending: true };
      return { ...state, turns: [...state.turns, turn].slice(-MAX_TURNS) };
    }
    case "accepted": {
      const local = state.turns.findIndex((t) => t.key === action.localId);
      if (local < 0) return state;
      const texts = { ...state.texts, [action.turnId]: state.turns[local]?.request?.text ?? "" };
      const early = state.turns.findIndex((t, i) => i !== local && t.turnId === action.turnId);   // the turn already exists (restored question)
      let next: ChatState;
      if (early < 0) {
        next = { ...state, texts, turns: state.turns.map((t, i) => (i === local ? { ...t, turnId: action.turnId, sending: false } : t)) };
      } else {
        const localTurn = state.turns[local];
        const earlyTurn = state.turns[early];
        if (!localTurn || !earlyTurn) return state;
        const merged: ChatTurn = { ...earlyTurn, key: localTurn.key, request: localTurn.request, sending: false };
        next = { ...state, texts, turns: state.turns.flatMap((t, i) => (i === local ? [merged] : i === early ? [] : [t])) };
      }
      const waiting = next.parked[action.turnId] ?? [];                       // events that beat the POST response
      if (waiting.length === 0) return next;
      const { [action.turnId]: _brought, ...rest } = next.parked;
      return waiting.reduce(applyChat, { ...next, parked: rest });
    }
    case "send_failed":
      return { ...state, turns: state.turns.map((t) => (t.key === action.localId ? { ...t, sending: false, done: true, error: action.message } : t)) };

    case "chat": {
      const seen = markSeen(state, action.message.seq);
      if (!seen) return state;
      if (action.message.type === "conversation" || action.message.type === "navigate") return seen;
      return isOwned(seen, action.message.data.turn_id) ? applyChat(seen, action.message) : park(seen, action.message);
    }
    case "order_status": {
      const seen = markSeen(state, action.seq);
      return seen ? applyOrderStatus(seen, action.seq, action.data) : state;
    }

    case "proposal_confirming":
      return withProposal(state, action.id, (p) => (p.state === "pending" ? { ...p, state: "confirming", note: null } : p));
    case "proposal_confirmed": {
      let confirmedAt = state.maxSeq;
      const next = withProposal(state, action.id, (p) => {
        if (p.state !== "confirming" && p.state !== "pending") return p;
        confirmedAt = state.maxSeq;
        return { ...p, state: "confirmed", note: null, confirmedAfterSeq: confirmedAt, follow: followFor(p.action, p.args) };
      });
      return { ...next, decided: { ...next.decided, [action.id]: { state: "confirmed", afterSeq: confirmedAt, orderId: null } } };
    }
    case "proposal_dismissed": {
      const next = withProposal(state, action.id, (p) => (p.state === "pending" ? { ...p, state: "dismissed", note: null } : p));
      return { ...next, decided: { ...next.decided, [action.id]: { state: "dismissed", afterSeq: null, orderId: null } } };
    }
    case "proposal_handled":
      return withProposal(state, action.id, (p) => ({ ...p, state: "handled", note: action.note }));
    case "proposal_failed":
      return withProposal(state, action.id, (p) => (p.state === "confirming" ? { ...p, state: "pending", note: action.note } : p));
  }
}

/** Is the turn still waiting for the assistant (nothing final yet)? */
export function isWorking(turn: ChatTurn): boolean {
  return !turn.done && !turn.error;
}

/** What the server saved of a conversation (GET /chat/conversations/{id}/messages) as finished turns: each question starts a turn, the
 *  answers after it (text + cards) belong to it. Proposals are not saved, so none come back. */
export function fromHistory(messages: readonly { role: string; text: string; cards: unknown }[]): ChatTurn[] {
  const turns: ChatTurn[] = [];
  const start = (request: ChatTurn["request"]): ChatTurn => {
    const turn: ChatTurn = { ...blankTurn(`saved-${turns.length}`, null), request, done: true };
    turns.push(turn);
    return turn;
  };
  for (const m of messages) {
    if (m.role === "user") start({ text: m.text, context: { path: "" } });
    else if (m.role === "assistant") (turns[turns.length - 1] ?? start(null)).messages.push({ text: m.text, cards: parseChatCards(m.cards), closing: "" });
  }
  return turns;
}
