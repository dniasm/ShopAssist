/* The Assistant page: ask a question, watch its route, read the answer.
 * REST starts a query; a WebSocket streams its events; each event fills in a node of the route and, at the end, the answer.
 */
import { el, announce, sleep } from "./dom.js";
import { API, WS_BASE } from "./api.js";
import { mountReadout } from "./shell.js";
import { createRoute, ghostRoute, intentDetail, serviceLabel } from "./route.js";
import { createTrackCard, questionList, renderAnswer } from "./render.js";

const UPDATE_MIN_GAP_MS = 500; // keeps a replayed burst of order_update visibly animated
const PHONE = "(max-width: 720px)";

const $ = (id) => document.getElementById(id);
const thread = $("thread");
const emptyState = $("empty");
let stickToBottom = true;

addEventListener("scroll", () => {
  stickToBottom = innerHeight + scrollY >= document.documentElement.scrollHeight - 160;
}, { passive: true });

function scrollToEnd(force = false) {
  if (!force && !stickToBottom) return;
  const behavior = matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior });
}

const elapsed = (view, ev) => (view.t0 === null ? 0 : Math.max(0, Date.parse(ev.ts) - view.t0));
const duration = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${Math.round(n)} ms`);

// ------------------------------------------------------------------ one exchange: the question, its route, its answer

function loadingAnswer() {
  // loading keeps the final layout: flat bars at their real size, no shimmer
  return el("div", { class: "loading", "aria-label": "Waiting for the answer" },
    el("span", { class: "skel", style: "width: 38%; height: 22px" }),
    el("span", { class: "skel", style: "width: 100%" }), el("span", { class: "skel", style: "width: 82%" }));
}

function createExchange(text) {
  const route = createRoute();
  route.push({ key: "accepted", title: "Question accepted", sub: "REST · POST /query", state: "active" });
  const answerEl = el("div", { class: "answer" }, loadingAnswer());
  thread.append(el("article", { class: "exchange" },
    el("p", { class: "asked", text }),
    el("section", { class: "reply", "aria-label": `Answer to: ${text}` }, route.root, answerEl)));
  return {
    text, route, answerEl, ask: submitQuery,
    t0: null, intent: null, entities: {}, source: null, calls: 0, updates: 0, tracker: null, done: false, chain: Promise.resolve(),
  };
}

function summarise(view, ev, finished) {
  const calls = `${view.calls} gRPC call${view.calls === 1 ? "" : "s"}`;
  view.route.setSummary(finished ? `${duration(elapsed(view, ev))} · ${calls}` : "running…");
}

const HANDLERS = {
  accepted(view, ev) {
    view.t0 = Date.parse(ev.ts);
    Object.assign(view.route.find("accepted"), { ms: 0 });
    view.route.push({ key: "intent", title: "Intent detected", sub: "WebSocket", state: "active" });
  },

  intent_detected(view, ev) {
    const { intent, entities, source } = ev.data;
    Object.assign(view, { intent, entities: entities || {}, source });
    Object.assign(view.route.find("intent"), { state: "done", ms: elapsed(view, ev), detail: intentDetail(intent, source, view.entities) });
    view.route.draw();
    if (intent === "track_order" && view.entities.order_id) {
      view.tracker = createTrackCard(view.entities.order_id);
      view.answerEl.replaceChildren(view.tracker.root);
    }
  },

  service_call(view, ev) {
    view.calls += 1;
    view.route.push({
      key: `${ev.data.service}.${ev.data.method}`, title: serviceLabel(ev.data.service),
      sub: `gRPC · ${ev.data.method}`, ms: elapsed(view, ev), state: "active",
    });
    summarise(view, ev, false);
  },

  partial_result() { /* shown as part of the final answer */ },

  order_update(view, ev) {
    if (!view.tracker) { // the intent event was missed: build the tracker lazily
      view.tracker = createTrackCard(ev.data.order_id);
      view.answerEl.replaceChildren(view.tracker.root);
    }
    view.tracker.update(ev.data);
    view.updates += 1;
    const hop = view.route.nodes[view.route.nodes.length - 1];
    if (hop && hop.sub && hop.sub.includes("TrackOrder")) {
      hop.detail = el("span", { class: "hop-ents", text: `${view.updates} update${view.updates === 1 ? "" : "s"} received` });
    }
    view.route.draw();
    return sleep(UPDATE_MIN_GAP_MS);
  },

  final_answer(view, ev) {
    const total = elapsed(view, ev);
    view.route.finish("done", total, "WebSocket · final_answer");
    summarise(view, ev, true);
    renderAnswer(view, ev.data);
    view.done = true;
    // open while running; on a phone fold the route away once the answer is in, so the answer is what you see
    if (matchMedia(PHONE).matches) view.route.setOpen(false);
    announce(String(ev.data.answer || ""));
  },

  error(view, ev) {
    fail(view, "Couldn't get an answer", String(ev.data.message || "Unknown error"), ev);
  },
};

function fail(view, title, message, ev) {
  if (view.done) return;
  view.done = true;
  view.route.finish("error", ev ? elapsed(view, ev) : null, "WebSocket · error");
  view.route.setSummary(ev ? `failed after ${duration(elapsed(view, ev))}` : "failed");
  view.answerEl.replaceChildren(el("div", { class: "error-card", role: "alert" },
    el("span", { class: "pill neg", text: "Failed" }),
    el("h3", { class: "answer-title", text: title }),
    el("p", { class: "err-msg", text: message }),
    el("button", { type: "button", class: "btn", text: "Retry", onclick: () => submitQuery(view.text) })));
  announce(`${title}. ${message}`);
}

// ------------------------------------------------------------------ query flow

function openSocket(view, url) {
  const ws = new WebSocket(url);
  ws.addEventListener("message", (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch { return; }
    // Events are handled one at a time, in order; order_update adds a short gap so bursts stay visible.
    view.chain = view.chain
      .then(() => { const handler = HANDLERS[ev.type]; return handler ? handler(view, ev) : undefined; })
      .catch((err) => console.error("event handler failed", ev.type, err))
      .then(() => scrollToEnd());
  });
  ws.addEventListener("close", () => {
    view.chain = view.chain.then(() => {
      if (!view.done) fail(view, "Connection lost", "The live connection closed before an answer arrived.");
    });
  });
}

async function submitQuery(rawText) {
  const text = String(rawText || "").trim();
  if (!text) return;
  emptyState.hidden = true;
  stickToBottom = true;
  const view = createExchange(text);
  scrollToEnd(true);
  try {
    const resp = await fetch(`${API}/query`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    });
    if (resp.status === 422) { fail(view, "Question rejected", "The server needs a non-empty question."); return; }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const { ws_url: wsUrl } = await resp.json();
    openSocket(view, WS_BASE + wsUrl);
  } catch (err) {
    fail(view, "Can't reach the backend", `The request to ${API} failed (${err.message}). Is the orchestrator running?`);
  }
}

// ------------------------------------------------------------------ start-up

function init() {
  mountReadout();
  emptyState.querySelector(".ghost-route").append(ghostRoute().root);
  $("questions").replaceChildren(...questionList(submitQuery).children);
  emptyState.querySelector(".empty-questions").append(questionList(submitQuery));

  const input = $("query");
  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value;
    input.value = "";
    submitQuery(text);
    input.focus();
  });
}

init();
