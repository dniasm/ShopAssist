/* What comes down the session socket. A frame is untyped JSON, so `parseSocketMessage` checks it at runtime and builds a value of the
 * generated `SocketMessage` union (schema: GET /api/ws/schema), which TypeScript then narrows on channel + type everywhere else. It
 * constructs each message from checked fields rather than casting, so a frame that does not fit the contract is dropped, not trusted. */

import type {
  CheckoutDoneData, CheckoutFailedData, CheckoutInterruptedData, CheckoutStepData, Order, OrderEvent, OrderLine, OrderStatusData,
  ChatCard, ChatCardKind, ChatWsMessage, Shortage, SocketMessage, User,
} from "../../api/types";

type Rec = Record<string, unknown>;

function isRec(v: unknown): v is Rec {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const num = (v: unknown, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

const STEPS = ["VALIDATING", "RESERVING", "CHARGING", "CONFIRMING", "COMPENSATING"] as const satisfies readonly CheckoutStepData["step"][];
const STATES = ["STARTED", "SUCCEEDED", "FAILED"] as const satisfies readonly CheckoutStepData["state"][];
const ORDER_STATUSES = ["PENDING", "CONFIRMED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED", "CANCELLED", "FAILED"] as const satisfies readonly Order["status"][];

function parseUser(v: unknown): User | null {
  if (!isRec(v) || typeof v.user_id !== "string") return null;
  return { user_id: v.user_id, name: str(v.name), email: str(v.email), city: str(v.city) };
}

function parseShortages(v: unknown): Shortage[] | undefined {
  if (!Array.isArray(v) || v.length === 0) return undefined;   // the server only sends it when non-empty
  const out: Shortage[] = [];
  for (const item of v) {
    if (isRec(item) && typeof item.sku_id === "string") out.push({ sku_id: item.sku_id, requested: num(item.requested), available: num(item.available) });
  }
  return out.length > 0 ? out : undefined;
}

function parseOrderLine(v: unknown): OrderLine | null {
  if (!isRec(v) || typeof v.sku_id !== "string") return null;
  const quantity = num(v.quantity);
  const unit = num(v.unit_price_cents);
  return {
    sku_id: v.sku_id, product_id: str(v.product_id), name: str(v.name), category: str(v.category), size: str(v.size), color: str(v.color),
    quantity, unit_price_cents: unit, line_total_cents: num(v.line_total_cents, unit * quantity),
  };
}

function parseOrderEvent(v: unknown): OrderEvent | null {
  if (!isRec(v)) return null;
  return { event_id: num(v.event_id), order_id: str(v.order_id), status: str(v.status), message: str(v.message), timestamp: str(v.timestamp) };
}

/** An order as the socket carries it (the `done` event). Unknown statuses are refused rather than guessed. */
export function parseOrder(v: unknown): Order | null {
  if (!isRec(v) || typeof v.order_id !== "string") return null;
  const status = ORDER_STATUSES.find((s) => s === v.status);
  if (!status) return null;
  return {
    order_id: v.order_id, user_id: str(v.user_id), status,
    lines: Array.isArray(v.lines) ? v.lines.flatMap((l) => parseOrderLine(l) ?? []) : [],
    subtotal_cents: num(v.subtotal_cents), shipping_cents: num(v.shipping_cents), total_cents: num(v.total_cents),
    payment_id: str(v.payment_id), failure_reason: str(v.failure_reason), created_at: str(v.created_at), updated_at: str(v.updated_at),
    history: Array.isArray(v.history) ? v.history.flatMap((e) => parseOrderEvent(e) ?? []) : [],
  };
}

/** One WebSocket text frame -> a typed message, or null if it is not part of the contract. */
export function parseSocketMessage(raw: string): SocketMessage | null {
  let value: unknown;
  try { value = JSON.parse(raw); } catch { return null; }
  if (!isRec(value) || !isRec(value.data)) return null;
  // seq is 0 for system messages (not in the replay log); a missing or non-numeric seq is NaN so the machine drops it.
  const seq = num(value.seq, Number.NaN);
  const ts = str(value.ts);
  const d = value.data;

  switch (`${str(value.channel)}/${str(value.type)}`) {
    case "system/hello": {
      const user = parseUser(d.user);
      return user ? { seq, ts, channel: "system", type: "hello", data: { user, seq: num(d.seq) } } : null;
    }
    case "system/resync":
      return { seq, ts, channel: "system", type: "resync", data: {} };
    case "checkout/step": {
      const step = STEPS.find((s) => s === d.step);
      const state = STATES.find((s) => s === d.state);
      if (typeof d.order_id !== "string" || !step || !state) return null;
      return { seq, ts, channel: "checkout", type: "step", data: { order_id: d.order_id, step, state, message: str(d.message), shortages: parseShortages(d.shortages) } };
    }
    case "checkout/done": {
      const order = parseOrder(d.order);
      return order ? { seq, ts, channel: "checkout", type: "done", data: { order } } : null;
    }
    case "checkout/failed":
      if (typeof d.order_id !== "string") return null;
      return { seq, ts, channel: "checkout", type: "failed", data: { order_id: d.order_id, reason: str(d.reason), shortages: parseShortages(d.shortages) } };
    case "checkout/interrupted":
      if (typeof d.order_id !== "string") return null;
      return { seq, ts, channel: "checkout", type: "interrupted", data: { order_id: d.order_id, message: str(d.message) } };
    case "order/status":
      if (typeof d.order_id !== "string") return null;
      return { seq, ts, channel: "order", type: "status", data: { order_id: d.order_id, status: str(d.status), message: str(d.message), event_id: num(d.event_id) } };
    default:
      return parseChat(seq, ts, `${str(value.channel)}/${str(value.type)}`, d);
  }
}

const CARD_KINDS = ["products", "product", "stock", "cart", "order", "orders"] as const satisfies readonly ChatCardKind[];

function parseCards(v: unknown): ChatCard[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((c): ChatCard[] => {
    const kind = isRec(c) ? CARD_KINDS.find((k) => k === c.kind) : undefined;
    return isRec(c) && kind ? [{ kind, data: c.data }] : [];
  });
}

/** The `chat` channel (TEMPORARY contract, see api/temporary.ts). Anything that does not fit is dropped. */
function parseChat(seq: number, ts: string, key: string, d: Rec): ChatWsMessage | null {
  if (!key.startsWith("chat/") || typeof d.turn_id !== "string") return null;
  const turn_id = d.turn_id;
  switch (key) {
    case "chat/turn_started": return { seq, ts, channel: "chat", type: "turn_started", data: { turn_id } };
    case "chat/tool_call":
      if (typeof d.tool !== "string") return null;
      return { seq, ts, channel: "chat", type: "tool_call", data: { turn_id, tool: d.tool, args: isRec(d.args) ? d.args : {} } };
    case "chat/tool_result":
      if (typeof d.tool !== "string") return null;
      return { seq, ts, channel: "chat", type: "tool_result", data: { turn_id, tool: d.tool, summary: str(d.summary) } };
    case "chat/message": return { seq, ts, channel: "chat", type: "message", data: { turn_id, text: str(d.text), cards: parseCards(d.cards) } };
    case "chat/proposal":
      if (typeof d.proposal_id !== "string") return null;
      return { seq, ts, channel: "chat", type: "proposal", data: { turn_id, proposal_id: d.proposal_id, action: str(d.action), args: isRec(d.args) ? d.args : {}, summary: str(d.summary) } };
    case "chat/turn_done": return { seq, ts, channel: "chat", type: "turn_done", data: { turn_id, source: d.source === "llm" ? "llm" : "rules" } };
    case "chat/error": return { seq, ts, channel: "chat", type: "error", data: { turn_id, message: str(d.message) } };
    default: return null;
  }
}

// ---------- narrowing helpers (no parsing, no casts) ----------

export type CheckoutEvent =
  | { kind: "step"; data: CheckoutStepData }
  | { kind: "done"; data: CheckoutDoneData }
  | { kind: "failed"; data: CheckoutFailedData }
  | { kind: "interrupted"; data: CheckoutInterruptedData };

/** A `checkout` message as the saga's event, or null for any other channel. */
export function toCheckoutEvent(message: SocketMessage): CheckoutEvent | null {
  if (message.channel !== "checkout") return null;
  switch (message.type) {
    case "step": return { kind: "step", data: message.data };
    case "done": return { kind: "done", data: message.data };
    case "failed": return { kind: "failed", data: message.data };
    case "interrupted": return { kind: "interrupted", data: message.data };
    default: return null;
  }
}

/** A `chat` channel message, or null. */
export function toChatMessage(message: SocketMessage): ChatWsMessage | null {
  return message.channel === "chat" ? message : null;
}

/** An `order` channel status change, or null. */
export function toOrderStatus(message: SocketMessage): OrderStatusData | null {
  return message.channel === "order" ? message.data : null;
}

/** Which order a checkout event is about. */
export function checkoutOrderId(event: CheckoutEvent): string {
  return event.kind === "done" ? event.data.order.order_id : event.data.order_id;
}
