/* One poller for live system status, shared by the header readout and the System page.
 * Every 10 s it asks the orchestrator two things: GET /services/health (a short gRPC call per service) and
 * GET /llm/status (the key pool). Subscribers get the whole state each time; nothing here touches the DOM.
 */
import { getJson } from "./api.js";

export const POLL_MS = 10000;
const SERVICES = ["product", "inventory", "order"];

export const state = {
  health: null,        // last good /services/health body
  llm: null,           // last good /llm/status body
  healthError: false,  // the last attempt failed (offline, timeout, 5xx)
  llmError: false,
  checkedAt: null,     // Date of the last attempt
  lastGoodAt: null,    // Date of the last time health succeeded
  loaded: false,       // false until the first attempt finishes
  history: Object.fromEntries(SERVICES.map((n) => [n, []])), // latency (ms) of each successful probe this visit
};

const listeners = new Set();
let timer = null;
let inflight = null;

function notify() { listeners.forEach((fn) => fn(state)); }

async function poll() {
  if (inflight) return inflight;
  inflight = (async () => {
    const [health, llm] = await Promise.allSettled([getJson("/services/health", 5000), getJson("/llm/status")]);
    state.checkedAt = new Date();
    state.loaded = true;
    state.healthError = health.status === "rejected";
    state.llmError = llm.status === "rejected";
    if (!state.healthError) {
      state.health = health.value;
      state.lastGoodAt = state.checkedAt;
      for (const s of health.value.services) {
        if (s.status === "up" && typeof s.latency_ms === "number") state.history[s.name]?.push(s.latency_ms);
      }
    }
    if (!state.llmError) state.llm = llm.value;
    notify();
  })().finally(() => { inflight = null; });
  return inflight;
}

/** Call fn(state) now and after every poll. Returns an unsubscribe function. Starts polling on first use. */
export function subscribe(fn) {
  listeners.add(fn);
  fn(state);
  if (!timer) { poll(); timer = setInterval(poll, POLL_MS); }
  return () => listeners.delete(fn);
}

/** Poll right now (the "Check now" button). */
export const refresh = poll;

/** Average of this visit's probes for one service, or null before the first success. */
export function average(name) {
  const xs = state.history[name] || [];
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
}
