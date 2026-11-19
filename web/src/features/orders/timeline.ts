/* The delivery timeline of one order, as a pure function of what is known: the order's own `history` (from GET /api/orders/{id})
 * plus the `order` channel's live `status` events.
 *
 * Rules (each one has a test):
 *  - events are merged by event_id: the same event arriving from history and from the socket (or twice) counts once
 *  - they are applied in event_id order, whatever order they arrived in
 *  - a step is shown once: the first event that reaches it. An event that does not go forward (a repeat, an older status) is ignored
 *  - a step skipped by missed events is shown as reached, without a time ("implied"), because a later step proves it
 *  - CANCELLED and FAILED are terminal, not steps: the first one wins and everything after it (by event_id) is ignored, so the
 *    steps reached so far stay as they were and the rest stay pending
 *  - a refresh mid-delivery (history already holds some events, the socket replays all of them) gives the same timeline */

import type { OrderEvent, OrderStatus, OrderStatusData } from "../../api/types";

export const ORDER_STEPS = ["CONFIRMED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"] as const satisfies readonly OrderStatus[];
export type OrderStep = (typeof ORDER_STEPS)[number];
export type TerminalStatus = "CANCELLED" | "FAILED";

export const STEP_LABELS: Record<OrderStep, string> = {
  CONFIRMED: "Confirmed",
  PACKED: "Packed",
  SHIPPED: "Shipped",
  OUT_FOR_DELIVERY: "Out for delivery",
  DELIVERED: "Delivered",
};

const ALL_STATUSES = ["PENDING", ...ORDER_STEPS, "CANCELLED", "FAILED"] as const satisfies readonly OrderStatus[];

/** A status string from the wire as a known status, or null (a future status must not crash the page). */
export function asOrderStatus(value: string): OrderStatus | null {
  return ALL_STATUSES.find((s) => s === value) ?? null;
}

export function isTerminal(status: string): status is TerminalStatus {
  return status === "CANCELLED" || status === "FAILED";
}

export function stepIndex(status: string): number {
  return ORDER_STEPS.findIndex((s) => s === status);
}

export interface StatusEvent { event_id: number; status: string; message: string; timestamp: string | null }

/** A live `order` event; `at` is the socket envelope's own timestamp (the event payload carries none). */
export type LiveEvent = OrderStatusData & { at?: string };

/** History and live events as one list: one entry per event_id (history wins: its timestamp is the record's), oldest first. */
export function mergeEvents(history: readonly OrderEvent[], live: readonly LiveEvent[]): StatusEvent[] {
  const byId = new Map<number, StatusEvent>();
  for (const e of live) {
    byId.set(e.event_id, { event_id: e.event_id, status: e.status, message: e.message, timestamp: e.at || null });
  }
  for (const e of history) {
    byId.set(e.event_id, { event_id: e.event_id, status: e.status, message: e.message, timestamp: e.timestamp || null });
  }
  return [...byId.values()].sort((a, b) => a.event_id - b.event_id);
}

export type StepState = "done" | "current" | "pending";
export interface TimelineStep {
  status: OrderStep;
  label: string;
  state: StepState;
  /** ISO timestamp, when the history carries one. */
  at: string | null;
  message: string;
  /** Reached, but its own event never arrived (a later step proves it). */
  implied: boolean;
}

export interface Timeline {
  steps: TimelineStep[];
  terminal: { status: TerminalStatus; message: string; at: string | null } | null;
  /** The furthest status the events prove (a terminal one wins), or null when they say nothing yet. */
  status: OrderStatus | null;
}

export function timeline(history: readonly OrderEvent[], live: readonly LiveEvent[] = []): Timeline {
  let reached = -1;
  let terminal: Timeline["terminal"] = null;
  const found = new Map<number, StatusEvent>();

  for (const event of mergeEvents(history, live)) {
    if (terminal) break;                                   // nothing counts after the end
    if (isTerminal(event.status)) {
      terminal = { status: event.status, message: event.message, at: event.timestamp };
      continue;
    }
    const idx = stepIndex(event.status);
    if (idx <= reached) continue;                          // not forward: a repeat or an older status (idx -1 = not a step, e.g. PENDING)
    reached = idx;
    found.set(idx, event);
  }

  const steps = ORDER_STEPS.map((status, i): TimelineStep => {
    const event = found.get(i);
    const done = i < reached || (i === reached && (terminal !== null || status === "DELIVERED"));
    const state: StepState = done ? "done" : i === reached ? "current" : "pending";
    return { status, label: STEP_LABELS[status], state, at: event?.timestamp ?? null, message: event?.message ?? "", implied: i <= reached && !event };
  });

  return { steps, terminal, status: terminal ? terminal.status : reached >= 0 ? (ORDER_STEPS[reached] ?? null) : null };
}

/** The order's status now: what the order record says, moved forward by whatever the events prove. A terminal status wins. */
export function effectiveStatus(recorded: OrderStatus, tl: Timeline): OrderStatus {
  if (isTerminal(recorded)) return recorded;
  if (tl.terminal) return tl.terminal.status;
  if (tl.status && stepIndex(tl.status) > stepIndex(recorded)) return tl.status;
  return recorded;
}

/** One live event applied to a status (the orders list): forward only, terminal is final, unknown statuses are ignored. */
export function advanceStatus(current: OrderStatus, event: OrderStatusData): OrderStatus {
  const next = asOrderStatus(event.status);
  if (!next || isTerminal(current) || next === "PENDING") return current;
  if (isTerminal(next)) return next;
  return stepIndex(next) > stepIndex(current) ? next : current;
}
