/* The session WebSocket's connection logic as a pure state machine: no sockets, no timers, no React. The provider feeds
 * it events (opened, message, closed, timer) and performs the effects it returns. That keeps the rules testable:
 *
 *  - one socket per signed-in tab; resume with ?after=<lastSeq> after every reconnect
 *  - reconnect delays 1 s, 2 s, 4 s ... capped at 15 s; a successful open resets the ladder
 *  - events are delivered once and in order: a seq <= lastSeq (duplicate or out-of-order) is dropped
 *  - close code 4401 means the session is not valid: sign out, never retry
 *  - system/hello and system/resync are control messages: they carry seq 0 (they are not in the server's replay log), are
 *    always delivered, and never move lastSeq, so a seq of 0 is neither a "duplicate" nor a reset of the resume point
 *    (after a resync the server replays from its buffer, and those events carry seqs above lastSeq) */

import type { WsMessage } from "../../api/types";

export const BACKOFF_FIRST_MS = 1000;
export const BACKOFF_MAX_MS = 15000;
export const CLOSE_UNAUTHENTICATED = 4401;

export type MachineStatus = "idle" | "connecting" | "open" | "backoff" | "stopped" | "signed_out";

export interface MachineState {
  status: MachineStatus;
  /** Highest seq delivered so far; sent as ?after= when (re)connecting. */
  lastSeq: number;
  /** Failed connection attempts since the last successful open. */
  attempt: number;
  /** How many system/resync messages arrived (the app refetches its data each time). */
  resyncs: number;
}

export type MachineEvent =
  | { type: "start" }
  | { type: "stop" }
  | { type: "opened" }
  | { type: "message"; envelope: WsMessage }
  | { type: "closed"; code: number }
  | { type: "timer" };

export type MachineEffect =
  | { type: "open"; after: number }
  | { type: "schedule"; delayMs: number }
  | { type: "deliver"; envelope: WsMessage }
  | { type: "close" }
  | { type: "sign_out" };

export interface MachineStep { state: MachineState; effects: MachineEffect[] }

export function initialMachineState(): MachineState {
  return { status: "idle", lastSeq: 0, attempt: 0, resyncs: 0 };
}

/** 1 s, 2 s, 4 s, 8 s, then 15 s forever. `attempt` is 0 for the first retry. */
export function backoffDelay(attempt: number): number {
  return Math.min(BACKOFF_MAX_MS, BACKOFF_FIRST_MS * 2 ** Math.max(0, attempt));
}

const none = (state: MachineState): MachineStep => ({ state, effects: [] });

export function reduceMachine(state: MachineState, event: MachineEvent): MachineStep {
  switch (event.type) {
    case "start": {
      if (state.status !== "idle" && state.status !== "stopped" && state.status !== "signed_out") return none(state);
      return { state: { ...state, status: "connecting", attempt: 0 }, effects: [{ type: "open", after: state.lastSeq }] };
    }

    case "stop":
      if (state.status === "stopped") return none(state);
      return { state: { ...state, status: "stopped", attempt: 0 }, effects: [{ type: "close" }] };

    case "opened":
      if (state.status !== "connecting") return none(state);
      return none({ ...state, status: "open", attempt: 0 });

    case "message": {
      if (state.status !== "open" && state.status !== "connecting") return none(state);
      const env = event.envelope;
      if (env.channel === "system") {
        const resyncs = env.type === "resync" ? state.resyncs + 1 : state.resyncs;
        return { state: { ...state, resyncs }, effects: [{ type: "deliver", envelope: env }] };
      }
      if (!Number.isFinite(env.seq) || env.seq <= state.lastSeq) return none(state);
      return { state: { ...state, lastSeq: env.seq }, effects: [{ type: "deliver", envelope: env }] };
    }

    case "closed": {
      if (state.status === "stopped" || state.status === "signed_out") return none(state);
      if (event.code === CLOSE_UNAUTHENTICATED) {
        return { state: { ...state, status: "signed_out" }, effects: [{ type: "sign_out" }] };
      }
      return {
        state: { ...state, status: "backoff", attempt: state.attempt + 1 },
        effects: [{ type: "schedule", delayMs: backoffDelay(state.attempt) }],
      };
    }

    case "timer":
      if (state.status !== "backoff") return none(state);
      return { state: { ...state, status: "connecting" }, effects: [{ type: "open", after: state.lastSeq }] };
  }
}
