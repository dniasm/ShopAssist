/* The route: what only ShopAssist has to draw.
 * Every question travels REST > WebSocket > gRPC and back. The route shows that trip the way a shop shows a parcel:
 * a line with one node per real event, filled in live as the events arrive (hollow = still to come, blue = where it is now).
 * The order stepper in render.js uses the same line-and-nodes language.
 */
import { el, serviceLabel } from "./dom.js";

const STATE_WORD = { done: "done", active: "in progress", pending: "not yet", error: "failed" };

function entityLine(entities) {
  const parts = [];
  const add = (k, v) => parts.push(el("span", { class: "ent" }, el("span", { class: "muted", text: `${k} ` }), String(v)));
  for (const [key, value] of Object.entries(entities || {})) {
    if (value === null || value === undefined) continue;
    if (key === "keywords" && Array.isArray(value)) value.forEach((w) => add("keyword", w));
    else if (key === "max_price") add(key, `$${Number(value).toFixed(2)}`);
    else add(key, value);
  }
  return parts;
}

/** The detail under the Intent node: the intent id, where it was decided (llm or rules) and what was extracted. */
export function intentDetail(intent, source, entities) {
  return el("div", { class: "hop-detail" },
    el("span", { class: "hop-line" }, el("span", { class: "hop-strong", text: String(intent) }),
      el("span", { class: "pill", text: String(source) })),
    el("span", { class: "hop-ents" }, entityLine(entities)));
}

function hopEl(n) {
  return el("li", { class: "hop", dataset: { state: n.state }, "aria-current": n.state === "active" ? "step" : false },
    el("span", { class: "hop-dot", "aria-hidden": "true" }),
    el("div", { class: "hop-body" },
      el("span", { class: "hop-title" }, n.title, el("span", { class: "sr-only", text: ` (${STATE_WORD[n.state]})` })),
      n.sub && el("span", { class: "hop-sub", text: n.sub }),
      n.detail),
    el("span", { class: "hop-ms", text: n.ms === null || n.ms === undefined ? "" : `+${Math.round(n.ms)} ms` }));
}

/** A route for one question. Mutate `nodes` (and `answer`) through the methods, then everything redraws. */
export function createRoute() {
  const nodes = [];
  const answer = { title: "Answer sent", sub: "WebSocket", state: "pending", ms: null };
  const list = el("ol", { class: "trace", "aria-label": "Route of this question" });
  const summary = el("span", { class: "route-summary", text: "running…" });
  const toggle = el("button", { type: "button", class: "route-toggle", "aria-expanded": "true" },
    el("span", { class: "caret", "aria-hidden": "true" }), el("span", { class: "route-title", text: "Route" }), summary);
  const root = el("aside", { class: "route", "aria-label": "How this answer was routed" }, toggle, list);

  const setOpen = (open) => { toggle.setAttribute("aria-expanded", String(open)); list.hidden = !open; };
  toggle.addEventListener("click", () => setOpen(toggle.getAttribute("aria-expanded") !== "true"));

  const draw = () => list.replaceChildren(...[...nodes, answer].map(hopEl));

  const route = {
    root, nodes, answer, setOpen,
    /** Add a node, marking the one before it done. */
    push(node) {
      const last = nodes[nodes.length - 1];
      if (last && last.state === "active") last.state = "done";
      nodes.push(node);
      draw();
      return node;
    },
    find: (key) => nodes.find((n) => n.key === key),
    /** Everything up to here is done; the answer node takes the final state. */
    finish(state, ms, sub) {
      nodes.forEach((n) => { if (n.state === "active") n.state = state === "error" ? "error" : "done"; });
      answer.state = state === "error" ? "error" : "done";
      answer.ms = ms;
      if (sub) answer.sub = sub;
      draw();
    },
    setSummary(text) { summary.textContent = text; },
    draw,
  };
  draw();
  return route;
}

/** The empty-state route: the same line and nodes, all still to come, so the first screen shows what an answer will bring. */
export function ghostRoute() {
  const route = createRoute();
  route.nodes.push(
    { key: "accepted", title: "Question accepted", sub: "REST \u00b7 POST /query", state: "pending" },
    { key: "intent", title: "Intent detected", sub: "WebSocket", state: "pending" },
    { key: "services", title: "Services called", sub: "gRPC", state: "pending" });
  route.setSummary("waiting");
  route.draw();
  return route;
}

export { serviceLabel };
