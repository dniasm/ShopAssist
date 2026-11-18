/* Answer bodies: one small render function per intent, reading the real final_answer.data shapes
 * (see orchestrator/dispatch.py and tests/test_frontend.py, which pins these shapes). Each renderer takes
 * { view, data, entities, answer } and returns a DOM node, or null to fall back to the plain answer text.
 */
import { el, money, fmtTime, isEmpty } from "./dom.js";

export const STEPS = ["PLACED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"];
export const STEP_LABEL = {
  PLACED: "Placed", PACKED: "Packed", SHIPPED: "Shipped", OUT_FOR_DELIVERY: "Out for delivery",
  DELIVERED: "Delivered", CANCELLED: "Cancelled",
};
const STATUS_PILL = { DELIVERED: "pos", CANCELLED: "neg" };
const LOW_STOCK = 2;
const SWATCH = {
  black: "#20242e", white: "#f7f7f8", blue: "#2f6fdb", navy: "#1e2a5a", red: "#d03a3a", green: "#2f9e5b",
  gray: "#8a92a2", grey: "#8a92a2", silver: "#c3c8d2", brown: "#7a5230", beige: "#dccdaa", pink: "#ee8fb0",
  yellow: "#f1c933", orange: "#ef8a2b", purple: "#7e57c2", gold: "#c9a227",
};

/** What people can ask, in the words of the trade. Used by the rail, the empty state and the "not supported" answer. */
export const QUESTIONS = [
  { q: "Where is my order 1042?", label: "Order status" },
  { q: "Track order 1043 live", label: "Live tracking" },
  { q: "Show me headphones under $100", label: "Product search" },
  { q: "Do you have the blue hoodie in size M?", label: "Stock check" },
  { q: "Tell me about P-1003", label: "Product details" },
  { q: "What's the weather?", label: "Not supported, to see the fallback" },
];

export function questionList(onAsk, extraClass = "") {
  return el("ul", { class: `questions ${extraClass}`.trim(), "aria-label": "Questions to try" },
    QUESTIONS.map(({ q, label }) => el("li", {},
      el("button", { type: "button", class: "question", onclick: () => onAsk(q) },
        el("span", { class: "question-text", text: q }), el("span", { class: "question-label", text: label })))));
}

// ------------------------------------------------------------------ shared building blocks

export const textAnswer = (answer) => el("p", { class: "plain-answer", text: answer || "" });

function swatchFor(color) {
  const key = String(color || "").toLowerCase();
  const node = el("span", { class: "swatch", title: String(color || ""), "aria-hidden": "true" });
  if (SWATCH[key]) node.style.backgroundColor = SWATCH[key];
  else node.classList.add(key === "clear" ? "clear" : "unknown");
  return node;
}

/** The order's progress: the same line-and-nodes language as the route. */
export function createStepper() {
  const items = STEPS.map((s) => el("li", { class: "step" },
    el("span", { class: "step-dot", "aria-hidden": "true" }), el("span", { class: "step-label", text: STEP_LABEL[s] })));
  const ol = el("ol", { class: "stepper", "aria-label": "Order progress" }, items);
  const cancel = el("p", { class: "cancel-note", role: "status", hidden: true, text: "This order was cancelled." });
  const root = el("div", { class: "progress" }, ol, cancel);

  function set(status) {
    const idx = STEPS.indexOf(status);
    const delivered = status === "DELIVERED";
    ol.classList.toggle("is-cancelled", status === "CANCELLED");
    cancel.hidden = status !== "CANCELLED";
    ol.style.setProperty("--progress", idx < 0 ? 0 : idx / (STEPS.length - 1));
    items.forEach((li, i) => {
      li.classList.toggle("done", idx >= 0 && (i < idx || (delivered && i === idx)));
      li.classList.toggle("current", i === idx && !delivered);
      if (i === idx) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
  }
  set(null);
  return { root, set };
}

function statusPill(status) {
  return el("span", { class: `pill ${STATUS_PILL[status] || ""}`.trim(), text: STEP_LABEL[status] || String(status) });
}

function sizeTiles(levels, entities) {
  if (!levels || levels.length === 0) {
    const wanted = entities && entities.size;
    return el("p", { class: "muted", text: wanted ? `Size ${wanted} is not carried.` : "No stock information." });
  }
  return el("ul", { class: "sizes", "aria-label": "Stock by size" }, levels.map((lv) => {
    const out = !(lv.quantity > 0);
    const low = !out && lv.quantity <= LOW_STOCK;
    return el("li", { class: `size${out ? " out" : ""}${low ? " low" : ""}` },
      el("span", { class: "size-name", text: lv.size === "ONE" ? "One size" : String(lv.size) }),
      el("span", { class: "size-qty", text: out ? "out of stock" : `${lv.quantity} in stock` }));
  }));
}

function relaxedNotice(data, entities) {
  if (!data.relaxed) return null;
  const dropped = (entities.keywords || []).map((k) => `'${k}'`).join(", ");
  const what = [entities.color, entities.category || "products"].filter(Boolean).join(" ");
  const price = typeof entities.max_price === "number" ? ` up to ${money(entities.max_price)}` : "";
  return el("p", { class: "notice", role: "note", text: `No exact match for ${dropped}, but here are ${what}${price}.` });
}

function productTile(p, onAsk) {
  return el("li", {},
    el("button", {
      type: "button", class: "tile", "aria-label": `${p.name}, ${money(p.price)}, ${p.product_id}. Ask for details.`,
      onclick: () => onAsk(`Tell me about ${p.product_id}`),
    },
      el("span", { class: "tile-top" }, swatchFor(p.color), el("span", { class: "muted small", text: `${p.color} ${p.category}` })),
      el("span", { class: "tile-name", text: String(p.name) }),
      el("span", { class: "tile-price", text: money(p.price) }),
      el("span", { class: "muted small", text: String(p.product_id) })));
}

// ------------------------------------------------------------------ renderers

function renderOrderStatus({ data }) {
  const o = data.order;
  if (!o) return null;
  const progress = createStepper();
  progress.set(o.status);
  const rows = (o.items || []).map((it) => el("tr", {},
    el("td", { text: String(it.name || it.product_id) }),
    el("td", { text: it.size === "ONE" ? "—" : String(it.size) }),
    el("td", { class: "n", text: String(it.quantity) }),
    el("td", { class: "n unit", text: money(it.unit_price) }),
    el("td", { class: "n", text: money(it.quantity * it.unit_price) })));
  return el("div", { class: "stack" },
    el("div", { class: "answer-head" },
      el("div", {}, el("h3", { class: "answer-title", text: `Order ${o.order_id}` }),
        el("p", { class: "muted small", text: `${o.customer_name} · updated ${fmtTime(o.updated_at)}` })),
      statusPill(o.status)),
    progress.root,
    el("div", { class: "table-wrap" }, el("table", { class: "items" },
      el("thead", {}, el("tr", {}, ["Item", "Size", "Qty", "Unit price", "Subtotal"].map((h, i) =>
        el("th", { class: [i > 1 ? "n" : "", i === 3 ? "unit" : ""].join(" ").trim(), text: h })))),
      el("tbody", {}, rows),
      el("tfoot", {}, el("tr", {}, el("td", { colspan: "3", text: "Total" }), el("td", { class: "unit" }),
        el("td", { class: "n total", text: money(o.total) }))))));
}

/** The live tracker for track_order: built when the intent arrives, updated per order_update. */
export function createTrackCard(orderId) {
  const progress = createStepper();
  const state = el("span", { class: "live-state" }, el("span", { class: "live-dot", "aria-hidden": "true" }), "Live");
  const slot = el("div", { class: "live-line" }, el("span", { class: "skel", style: "width: 160px" }),
    el("span", { class: "muted", text: "Waiting for the first update…" }));
  const timeline = el("ul", { class: "timeline", "aria-label": "Status history" });
  const root = el("div", { class: "stack" },
    el("div", { class: "answer-head" }, el("h3", { class: "answer-title", text: `Tracking order ${orderId}` }), state),
    progress.root, slot, timeline);

  const card = {
    root,
    update(u) {
      progress.set(u.status);
      const terminal = u.status === "DELIVERED" || u.status === "CANCELLED";
      slot.className = `live-line${terminal ? " done" : ""}`;
      slot.replaceChildren(el("span", { class: "live-message", text: String(u.message || STEP_LABEL[u.status] || u.status) }),
        el("span", { class: "muted small", text: fmtTime(u.timestamp) }));
      timeline.append(el("li", {}, el("span", { class: "when", text: fmtTime(u.timestamp) }),
        el("span", { text: `${STEP_LABEL[u.status] || u.status}: ${u.message || ""}` })));
      if (terminal) card.finish(u.status);
    },
    finish(status) {
      state.className = `pill ${STATUS_PILL[status] || ""}`.trim();
      state.replaceChildren(STEP_LABEL[status] || "Complete");
    },
  };
  return card;
}

function renderTrackOrder({ view, data }) {
  const updates = Array.isArray(data.updates) ? data.updates : [];
  if (updates.length === 0) return null; // not found / no id: plain text
  if (!view.tracker) { // the socket delivered everything at once: build it now
    view.tracker = createTrackCard(data.order_id);
    updates.forEach((u) => view.tracker.update(u));
  }
  view.tracker.finish(updates[updates.length - 1].status);
  return view.tracker.root;
}

function renderProductSearch({ view, data, entities }) {
  const products = Array.isArray(data.products) ? data.products : [];
  if (products.length === 0) return null;
  return el("div", { class: "stack" }, relaxedNotice(data, entities),
    el("p", { class: "muted", text: `${products.length} ${products.length === 1 ? "product" : "products"}. Pick one for its details.` }),
    el("ul", { class: "tiles", "aria-label": `${products.length} products` }, products.map((p) => productTile(p, view.ask))));
}

function renderStockCheck({ data, entities }) {
  const results = Array.isArray(data.results) ? data.results : [];
  if (results.length === 0) return null;
  return el("div", { class: "stack" }, relaxedNotice(data, entities),
    results.map((r) => el("section", { class: "stock-block" },
      el("div", { class: "answer-head" },
        el("h3", { class: "answer-title", text: String(r.product.name) }),
        el("span", { class: "muted small", text: `${r.product.product_id} · ${money(r.product.price)}` })),
      sizeTiles(r.stock, entities))));
}

function renderProductInfo({ data, entities }) {
  const p = data.product;
  if (!p) return null;
  return el("div", { class: "stack" },
    el("div", { class: "answer-head" },
      el("div", {}, el("h3", { class: "answer-title", text: String(p.name) }),
        el("p", { class: "muted small", text: `${p.product_id} · ${p.category}` })),
      el("span", { class: "detail-price", text: money(p.price) })),
    el("p", { class: "detail-colour" }, swatchFor(p.color), el("span", { text: String(p.color) })),
    el("p", { class: "detail-desc", text: String(p.description || "") }),
    el("h4", { class: "section-label", text: "Availability" }),
    sizeTiles(data.stock, entities));
}

function renderUnknown({ view, answer }) {
  return el("div", { class: "stack" }, textAnswer(answer),
    el("p", { class: "muted", text: "Questions it can answer:" }), questionList(view.ask, "compact"));
}

const RENDERERS = {
  order_status: renderOrderStatus,
  track_order: renderTrackOrder,
  product_search: renderProductSearch,
  stock_check: renderStockCheck,
  product_info: renderProductInfo,
  unknown: renderUnknown,
};

/** Fill view.answerEl for a finished query. Falls back to the plain answer text when the data is empty or malformed. */
export function renderAnswer(view, finalData) {
  const ctx = { view, data: finalData.data || {}, entities: view.entities, answer: finalData.answer };
  const render = RENDERERS[view.intent];
  let node = null;
  if (render && (!isEmpty(ctx.data) || view.intent === "unknown")) {
    try { node = render(ctx); } catch (err) { console.error("render failed, using plain text", err); }
  }
  view.answerEl.replaceChildren(node || textAnswer(finalData.answer));
}
