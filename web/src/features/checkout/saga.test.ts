import { describe, expect, it } from "vitest";
import type { CheckoutStepData, Order, Shortage } from "../../api/types";
import type { CheckoutEvent } from "../session/events";
import { classifyFailure, describeFailure, initialSaga, orderStatusToEvent, readableReason, reduceSaga, sagaFrom, sagaRows, type SagaState } from "./saga";

const ORDER = "O-20412";
const step = (name: CheckoutStepData["step"], state: CheckoutStepData["state"], message = "", order = ORDER, shortages?: Shortage[]): CheckoutEvent =>
  ({ kind: "step", data: { order_id: order, step: name, state, message, shortages } });
const failed = (reason: string, shortages?: Shortage[], order = ORDER): CheckoutEvent => ({ kind: "failed", data: { order_id: order, reason, shortages } });
const order: Order = {
  order_id: ORDER, user_id: "U-01", status: "CONFIRMED", payment_id: "PAY-1", failure_reason: "", created_at: "", updated_at: "",
  lines: [{ sku_id: "SKU-1", product_id: "P-1", name: "Rain Jacket", category: "jackets", size: "M", color: "navy", quantity: 2, unit_price_cents: 6000, line_total_cents: 12000 }], history: [],
  subtotal_cents: 12000, shipping_cents: 0, total_cents: 12000,
};
const done: CheckoutEvent = { kind: "done", data: { order } };

// Recorded sequences, as the orchestrator emits them.
const SUCCESS: CheckoutEvent[] = [
  step("VALIDATING", "STARTED", "Checking prices"), step("VALIDATING", "SUCCEEDED"),
  step("RESERVING", "STARTED", "Reserving stock"), step("RESERVING", "SUCCEEDED"),
  step("CHARGING", "STARTED", "Charging card"), step("CHARGING", "SUCCEEDED"),
  step("CONFIRMING", "STARTED"), step("CONFIRMING", "SUCCEEDED"), done,
];
const SHORT: Shortage[] = [{ sku_id: "SKU-1", requested: 2, available: 1 }, { sku_id: "SKU-2", requested: 1, available: 0 }];
const SOLD_OUT: CheckoutEvent[] = [
  step("VALIDATING", "STARTED"), step("VALIDATING", "SUCCEEDED"), step("RESERVING", "STARTED"),
  step("RESERVING", "FAILED", "Not enough stock", ORDER, SHORT),
  step("COMPENSATING", "STARTED", "Releasing reserved items"), step("COMPENSATING", "SUCCEEDED"),
  failed("not enough stock", SHORT),
];
const DECLINED: CheckoutEvent[] = [
  step("VALIDATING", "STARTED"), step("VALIDATING", "SUCCEEDED"), step("RESERVING", "STARTED"), step("RESERVING", "SUCCEEDED"),
  step("CHARGING", "STARTED"), step("CHARGING", "FAILED", "Card declined"),
  step("COMPENSATING", "STARTED", "Releasing reserved items"), step("COMPENSATING", "SUCCEEDED"),
  failed("payment declined: card declined by issuer"),
];
/** The final FAILED that recovery produces after a lost stream (the failed event's reason mentions the interruption). */
const RECOVERED_FAILED: CheckoutEvent[] = [
  step("VALIDATING", "STARTED"), step("VALIDATING", "SUCCEEDED"), step("RESERVING", "STARTED"), step("RESERVING", "SUCCEEDED"), step("CHARGING", "STARTED"),
  step("COMPENSATING", "STARTED"), step("COMPENSATING", "SUCCEEDED"),
  failed("Checkout was interrupted and recovered by the system"),
];
const statuses = (s: SagaState) => sagaRows(s).map((r) => `${r.key}:${r.status}`);

describe("success", () => {
  it("walks every step to done and ends with the order from the done event", () => {
    const s = sagaFrom(ORDER, SUCCESS);
    expect(statuses(s)).toEqual(["VALIDATING:done", "RESERVING:done", "CHARGING:done", "CONFIRMING:done"]);
    expect(s.outcome).toEqual({ kind: "success", order });
  });
  it("shows progress after each event, with the active step carrying the server's message", () => {
    const s = sagaFrom(ORDER, SUCCESS.slice(0, 5));
    expect(statuses(s)).toEqual(["VALIDATING:done", "RESERVING:done", "CHARGING:active", "CONFIRMING:pending"]);
    expect(s.steps.CHARGING.message).toBe("Charging card");
    expect(s.outcome).toBeNull();
  });
});

describe("failures", () => {
  it("sold out: RESERVING fails, the undo step is visible, shortages are kept", () => {
    const s = sagaFrom(ORDER, SOLD_OUT);
    expect(statuses(s)).toEqual(["VALIDATING:done", "RESERVING:failed", "CHARGING:pending", "CONFIRMING:pending", "COMPENSATING:done"]);
    expect(sagaRows(s).at(-1)?.label).toBe("Releasing your reserved items");
    expect(s.outcome).toMatchObject({ kind: "failed", failure: { kind: "sold_out", failedStep: "RESERVING" } });
  });
  it("declined: CHARGING fails and the undo step is shown", () => {
    const s = sagaFrom(ORDER, DECLINED);
    expect(statuses(s)).toEqual(["VALIDATING:done", "RESERVING:done", "CHARGING:failed", "CONFIRMING:pending", "COMPENSATING:done"]);
    expect(s.outcome).toMatchObject({ kind: "failed", failure: { kind: "declined", failedStep: "CHARGING" } });
  });
  it("recovered failure: the step that was running is marked failed when the final event says so", () => {
    const s = sagaFrom(ORDER, RECOVERED_FAILED);
    expect(s.steps.CHARGING.status).toBe("failed");
    expect(s.outcome).toMatchObject({ kind: "failed", failure: { kind: "interrupted", failedStep: "CHARGING" } });
  });
  it("shows the undo step while it is still running, before the final event", () => {
    const s = sagaFrom(ORDER, DECLINED.slice(0, 7));
    expect(sagaRows(s).at(-1)).toMatchObject({ key: "COMPENSATING", status: "active", message: "Releasing reserved items", compensation: true });
    expect(s.outcome).toBeNull();
  });
  it("uses refund wording when money had been taken", () => {
    const afterCharge = sagaFrom(ORDER, [...SUCCESS.slice(0, 6), step("CONFIRMING", "FAILED"), step("COMPENSATING", "STARTED")]);
    expect(sagaRows(afterCharge).at(-1)?.label).toBe("Refunding your card and releasing your items");
  });
});

describe("classification", () => {
  it("uses shortages first, then the reason, then the failed step", () => {
    expect(classifyFailure("anything", [{ sku_id: "S", requested: 1, available: 0 }], null)).toBe("sold_out");
    expect(classifyFailure("payment declined: card declined by issuer", [], null)).toBe("declined");
    expect(classifyFailure("not enough stock", [], null)).toBe("sold_out");
    expect(classifyFailure("recovered after crash", [], null)).toBe("interrupted");
    expect(classifyFailure("", [], "RESERVING")).toBe("sold_out");
    expect(classifyFailure("", [], "CHARGING")).toBe("declined");
    expect(classifyFailure("weird", [], "VALIDATING")).toBe("other");
  });
});

describe("robustness: replays, duplicates, out-of-order, other orders", () => {
  it("the same events twice give the same state", () => {
    expect(statuses(sagaFrom(ORDER, [...SUCCESS, ...SUCCESS]))).toEqual(statuses(sagaFrom(ORDER, SUCCESS)));
    expect(sagaFrom(ORDER, [...DECLINED, ...DECLINED]).outcome).toEqual(sagaFrom(ORDER, DECLINED).outcome);
  });
  it("a late 'started' never un-finishes a step", () => {
    const s = sagaFrom(ORDER, [step("RESERVING", "SUCCEEDED"), step("RESERVING", "STARTED"), step("VALIDATING", "STARTED")]);
    expect(statuses(s)).toEqual(["VALIDATING:done", "RESERVING:done", "CHARGING:pending", "CONFIRMING:pending"]);
  });
  it("a step event implies the earlier steps are done even if their events were missed", () => {
    expect(statuses(sagaFrom(ORDER, [step("CHARGING", "STARTED")]))).toEqual(["VALIDATING:done", "RESERVING:done", "CHARGING:active", "CONFIRMING:pending"]);
  });
  it("a done event alone completes everything", () => {
    expect(statuses(sagaFrom(ORDER, [done]))).toEqual(["VALIDATING:done", "RESERVING:done", "CHARGING:done", "CONFIRMING:done"]);
  });
  it("the first final event wins; later steps or a contradicting final are ignored", () => {
    const s = sagaFrom(ORDER, [...SUCCESS, step("CHARGING", "FAILED"), failed("late")]);
    expect(s.outcome?.kind).toBe("success");
    expect(s.steps.CHARGING.status).toBe("done");
    expect(sagaFrom(ORDER, [...DECLINED, done]).outcome?.kind).toBe("failed");
  });
  it("ignores events of other orders", () => {
    const s = sagaFrom(ORDER, [step("CHARGING", "STARTED", "", "O-1"), failed("x", undefined, "O-2")]);
    expect(s).toEqual(initialSaga(ORDER));
  });
  it("each prefix of a recorded run is a valid state (nothing moves backwards)", () => {
    const rank = { pending: 0, active: 1, done: 2, failed: 2 } as const;
    for (const seq of [SUCCESS, SOLD_OUT, DECLINED, RECOVERED_FAILED]) {
      let prev = initialSaga(ORDER);
      for (const e of seq) {
        const next = reduceSaga(prev, e);
        for (const key of ["VALIDATING", "RESERVING", "CHARGING", "CONFIRMING"] as const) {
          expect(rank[next.steps[key].status]).toBeGreaterThanOrEqual(rank[prev.steps[key].status]);
        }
        prev = next;
      }
    }
  });
});

// The saga stream broke mid-checkout: the outcome is UNKNOWN until something settles it.
const LOST_MESSAGE = "We lost contact while placing your order. You will only be charged if it is confirmed.";
const lost = (order = ORDER, message = LOST_MESSAGE): CheckoutEvent => ({ kind: "interrupted", data: { order_id: order, message } });
const UP_TO_CHARGING: CheckoutEvent[] = [
  step("VALIDATING", "STARTED"), step("VALIDATING", "SUCCEEDED"), step("RESERVING", "STARTED"), step("RESERVING", "SUCCEEDED"), step("CHARGING", "STARTED", "Charging card"),
];

describe("interrupted (outcome unknown)", () => {
  it("keeps the steps exactly as they were and claims neither success nor failure", () => {
    const before = sagaFrom(ORDER, UP_TO_CHARGING);
    const s = reduceSaga(before, lost());
    expect(statuses(s)).toEqual(statuses(before));
    expect(s.steps).toEqual(before.steps);
    expect(s.outcome).toBeNull();
    expect(s.interrupted).toEqual({ message: LOST_MESSAGE });
    expect(sagaRows(s).some((r) => r.compensation)).toBe(false);
  });
  it("is not final: a later done settles it as a success and clears the notice", () => {
    const s = sagaFrom(ORDER, [...UP_TO_CHARGING, lost(), done]);
    expect(s.outcome?.kind).toBe("success");
    expect(s.interrupted).toBeNull();
  });
  it("a later failed settles it as a failure and clears the notice", () => {
    const s = sagaFrom(ORDER, [...UP_TO_CHARGING, lost(), failed("payment declined: card declined by issuer")]);
    expect(s.outcome).toMatchObject({ kind: "failed", failure: { kind: "declined" } });
    expect(s.interrupted).toBeNull();
  });
  it("a step event after it means we are in contact again, so the notice goes", () => {
    const s = sagaFrom(ORDER, [...UP_TO_CHARGING, lost(), step("CHARGING", "SUCCEEDED")]);
    expect(s.interrupted).toBeNull();
    expect(s.steps.CHARGING.status).toBe("done");
  });
  it("is idempotent, keeps the newest message, and ignores other orders and anything after the final event", () => {
    const twice = sagaFrom(ORDER, [...UP_TO_CHARGING, lost(), lost()]);
    expect(twice.interrupted).toEqual({ message: LOST_MESSAGE });
    expect(sagaFrom(ORDER, [...UP_TO_CHARGING, lost(ORDER, "first"), lost(ORDER, "second")]).interrupted).toEqual({ message: "second" });
    expect(sagaFrom(ORDER, [...UP_TO_CHARGING, lost("O-OTHER")]).interrupted).toBeNull();
    expect(sagaFrom(ORDER, [...SUCCESS, lost()]).interrupted).toBeNull();
  });
  it("works as the very first event (a refresh that only replays the interruption)", () => {
    const s = sagaFrom(ORDER, [lost()]);
    expect(s.interrupted).not.toBeNull();
    expect(s.outcome).toBeNull();
    expect(statuses(s)).toEqual(["VALIDATING:pending", "RESERVING:pending", "CHARGING:pending", "CONFIRMING:pending"]);
  });
});

describe("orderStatusToEvent (settling an interrupted order from its record)", () => {
  const rec = (status: Order["status"], failure_reason = ""): Order => ({ ...order, status, failure_reason });
  it("PENDING is still unknown", () => {
    expect(orderStatusToEvent(rec("PENDING"))).toBeNull();
  });
  it("CONFIRMED and later are a success carrying the order", () => {
    for (const status of ["CONFIRMED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"] as const) {
      expect(orderStatusToEvent(rec(status))).toEqual({ kind: "done", data: { order: rec(status) } });
    }
  });
  it("FAILED is a failure with the order's own reason", () => {
    expect(orderStatusToEvent(rec("FAILED", "payment_declined: card declined"))).toEqual({ kind: "failed", data: { order_id: ORDER, reason: "payment_declined: card declined" } });
  });
  it("an interrupted order that recovery then fails shows the failure, and one that recovery confirms shows the order", () => {
    const failedState = sagaFrom(ORDER, [...UP_TO_CHARGING, lost(), orderStatusToEvent(rec("FAILED", "Checkout was interrupted and recovered")) ?? lost()]);
    expect(failedState.outcome?.kind).toBe("failed");
    const okState = sagaFrom(ORDER, [...UP_TO_CHARGING, lost(), orderStatusToEvent(rec("CONFIRMED")) ?? lost()]);
    expect(okState.outcome?.kind).toBe("success");
  });
});

describe("readableReason", () => {
  it("turns server codes into sentences and drops bare codes", () => {
    expect(readableReason("payment declined: card declined by issuer")).toBe("Card declined by issuer");
    expect(readableReason("payment_declined: insufficient funds")).toBe("Insufficient funds");
    expect(readableReason("not enough stock")).toBe("Not enough stock");
    expect(readableReason("out_of_stock")).toBeNull();
    expect(readableReason("")).toBeNull();
    expect(readableReason("Checkout was interrupted and recovered by the system")).toBe("Checkout was interrupted and recovered by the system");
  });
});

describe("failure wording", () => {
  const names: Record<string, string> = { "SKU-1": "Rain Jacket (M, navy)", "SKU-2": "Canvas Cap (ONE, red)" };
  const nameOf = (id: string) => names[id] ?? id;
  const failureOf = (events: CheckoutEvent[]) => {
    const o = sagaFrom(ORDER, events).outcome;
    if (!o || o.kind !== "failed") throw new Error("expected a failure");
    return describeFailure(o.failure, nameOf);
  };
  it("sold out lists each shortage with the product name and 'only N left' or 'none left'", () => {
    const v = failureOf(SOLD_OUT);
    expect(v.title).toBe("Some items sold out");
    expect(v.details).toEqual([
      "Rain Jacket (M, navy): only 1 left (you asked for 2)",
      "Canvas Cap (ONE, red): none left (you asked for 1)",
    ]);
    expect(v.reassurance).toContain("Nothing was charged");
    expect(v.next).toBe("review_cart");
  });
  it("declined offers another method and says the cart is intact", () => {
    const v = failureOf(DECLINED);
    expect(v.title).toBe("Your card was declined");
    expect(v.next).toBe("choose_method");
    expect(v.details).toEqual(["Card declined by issuer"]);
    expect(v.reassurance).toMatch(/cart is still intact/);
  });
  it("a recovered failure says the order was cancelled and refunded, not 'nothing was charged'", () => {
    const v = failureOf(RECOVERED_FAILED);
    expect(v.title).toBe("The order was not completed");
    expect(v.reassurance).toMatch(/cancelled/);
    expect(v.reassurance).toMatch(/refunded/);
    expect(v.reassurance).not.toMatch(/nothing was charged/i);
  });
});
