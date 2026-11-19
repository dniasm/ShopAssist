import { describe, expect, it } from "vitest";
import type { ChatCard, ChatWsMessage, OrderStatusData } from "../../api/types";
import type { CheckoutEvent } from "../session/events";
import { chatReducer, initialChat, isWorking, type ChatAction, type ChatState } from "./transcript";

const TS = "2026-01-01T00:00:00Z";
const started = (seq: number, turn: string): ChatWsMessage => ({ seq, ts: TS, channel: "chat", type: "turn_started", data: { turn_id: turn } });
const call = (seq: number, turn: string, tool: string, args: Record<string, unknown> = {}): ChatWsMessage => ({ seq, ts: TS, channel: "chat", type: "tool_call", data: { turn_id: turn, tool, args } });
const result = (seq: number, turn: string, tool: string, summary = "ok"): ChatWsMessage => ({ seq, ts: TS, channel: "chat", type: "tool_result", data: { turn_id: turn, tool, summary } });
const say = (seq: number, turn: string, text: string, cards: ChatCard[] = []): ChatWsMessage => ({ seq, ts: TS, channel: "chat", type: "message", data: { turn_id: turn, text, cards, closing: "" } });
const propose = (seq: number, turn: string, id: string, action: "checkout" | "cancel_order", args: Record<string, unknown> = {}): ChatWsMessage =>
  ({ seq, ts: TS, channel: "chat", type: "proposal", data: { turn_id: turn, proposal_id: id, action, args, summary: `Do ${action}` } });
const done = (seq: number, turn: string, source: "llm" | "rules" = "llm"): ChatWsMessage => ({ seq, ts: TS, channel: "chat", type: "turn_done", data: { turn_id: turn, source } });
const oops = (seq: number, turn: string, message = "boom"): ChatWsMessage => ({ seq, ts: TS, channel: "chat", type: "error", data: { turn_id: turn, message } });
const chat = (message: ChatWsMessage): ChatAction => ({ type: "chat", message });

const step = (order: string, name: "VALIDATING" | "RESERVING" | "CHARGING" | "CONFIRMING", state: "STARTED" | "SUCCEEDED"): CheckoutEvent => ({ kind: "step", data: { order_id: order, step: name, state, message: "" } });
const checkout = (seq: number, event: CheckoutEvent): ChatAction => ({ type: "checkout", seq, event });
const status = (seq: number, order: string, s: string): ChatAction => ({ type: "order_status", seq, data: { order_id: order, status: s, message: s.toLowerCase(), event_id: seq } satisfies OrderStatusData });

/** Turns the tests talk about belong to the conversation (their questions were remembered); `fresh` is a conversation that owns none. */
const OWNED = initialChat({ texts: Object.fromEntries(["T", "T1", "A", "B"].map((id) => [id, "a question"])) });
const run = (actions: ChatAction[], from: ChatState = OWNED) => actions.reduce(chatReducer, from);
const fresh = () => initialChat();
const turn = (s: ChatState, id: string) => s.turns.find((t) => t.turnId === id);
const strip = (s: ChatState) => JSON.stringify(s.turns.map((t) => ({ ...t, key: "", proposals: t.proposals })));

describe("one turn", () => {
  it("builds from turn_started, a tool call and its result, the message with cards, and turn_done", () => {
    const cards: ChatCard[] = [{ kind: "products", data: { products: [] } }];
    const s = run([chat(started(1, "T1")), chat(call(2, "T1", "search_products", { query: "hoodie" })), chat(result(3, "T1", "search_products", "6 products")), chat(say(4, "T1", "Here you go", cards)), chat(done(5, "T1", "rules"))]);
    const t = turn(s, "T1");
    expect(t?.tools).toEqual([{ tool: "search_products", args: { query: "hoodie" }, summary: "6 products", state: "done" }]);
    expect(t?.messages).toEqual([{ text: "Here you go", cards, closing: "" }]);
    expect(t).toMatchObject({ done: true, source: "rules", error: null });
    expect(t && isWorking(t)).toBe(false);
  });
  it("is working until it is done or fails", () => {
    const s = run([chat(started(1, "T1")), chat(call(2, "T1", "get_cart"))]);
    expect(isWorking(s.turns[0]!)).toBe(true);
    expect(s.turns[0]?.tools[0]?.state).toBe("running");
    const failed = run([chat(oops(3, "T1", "model unavailable"))], s);
    expect(failed.turns[0]).toMatchObject({ done: true, error: "model unavailable" });
    expect(isWorking(failed.turns[0]!)).toBe(false);
  });
  it("keeps several tool steps and several messages in order", () => {
    const s = run([chat(call(1, "T", "search_products")), chat(result(2, "T", "search_products")), chat(call(3, "T", "get_stock", { product_id: "P-1" })), chat(result(4, "T", "get_stock")), chat(say(5, "T", "a")), chat(say(6, "T", "b"))]);
    expect(turn(s, "T")?.tools.map((x) => x.tool)).toEqual(["search_products", "get_stock"]);
    expect(turn(s, "T")?.messages.map((x) => x.text)).toEqual(["a", "b"]);
  });
});

describe("interleaved turns", () => {
  it("keeps each turn's events with that turn", () => {
    const s = run([
      chat(started(1, "A")), chat(started(2, "B")), chat(call(3, "A", "search_products", { query: "a" })), chat(call(4, "B", "get_cart")),
      chat(result(5, "B", "get_cart", "2 lines")), chat(result(6, "A", "search_products", "9 products")), chat(say(7, "B", "your cart")), chat(say(8, "A", "results")),
      chat(done(9, "A")), chat(done(10, "B", "rules")),
    ]);
    expect(s.turns.map((t) => t.turnId)).toEqual(["A", "B"]);
    expect(turn(s, "A")).toMatchObject({ source: "llm", messages: [{ text: "results" }], tools: [{ tool: "search_products", summary: "9 products" }] });
    expect(turn(s, "B")).toMatchObject({ source: "rules", messages: [{ text: "your cart" }], tools: [{ tool: "get_cart", summary: "2 lines" }] });
  });
});

describe("out-of-order events", () => {
  it("a tool_result before its tool_call: one step, with the arguments filled in when the call shows up", () => {
    const s = run([chat(result(2, "T", "get_stock", "3 sizes")), chat(call(1, "T", "get_stock", { product_id: "P-9" }))]);
    expect(turn(s, "T")?.tools).toEqual([{ tool: "get_stock", args: { product_id: "P-9" }, summary: "3 sizes", state: "done" }]);
  });
  it("a message and turn_done before turn_started still land on one turn", () => {
    const s = run([chat(done(3, "T")), chat(say(2, "T", "hi")), chat(started(1, "T"))]);
    expect(s.turns).toHaveLength(1);
    expect(turn(s, "T")).toMatchObject({ done: true, messages: [{ text: "hi" }] });
  });
  it("events that beat the POST response are merged into the turn the shopper started", () => {
    const base = run([{ type: "user_sent", localId: "L1", text: "any hoodies?", context: { path: "/shop" } }], fresh());
    expect(base.turns[0]).toMatchObject({ key: "L1", turnId: null, sending: true });
    const s = run([chat(started(1, "T1")), chat(say(2, "T1", "yes")), { type: "accepted", localId: "L1", turnId: "T1" }], base);
    expect(s.turns).toHaveLength(1);
    expect(s.turns[0]).toMatchObject({ key: "L1", turnId: "T1", sending: false, request: { text: "any hoodies?" }, messages: [{ text: "yes" }] });
    expect(s.texts.T1).toBe("any hoodies?");
  });
  it("the normal order (response first, then events) gives the same turn", () => {
    const s = run([{ type: "user_sent", localId: "L1", text: "q", context: { path: "/" } }, { type: "accepted", localId: "L1", turnId: "T1" }, chat(started(1, "T1")), chat(say(2, "T1", "a"))], fresh());
    expect(s.turns).toHaveLength(1);
    expect(s.turns[0]).toMatchObject({ key: "L1", turnId: "T1", messages: [{ text: "a" }] });
  });
  it("a POST that fails marks that turn as failed and nothing else", () => {
    const s = run([{ type: "user_sent", localId: "L1", text: "q", context: { path: "/" } }, { type: "send_failed", localId: "L1", message: "offline" }]);
    expect(s.turns[0]).toMatchObject({ sending: false, done: true, error: "offline" });
  });
});

describe("reconnect and refresh replay", () => {
  const events = [chat(started(1, "T1")), chat(call(2, "T1", "search_products")), chat(result(3, "T1", "search_products")), chat(say(4, "T1", "done")), chat(done(5, "T1"))];
  it("applying the same events again changes nothing", () => {
    const once = run(events);
    const twice = run(events, once);
    expect(strip(twice)).toBe(strip(once));
    expect(twice.maxSeq).toBe(5);
  });
  it("a partial replay overlapping what is already shown adds only the new events", () => {
    const first = run(events.slice(0, 3));
    const after = run(events.slice(1), first);          // the server resends from seq 2
    expect(strip(after)).toBe(strip(run(events)));
  });
  it("after a refresh the replay rebuilds the transcript, and the remembered words come back", () => {
    const live = run([{ type: "user_sent", localId: "L1", text: "any hoodies?", context: { path: "/shop" } }, { type: "accepted", localId: "L1", turnId: "T1" }, ...events], fresh());
    const rebuilt = run(events, initialChat({ texts: { T1: live.texts.T1 ?? "" } }));     // new tab state: only the persisted texts survive
    expect(rebuilt.turns).toHaveLength(1);
    expect(rebuilt.turns[0]?.request?.text).toBe("any hoodies?");
    expect(rebuilt.turns[0]?.messages).toEqual(live.turns[0]?.messages);
    expect(rebuilt.turns[0]?.tools).toEqual(live.turns[0]?.tools);
  });
});

describe("proposals", () => {
  const withProposal = (action: "checkout" | "cancel_order" = "checkout", args: Record<string, unknown> = { payment_method: "card_ok" }) =>
    run([chat(started(1, "T1")), chat(propose(2, "T1", "PR-1", action, args)), chat(done(3, "T1"))]);
  const proposal = (s: ChatState) => s.turns[0]?.proposals[0];

  it("arrives pending, once, even if the event is replayed", () => {
    const s = run([chat(propose(2, "T1", "PR-1", "checkout"))]);
    expect(s.turns[0]?.proposals).toHaveLength(1);
    expect(proposal(s)).toMatchObject({ id: "PR-1", state: "pending", follow: null });
    expect(run([chat(propose(2, "T1", "PR-1", "checkout"))], s).turns[0]?.proposals).toHaveLength(1);
    expect(run([chat(propose(9, "T1", "PR-1", "checkout"))], s).turns[0]?.proposals).toHaveLength(1);   // same id, new seq: still one
  });

  it("can be decided once: confirm, then no second confirm, no dismiss", () => {
    let s = withProposal();
    s = run([{ type: "proposal_confirming", id: "PR-1" }], s);
    expect(proposal(s)?.state).toBe("confirming");
    s = run([{ type: "proposal_confirming", id: "PR-1" }, { type: "proposal_dismissed", id: "PR-1" }], s);
    expect(proposal(s)?.state).toBe("confirming");
    s = run([{ type: "proposal_confirmed", id: "PR-1" }, { type: "proposal_confirming", id: "PR-1" }, { type: "proposal_dismissed", id: "PR-1" }], s);
    expect(proposal(s)?.state).toBe("confirmed");
  });

  it("dismiss is final too", () => {
    let s = run([{ type: "proposal_dismissed", id: "PR-1" }], withProposal());
    expect(proposal(s)?.state).toBe("dismissed");
    s = run([{ type: "proposal_confirming", id: "PR-1" }, { type: "proposal_confirmed", id: "PR-1" }], s);
    expect(proposal(s)?.state).toBe("dismissed");
  });

  it("a 409 'already done' is shown as handled, not as a second action; a failed attempt goes back to pending with a note", () => {
    let s = run([{ type: "proposal_confirming", id: "PR-1" }, { type: "proposal_handled", id: "PR-1", note: "Already confirmed" }], withProposal());
    expect(proposal(s)).toMatchObject({ state: "handled", note: "Already confirmed" });
    s = run([{ type: "proposal_confirming", id: "PR-1" }, { type: "proposal_failed", id: "PR-1", note: "Can't reach the shop" }], withProposal());
    expect(proposal(s)).toMatchObject({ state: "pending", note: "Can't reach the shop" });
  });

  it("confirmed checkout: its result is the next checkout the server runs, shown as steps, then the outcome", () => {
    let s = withProposal();
    s = run([chat(say(4, "T1", "x"))], s);                   // maxSeq = 4 at the click
    s = run([{ type: "proposal_confirming", id: "PR-1" }, { type: "proposal_confirmed", id: "PR-1" }], s);
    expect(proposal(s)).toMatchObject({ state: "confirmed", confirmedAfterSeq: 4, follow: { kind: "checkout", orderId: null } });
    s = run([
      checkout(5, step("O-77", "VALIDATING", "STARTED")), checkout(6, step("O-77", "VALIDATING", "SUCCEEDED")), checkout(7, step("O-77", "RESERVING", "STARTED")),
    ], s);
    const f = proposal(s)?.follow;
    expect(f && f.kind === "checkout" ? f.orderId : null).toBe("O-77");
    expect(f && f.kind === "checkout" ? f.saga?.steps.RESERVING.status : null).toBe("active");
    s = run([checkout(8, step("O-77", "RESERVING", "SUCCEEDED")), checkout(9, step("O-77", "CHARGING", "STARTED"))], s);
    const g = proposal(s)?.follow;
    expect(g && g.kind === "checkout" ? g.saga?.steps.CHARGING.status : null).toBe("active");
    expect(s.decided["PR-1"]).toMatchObject({ state: "confirmed", orderId: "O-77" });
  });

  it("a checkout from before the click, or of another order, is not its result", () => {
    let s = run([chat(say(4, "T1", "x"))], withProposal());
    s = run([{ type: "proposal_confirming", id: "PR-1" }, { type: "proposal_confirmed", id: "PR-1" }], s);
    s = run([checkout(3, step("O-OLD", "CHARGING", "STARTED"))], s);            // seq 3 <= 4: older than the click (a replay)
    expect(proposal(s)?.follow).toMatchObject({ kind: "checkout", orderId: null });
    s = run([checkout(5, step("O-77", "VALIDATING", "STARTED")), checkout(6, step("O-88", "VALIDATING", "STARTED"))], s);
    const f = proposal(s)?.follow;
    expect(f && f.kind === "checkout" ? f.orderId : null).toBe("O-77");        // O-88 does not steal it
  });

  it("a refresh after confirming: the persisted decision keeps it confirmed and the replay rebuilds the same outcome", () => {
    let live = run([chat(say(4, "T1", "x"))], withProposal());
    live = run([{ type: "proposal_confirming", id: "PR-1" }, { type: "proposal_confirmed", id: "PR-1" }, checkout(5, step("O-77", "CHARGING", "STARTED"))], live);
    const wire: ChatAction[] = [chat(started(1, "T1")), chat(propose(2, "T1", "PR-1", "checkout", { payment_method: "card_ok" })), chat(done(3, "T1")), chat(say(4, "T1", "x")), checkout(5, step("O-77", "CHARGING", "STARTED")), checkout(6, step("O-77", "CHARGING", "SUCCEEDED"))];
    const rebuilt = run(wire, initialChat({ texts: { T1: "checkout please" }, decided: live.decided }));
    expect(proposal(rebuilt)?.state).toBe("confirmed");
    const f = proposal(rebuilt)?.follow;
    expect(f && f.kind === "checkout" ? f.saga?.steps.CHARGING.status : null).toBe("done");
  });

  it("confirmed cancel: follows that order's status events after the click, forward only", () => {
    let s = withProposal("cancel_order", { order_id: "O-5" });
    s = run([{ type: "proposal_confirming", id: "PR-1" }, { type: "proposal_confirmed", id: "PR-1" }], s);   // maxSeq 3
    s = run([status(2, "O-5", "CONFIRMED")], s);                               // before the click: ignored
    expect(proposal(s)?.follow).toMatchObject({ kind: "order", orderId: "O-5", status: null });
    s = run([status(4, "O-5", "PACKED"), status(5, "O-9", "CANCELLED"), status(6, "O-5", "CANCELLED"), status(7, "O-5", "SHIPPED")], s);
    expect(proposal(s)?.follow).toMatchObject({ kind: "order", orderId: "O-5", status: "CANCELLED", message: "cancelled" });
  });
});

describe("conversations", () => {
  it("shows only the turns of this conversation: replayed events of other conversations stay out", () => {
    const old = [chat(started(1, "OLD")), chat(say(2, "OLD", "an old answer")), chat(done(3, "OLD"))];
    const s = run([...old, { type: "user_sent", localId: "L1", text: "new question", context: { path: "/" } }, { type: "accepted", localId: "L1", turnId: "NEW" }, chat(started(4, "NEW")), chat(say(5, "NEW", "new answer")), chat(done(6, "NEW"))], fresh());
    expect(s.turns.map((t) => t.turnId)).toEqual(["NEW"]);
    expect(s.turns[0]?.messages).toEqual([{ text: "new answer", cards: [], closing: "" }]);
  });
  it("events that beat the POST response wait, then join the turn once it is accepted", () => {
    let s = run([{ type: "user_sent", localId: "L1", text: "q", context: { path: "/" } }, chat(started(1, "T9")), chat(say(2, "T9", "early")), chat(done(3, "T9"))], fresh());
    expect(s.turns.map((t) => t.turnId)).toEqual([null]);                      // nothing shown for T9 yet
    s = run([{ type: "accepted", localId: "L1", turnId: "T9" }], s);
    expect(s.turns).toHaveLength(1);
    expect(s.turns[0]).toMatchObject({ key: "L1", turnId: "T9", done: true, messages: [{ text: "early" }] });
    expect(s.parked).toEqual({});
  });
  it("a replay of events that were already waiting does not duplicate them", () => {
    const early = [chat(say(2, "T9", "early")), chat(say(2, "T9", "early"))];
    let s = run([{ type: "user_sent", localId: "L1", text: "q", context: { path: "/" } }, ...early, { type: "accepted", localId: "L1", turnId: "T9" }], fresh());
    expect(s.turns[0]?.messages).toHaveLength(1);
    s = run(early, s);
    expect(s.turns[0]?.messages).toHaveLength(1);
  });
  it("a new conversation starts empty and does not pick up the old one's late events", () => {
    const live = run([chat(started(1, "T1")), chat(say(2, "T1", "hi"))]);
    const next = run([{ type: "reset" }, chat(say(3, "T1", "late answer of the old conversation"))], live);
    expect(next.turns).toHaveLength(0);
    expect(next.texts).toEqual({});
  });
  it("reset clears everything", () => {
    expect(run([chat(started(1, "T1")), { type: "reset" }]).turns).toHaveLength(0);
  });
});
