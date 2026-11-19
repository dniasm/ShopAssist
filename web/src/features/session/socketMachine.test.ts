import { describe, expect, it } from "vitest";
import type { SocketMessage } from "../../api/types";
import {
  BACKOFF_MAX_MS, CLOSE_UNAUTHENTICATED, backoffDelay, initialMachineState, reduceMachine,
  type MachineEffect, type MachineEvent, type MachineState,
} from "./socketMachine";

const TS = "2026-01-01T00:00:00Z";
const USER = { user_id: "U-03", name: "Priya Raman", email: "p@example.com", city: "Seattle" };
/** A valid message of the given channel. System messages carry seq 0 in the real contract, whatever seq is passed for them. */
function env(seq: number, channel: SocketMessage["channel"] = "checkout", type = "step"): SocketMessage {
  if (channel === "system") {
    return type === "hello"
      ? { seq, ts: TS, channel, type: "hello", data: { user: USER, seq: 12 } }
      : { seq, ts: TS, channel, type: "resync", data: {} };
  }
  if (channel === "order") return { seq, ts: TS, channel, type: "status", data: { order_id: "O-1", status: "CONFIRMED", message: "", event_id: seq } };
  return { seq, ts: TS, channel: "checkout", type: "step", data: { order_id: "O-1", step: "VALIDATING", state: "STARTED", message: "" } };
}

/** Feeds events through the machine, collecting every effect. */
function run(events: MachineEvent[], from: MachineState = initialMachineState()) {
  let state = from;
  const effects: MachineEffect[] = [];
  for (const e of events) {
    const step = reduceMachine(state, e);
    state = step.state;
    effects.push(...step.effects);
  }
  return { state, effects };
}
const delivered = (effects: MachineEffect[]) => effects.flatMap((e) => (e.type === "deliver" ? [e.envelope.seq] : []));
const connected = () => run([{ type: "start" }, { type: "opened" }]).state;

describe("backoff", () => {
  it("climbs 1s, 2s, 4s, 8s and stops at 15s", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(backoffDelay)).toEqual([1000, 2000, 4000, 8000, 15000, 15000, 15000]);
    expect(backoffDelay(50)).toBe(BACKOFF_MAX_MS);
  });

  it("schedules each retry with the next delay and reconnects with after=lastSeq", () => {
    const { state, effects } = run([
      { type: "start" }, { type: "opened" }, { type: "message", envelope: env(7) },
      { type: "closed", code: 1006 }, { type: "timer" },       // retry 1 (1 s)
      { type: "closed", code: 1006 }, { type: "timer" },       // retry 2 (2 s)
      { type: "closed", code: 1006 }, { type: "timer" },       // retry 3 (4 s)
    ]);
    expect(effects.filter((e) => e.type === "schedule")).toEqual([
      { type: "schedule", delayMs: 1000 }, { type: "schedule", delayMs: 2000 }, { type: "schedule", delayMs: 4000 },
    ]);
    expect(effects.filter((e) => e.type === "open")).toEqual([
      { type: "open", after: 0 }, { type: "open", after: 7 }, { type: "open", after: 7 }, { type: "open", after: 7 },
    ]);
    expect(state.status).toBe("connecting");
  });

  it("a successful open resets the ladder", () => {
    const { effects } = run([
      { type: "start" }, { type: "closed", code: 1006 }, { type: "timer" }, { type: "closed", code: 1006 }, { type: "timer" },
      { type: "opened" }, { type: "closed", code: 1006 },
    ]);
    expect(effects.filter((e) => e.type === "schedule").map((e) => (e.type === "schedule" ? e.delayMs : 0))).toEqual([1000, 2000, 1000]);
  });

  it("ignores a timer that fires when it is not waiting to retry", () => {
    expect(run([{ type: "timer" }]).effects).toEqual([]);
    expect(run([{ type: "start" }, { type: "opened" }, { type: "timer" }]).state.status).toBe("open");
  });
});

describe("event delivery", () => {
  it("delivers in order and advances lastSeq", () => {
    const { state, effects } = run([{ type: "start" }, { type: "opened" }, ...[1, 2, 3].map((n) => ({ type: "message" as const, envelope: env(n) }))]);
    expect(delivered(effects)).toEqual([1, 2, 3]);
    expect(state.lastSeq).toBe(3);
  });

  it("drops duplicates and out-of-order (older) events", () => {
    const { state, effects } = run([
      ...[1, 2, 2, 5, 3, 4, 5, 6].map((n) => ({ type: "message" as const, envelope: env(n) })),
    ], connected());
    expect(delivered(effects)).toEqual([1, 2, 5, 6]);
    expect(state.lastSeq).toBe(6);
  });

  it("accepts gaps (seq is increasing, not dense)", () => {
    const { effects } = run([10, 40].map((n) => ({ type: "message" as const, envelope: env(n) })), connected());
    expect(delivered(effects)).toEqual([10, 40]);
  });

  it("replays after a reconnect without duplicating what was already delivered", () => {
    const first = run([{ type: "message", envelope: env(1) }, { type: "message", envelope: env(2) }, { type: "closed", code: 1006 }, { type: "timer" }, { type: "opened" }], connected());
    expect(first.effects.find((e) => e.type === "open")).toEqual({ type: "open", after: 2 });
    // the server replays seq > 2, but a lagging proxy might repeat 2 as well
    const replay = run([2, 3, 4].map((n) => ({ type: "message" as const, envelope: env(n) })), first.state);
    expect(delivered(replay.effects)).toEqual([3, 4]);
  });

  it("ignores messages that arrive after stop", () => {
    const { effects } = run([{ type: "stop" }, { type: "message", envelope: env(9) }], connected());
    expect(delivered(effects)).toEqual([]);
  });
});

describe("system messages", () => {
  it("delivers hello without moving lastSeq", () => {
    const { state, effects } = run([{ type: "message", envelope: env(0, "system", "hello") }, { type: "message", envelope: env(4) }], connected());
    expect(effects.map((e) => (e.type === "deliver" ? e.envelope.type : e.type))).toEqual(["hello", "step"]);
    expect(state.lastSeq).toBe(4);
  });

  it("seq 0 on system messages is not a duplicate and never resets lastSeq (hello and resync can repeat at any point)", () => {
    const start = run([{ type: "message", envelope: env(41) }], connected()).state;
    const { state, effects } = run([
      { type: "message", envelope: env(0, "system", "hello") },
      { type: "message", envelope: env(0, "system", "resync") },
      { type: "message", envelope: env(0, "system", "hello") },
      { type: "message", envelope: env(42) },
    ], start);
    expect(effects.map((e) => (e.type === "deliver" ? e.envelope.type : e.type))).toEqual(["hello", "resync", "hello", "step"]);
    expect(state.lastSeq).toBe(42);
    expect(run([{ type: "closed", code: 1006 }, { type: "timer" }], state).effects.find((e) => e.type === "open")).toEqual({ type: "open", after: 42 });
  });

  it("delivers resync (so the app refetches), counts it, and keeps lastSeq for the replay that follows", () => {
    const start = run([{ type: "message", envelope: env(5) }], connected()).state;
    const { state, effects } = run([
      { type: "message", envelope: env(0, "system", "resync") },
      { type: "message", envelope: env(6) },
    ], start);
    expect(effects.map((e) => (e.type === "deliver" ? e.envelope.type : e.type))).toEqual(["resync", "step"]);
    expect(state.resyncs).toBe(1);
    expect(state.lastSeq).toBe(6);
  });
});

describe("close codes and stopping", () => {
  it("signs out on 4401 and never retries", () => {
    const { state, effects } = run([{ type: "closed", code: CLOSE_UNAUTHENTICATED }, { type: "timer" }], connected());
    expect(state.status).toBe("signed_out");
    expect(effects).toEqual([{ type: "sign_out" }]);
  });

  it("an intentional stop closes the socket and a following close does not schedule a retry", () => {
    const { state, effects } = run([{ type: "stop" }, { type: "closed", code: 1000 }], connected());
    expect(state.status).toBe("stopped");
    expect(effects).toEqual([{ type: "close" }]);
  });

  it("can start again after a stop, resuming from lastSeq", () => {
    const { effects } = run([{ type: "message", envelope: env(8) }, { type: "stop" }, { type: "start" }], connected());
    expect(effects.filter((e) => e.type === "open")).toEqual([{ type: "open", after: 8 }]);
  });

  it("start is idempotent while already connecting or open", () => {
    expect(run([{ type: "start" }, { type: "start" }]).effects).toEqual([{ type: "open", after: 0 }]);
    expect(run([{ type: "start" }], connected()).effects).toEqual([]);
  });
});
