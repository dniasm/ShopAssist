/* The checkout saga as the shopper sees it: a pure reducer over the WebSocket `checkout` events for ONE order.
 *
 * The UI shows only what these events say. The rules that make it safe to feed in replayed, duplicated or reordered events:
 *  - a step only moves forward: pending -> active -> done | failed; an older state arriving late is ignored
 *  - a step that reports started/succeeded/failed means every earlier step is done (their own events may have been missed)
 *  - the first final event (done / failed) wins; anything after it is ignored
 *  - `interrupted` is NOT final: the saga stream broke and the outcome is unknown. The steps stay exactly as they were, the
 *    server's message is kept, and nothing is ever claimed as success or failure until a done / failed event (or the order's
 *    own final status, see orderStatusToEvent) says so
 *  - events for other orders are ignored */

import type { Order, Shortage } from "../../api/types";
import type { CheckoutEvent } from "../session/events";
import { checkoutOrderId } from "../session/events";

export const FORWARD_STEPS = ["VALIDATING", "RESERVING", "CHARGING", "CONFIRMING"] as const;
export type ForwardStep = (typeof FORWARD_STEPS)[number];

export type StepStatus = "pending" | "active" | "done" | "failed";
export interface StepProgress { status: StepStatus; message: string }

export type FailureKind = "sold_out" | "declined" | "interrupted" | "other";
export interface Failure {
  kind: FailureKind;
  /** The server's own words, shown as detail. */
  reason: string;
  shortages: Shortage[];
  failedStep: ForwardStep | null;
}

export type Outcome = { kind: "success"; order: Order } | { kind: "failed"; failure: Failure };

export interface SagaState {
  orderId: string;
  steps: Record<ForwardStep, StepProgress>;
  /** Undoing earlier steps after a failure (the saga's COMPENSATING step). "none" when it never ran. */
  compensation: StepProgress & { status: StepStatus };
  outcome: Outcome | null;
  /** Set by an `interrupted` event until something settles the order: we lost contact and do not know how it ended. */
  interrupted: { message: string } | null;
  /** Events applied (not ignored), for tests and "waiting for the first update" checks. */
  applied: number;
}

const pending = (): StepProgress => ({ status: "pending", message: "" });

export function initialSaga(orderId: string): SagaState {
  return {
    orderId,
    steps: { VALIDATING: pending(), RESERVING: pending(), CHARGING: pending(), CONFIRMING: pending() },
    compensation: pending(),
    outcome: null,
    interrupted: null,
    applied: 0,
  };
}

const RANK: Record<StepStatus, number> = { pending: 0, active: 1, done: 2, failed: 2 };

/** Moves a step forward only; a finished step never changes. */
function advance(current: StepProgress, status: StepStatus, message: string): StepProgress {
  if (current.status === "done" || current.status === "failed") return current;
  if (RANK[status] < RANK[current.status]) return current;
  return { status, message: message || current.message };
}

function markEarlierDone(steps: SagaState["steps"], upTo: ForwardStep): SagaState["steps"] {
  const out = { ...steps };
  for (const step of FORWARD_STEPS) {
    if (step === upTo) break;
    if (out[step].status !== "failed") out[step] = advance(out[step], "done", "");
  }
  return out;
}

/** Which step the failure happened in: the one that reported failed, else the one still running. */
function failedStepOf(steps: SagaState["steps"]): ForwardStep | null {
  return FORWARD_STEPS.find((s) => steps[s].status === "failed") ?? FORWARD_STEPS.find((s) => steps[s].status === "active") ?? null;
}

export function classifyFailure(reason: string, shortages: readonly Shortage[], failedStep: ForwardStep | null): FailureKind {
  const r = reason.toLowerCase();
  if (shortages.length > 0) return "sold_out";
  // Payment wording first: "insufficient funds" must not read as "insufficient stock".
  if (/declin|card|payment|funds/.test(r)) return "declined";
  if (/stock|shortage|sold out|unavailable/.test(r)) return "sold_out";
  if (/interrupt|recover|crash|timed? ?out|abandon|restart/.test(r)) return "interrupted";
  if (failedStep === "RESERVING") return "sold_out";
  if (failedStep === "CHARGING") return "declined";
  return "other";
}

export function reduceSaga(state: SagaState, event: CheckoutEvent): SagaState {
  if (state.outcome || checkoutOrderId(event) !== state.orderId) return state;

  if (event.kind === "interrupted") {
    return { ...state, interrupted: { message: event.data.message }, applied: state.applied + 1 };
  }

  if (event.kind === "done") {
    const steps = { ...state.steps };
    for (const step of FORWARD_STEPS) steps[step] = advance(steps[step], "done", "");
    return { ...state, steps, outcome: { kind: "success", order: event.data.order }, interrupted: null, applied: state.applied + 1 };
  }

  if (event.kind === "failed") {
    const steps = { ...state.steps };
    const failedStep = failedStepOf(steps);
    if (failedStep && steps[failedStep].status === "active") steps[failedStep] = { status: "failed", message: steps[failedStep].message };
    const shortages = event.data.shortages ?? [];
    return {
      ...state,
      steps: failedStep ? markEarlierDone(steps, failedStep) : steps,
      outcome: { kind: "failed", failure: { kind: classifyFailure(event.data.reason, shortages, failedStep), reason: event.data.reason, shortages, failedStep } },
      interrupted: null,
      applied: state.applied + 1,
    };
  }

  // The contract's enums are exact: step is one of the four forward steps or COMPENSATING, state is STARTED / SUCCEEDED / FAILED.
  const { step, state: phase, message } = event.data;
  const status: StepStatus = phase === "STARTED" ? "active" : phase === "SUCCEEDED" ? "done" : "failed";

  // Any step event means the saga is talking to us again: the "lost contact" notice no longer applies.
  if (step === "COMPENSATING") {
    return { ...state, compensation: advance(state.compensation, status, message), interrupted: null, applied: state.applied + 1 };
  }

  const steps = markEarlierDone(state.steps, step);
  steps[step] = advance(steps[step], status, message);
  return { ...state, steps, interrupted: null, applied: state.applied + 1 };
}

/** Replays a list of events (e.g. the socket's buffered log) into a state. */
export function sagaFrom(orderId: string, events: readonly CheckoutEvent[]): SagaState {
  return events.reduce(reduceSaga, initialSaga(orderId));
}

/** What an order record says about a checkout that was in doubt. PENDING is still unknown (null); a confirmed (or later) order
 *  is a success; FAILED / CANCELLED is a failure. Used when the shopper asks "Check order status" and by the 10 s poll. */
export function orderStatusToEvent(order: Order): CheckoutEvent | null {
  const status = order.status.trim().toUpperCase();
  if (status === "" || status === "PENDING") return null;
  if (status === "FAILED" || status === "CANCELLED") {
    return { kind: "failed", data: { order_id: order.order_id, reason: order.failure_reason || (status === "CANCELLED" ? "The order was cancelled" : "") } };
  }
  return { kind: "done", data: { order } };
}

// ---------- what to show ----------

export const STEP_LABELS: Record<ForwardStep, string> = {
  VALIDATING: "Checking your order",
  RESERVING: "Reserving your items",
  CHARGING: "Charging your card",
  CONFIRMING: "Confirming your order",
};

export interface SagaRow { key: string; label: string; status: StepStatus; message: string; compensation: boolean }

/** The four forward steps, plus the undo step when it ran. Charged money changes what the undo step says. */
export function sagaRows(state: SagaState): SagaRow[] {
  const rows: SagaRow[] = FORWARD_STEPS.map((step) => ({
    key: step, label: STEP_LABELS[step], status: state.steps[step].status, message: state.steps[step].message, compensation: false,
  }));
  if (state.compensation.status !== "pending") {
    const charged = state.steps.CHARGING.status === "done";
    rows.push({
      key: "COMPENSATING",
      label: charged ? "Refunding your card and releasing your items" : "Releasing your reserved items",
      status: state.compensation.status,
      message: state.compensation.message,
      compensation: true,
    });
  }
  return rows;
}

/** A server reason as a sentence for people: "payment declined: card declined by issuer" -> "Card declined by issuer", a bare
 *  code like "out_of_stock" -> null (the title already says it), plain text such as "not enough stock" stays as it is. */
export function readableReason(reason: string): string | null {
  const text = reason.trim();
  if (!text) return null;
  if (/^[a-z]+(_[a-z]+)*$/.test(text)) return null;
  // "payment_declined: x" and "payment declined: x": the title already says it, keep the part after the colon
  const withoutCode = text.replace(/^[a-z]+(?:[_ ][a-z]+)+:\s*/i, "");
  return withoutCode ? withoutCode.charAt(0).toUpperCase() + withoutCode.slice(1) : null;
}

export interface FailureView {
  title: string;
  /** One line per problem item or the server's reason. */
  details: string[];
  /** What happened to the shopper's money and cart, in plain words. */
  reassurance: string;
  next: "review_cart" | "choose_method" | "try_again";
}

const detailOf = (reason: string): string[] => {
  const r = readableReason(reason);
  return r ? [r] : [];
};

/** `nameOf` turns a sku id into "Hartwick Lane Tailored Crew-Neck T-Shirt (M, navy)". */
export function describeFailure(failure: Failure, nameOf: (skuId: string) => string): FailureView {
  switch (failure.kind) {
    case "sold_out":
      return {
        title: "Some items sold out",
        details: failure.shortages.length > 0
          ? failure.shortages.map((s) => `${nameOf(s.sku_id)}: ${s.available > 0 ? `only ${s.available} left` : "none left"} (you asked for ${s.requested})`)
          : [readableReason(failure.reason) ?? "There was not enough stock for your order."],
        reassurance: "Nothing was charged and your cart is still intact. Adjust the quantities and try again.",
        next: "review_cart",
      };
    case "declined":
      return {
        title: "Your card was declined",
        details: detailOf(failure.reason),
        reassurance: "Nothing was charged and your cart is still intact. You can choose another payment method.",
        next: "choose_method",
      };
    case "interrupted":
      // The final answer after recovery: the order did not go through. (The unknown, in-between state is the `interrupted`
      // event, which never reaches this function.)
      return {
        title: "The order was not completed",
        details: detailOf(failure.reason),
        reassurance: "We lost contact while placing it, so it was cancelled. Anything we set aside was released, any payment was refunded, and your cart is still intact.",
        next: "try_again",
      };
    case "other":
      return {
        title: "We couldn't complete this order",
        details: detailOf(failure.reason),
        reassurance: "Anything we reserved has been released and any payment refunded. Your cart is still intact.",
        next: "try_again",
      };
  }
}
