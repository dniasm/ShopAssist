import { describe, expect, it } from "vitest";
import type { SocketMessage } from "../../api/types";
import { appendEvent, eventsOn } from "./eventLog";
import { checkoutOrderId, parseSocketMessage, toCheckoutEvent, toNavigate, toOrderStatus } from "./events";

const frame = (o: unknown) => JSON.stringify(o);
const USER = { user_id: "U-03", name: "Priya Raman", email: "p@example.com", city: "Seattle" };
const orderJson = {
  order_id: "O-1", user_id: "U-03", status: "CONFIRMED", payment_id: "PAY-1", failure_reason: "", created_at: "a", updated_at: "b",
  lines: [{ sku_id: "SKU-1", product_id: "P-1", name: "Coat", category: "jackets", size: "M", color: "navy", quantity: 2, unit_price_cents: 5000, line_total_cents: 10000 }, "junk"],
  subtotal_cents: 10000, shipping_cents: 0, total_cents: 10000,
  history: [{ event_id: 7, order_id: "O-1", status: "CONFIRMED", message: "Paid", timestamp: "t" }],
};
const msg = (channel: string, type: string, data: unknown, seq = 1) => frame({ seq, ts: "t", channel, type, data });
const must = (raw: string): SocketMessage => {
  const m = parseSocketMessage(raw);
  if (!m) throw new Error("expected the frame to parse");
  return m;
};

describe("parseSocketMessage: system", () => {
  it("parses hello and resync, which carry seq 0", () => {
    expect(parseSocketMessage(msg("system", "hello", { user: USER, seq: 41 }, 0))).toEqual({ seq: 0, ts: "t", channel: "system", type: "hello", data: { user: USER, seq: 41 } });
    expect(parseSocketMessage(msg("system", "resync", {}, 0))).toEqual({ seq: 0, ts: "t", channel: "system", type: "resync", data: {} });
  });
  it("accepts a guest's hello, whose user is null", () => {
    expect(parseSocketMessage(msg("system", "hello", { user: null, seq: 3 }, 0))).toEqual({ seq: 0, ts: "t", channel: "system", type: "hello", data: { user: null, seq: 3 } });
  });
  it("refuses a hello without a user field", () => {
    expect(parseSocketMessage(msg("system", "hello", { seq: 1 }, 0))).toBeNull();
  });
});

describe("parseSocketMessage: checkout", () => {
  it("parses a step, leaving shortages undefined when the server omits them", () => {
    const m = must(msg("checkout", "step", { order_id: "O-1", step: "CHARGING", state: "STARTED", message: "Charging" }));
    expect(m).toMatchObject({ channel: "checkout", type: "step", data: { order_id: "O-1", step: "CHARGING", state: "STARTED", message: "Charging" } });
    expect(m.channel === "checkout" && m.type === "step" ? m.data.shortages : "wrong").toBeUndefined();
  });
  it("keeps shortages when present and drops malformed entries", () => {
    const m = must(msg("checkout", "step", { order_id: "O-1", step: "RESERVING", state: "FAILED", message: "", shortages: [{ sku_id: "SKU-1", requested: 3, available: 1 }, { nope: 1 }] }));
    expect(m.channel === "checkout" && m.type === "step" ? m.data.shortages : null).toEqual([{ sku_id: "SKU-1", requested: 3, available: 1 }]);
  });
  it("refuses steps and states outside the contract", () => {
    expect(parseSocketMessage(msg("checkout", "step", { order_id: "O-1", step: "FINISHED", state: "STARTED", message: "" }))).toBeNull();
    expect(parseSocketMessage(msg("checkout", "step", { order_id: "O-1", step: "CHARGING", state: "started", message: "" }))).toBeNull();
    expect(parseSocketMessage(msg("checkout", "step", { step: "CHARGING", state: "STARTED" }))).toBeNull();
  });
  it("parses done with the whole order, history included, and skips junk lines", () => {
    const m = must(msg("checkout", "done", { order: orderJson }));
    if (m.channel !== "checkout" || m.type !== "done") throw new Error("not a done");
    expect(m.data.order.lines).toHaveLength(1);
    expect(m.data.order.lines[0]?.category).toBe("jackets");
    expect(m.data.order.history).toEqual([{ event_id: 7, order_id: "O-1", status: "CONFIRMED", message: "Paid", timestamp: "t" }]);
    expect(parseSocketMessage(msg("checkout", "done", { order: { ...orderJson, status: "WEIRD" } }))).toBeNull();
    expect(parseSocketMessage(msg("checkout", "done", { order: { nope: 1 } }))).toBeNull();
  });
  it("parses failed (real reason strings) and interrupted", () => {
    expect(must(msg("checkout", "failed", { order_id: "O-1", reason: "payment declined: card declined by issuer" }))).toMatchObject({ type: "failed", data: { reason: "payment declined: card declined by issuer" } });
    expect(must(msg("checkout", "interrupted", { order_id: "O-1", message: "We lost contact" }))).toMatchObject({ type: "interrupted", data: { message: "We lost contact" } });
    expect(parseSocketMessage(msg("checkout", "interrupted", { message: "no order" }))).toBeNull();
  });
});

describe("parseSocketMessage: order and junk", () => {
  it("parses order status", () => {
    expect(must(msg("order", "status", { order_id: "O-1", status: "CONFIRMED", message: "Paid", event_id: 9 }))).toMatchObject({ channel: "order", type: "status", data: { order_id: "O-1", status: "CONFIRMED", event_id: 9 } });
  });
  it("rejects frames that are not part of the contract", () => {
    for (const raw of ["", "nope", "[]", "null", frame({ channel: "chat", type: "message", data: {} }), frame({ channel: "bogus", type: "x", data: {} }), frame({ channel: "checkout", type: "step" })]) {
      expect(parseSocketMessage(raw)).toBeNull();
    }
  });
  it("keeps a missing seq as NaN so the machine drops it", () => {
    const m = parseSocketMessage(frame({ channel: "checkout", type: "interrupted", data: { order_id: "O-1", message: "" } }));
    expect(Number.isNaN(m?.seq)).toBe(true);
  });
});

describe("narrowing helpers", () => {
  it("toCheckoutEvent maps checkout messages and nothing else", () => {
    const events = [
      must(msg("checkout", "step", { order_id: "O-1", step: "CHARGING", state: "STARTED", message: "" })),
      must(msg("checkout", "done", { order: orderJson })),
      must(msg("checkout", "failed", { order_id: "O-1", reason: "x" })),
      must(msg("checkout", "interrupted", { order_id: "O-1", message: "m" })),
    ].map((m) => toCheckoutEvent(m));
    expect(events.map((e) => e?.kind)).toEqual(["step", "done", "failed", "interrupted"]);
    for (const e of events) {
      if (!e) throw new Error("event did not map");
      expect(checkoutOrderId(e)).toBe("O-1");
    }
    expect(toCheckoutEvent(must(msg("system", "resync", {}, 0)))).toBeNull();
  });
  it("toOrderStatus maps the order channel only", () => {
    expect(toOrderStatus(must(msg("order", "status", { order_id: "O-1", status: "FAILED", message: "", event_id: 3 })))).toMatchObject({ order_id: "O-1", status: "FAILED" });
    expect(toOrderStatus(must(msg("checkout", "failed", { order_id: "O-1", reason: "x" })))).toBeNull();
  });
});

describe("event log", () => {
  const e = (seq: number, channel: "checkout" | "order"): SocketMessage => must(channel === "order"
    ? msg("order", "status", { order_id: "O-1", status: "CONFIRMED", message: "", event_id: seq }, seq)
    : msg("checkout", "interrupted", { order_id: "O-1", message: "" }, seq));
  it("keeps the newest events up to the limit and filters by channel", () => {
    let log: SocketMessage[] = [];
    for (let i = 1; i <= 7; i++) log = appendEvent(log, e(i, i % 2 ? "checkout" : "order"), 5);
    expect(log.map((x) => x.seq)).toEqual([3, 4, 5, 6, 7]);
    expect(eventsOn(log, "checkout").map((x) => x.seq)).toEqual([3, 5, 7]);
  });
});

describe("chat: navigate, conversation and checkout_redirect", () => {
  it("toNavigate returns a relative path", () => {
    expect(toNavigate(must(msg("chat", "navigate", { turn_id: "T1", path: "/checkout" })))).toBe("/checkout");
    expect(toNavigate(must(msg("chat", "navigate", { turn_id: "T1", path: "/p/P-1017?x=1" })))).toBe("/p/P-1017?x=1");
  });
  it("rejects absolute URLs and anything not starting with a single slash", () => {
    for (const path of ["https://evil.example", "//evil.example", "/\\evil", "cart", "", "javascript:alert(1)", "/a\nb", 5, null]) {
      expect(parseSocketMessage(msg("chat", "navigate", { turn_id: "T1", path }))).toBeNull();
    }
  });
  it("toNavigate ignores other messages", () => {
    expect(toNavigate(must(msg("chat", "turn_started", { turn_id: "T1" })))).toBeNull();
  });
  it("parses the conversation event and needs an id", () => {
    expect(must(msg("chat", "conversation", { turn_id: "T1", conversation_id: "C-9" }))).toMatchObject({ type: "conversation", data: { conversation_id: "C-9" } });
    expect(parseSocketMessage(msg("chat", "conversation", { turn_id: "T1" }))).toBeNull();
  });
  it("accepts checkout_redirect proposals and no longer the old checkout action", () => {
    expect(must(msg("chat", "proposal", { turn_id: "T1", proposal_id: "PR-1", action: "checkout_redirect", args: {}, summary: "s" }))).toMatchObject({ data: { action: "checkout_redirect" } });
    expect(parseSocketMessage(msg("chat", "proposal", { turn_id: "T1", proposal_id: "PR-1", action: "checkout", args: {}, summary: "s" }))).toBeNull();
  });
});
