/* Shared DOM and formatting helpers.
 *
 * Security rule for every file in frontend/js: everything that comes from the server (answers, product names,
 * entities, error messages, key labels) is untrusted. Build the DOM with createElement and textContent only;
 * never parse markup from a string. tests/test_frontend.py scans for violations.
 */

/** el("div", {class: "x", text: "hi", onclick: fn, dataset: {a: 1}}, child, [children]) */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child !== undefined && child !== null && child !== false) node.append(child); // strings become text nodes
  }
  return node;
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export const isEmpty = (obj) => !obj || (typeof obj === "object" && Object.keys(obj).length === 0);

const USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const INT = new Intl.NumberFormat("en-US");
export const money = (n) => (typeof n === "number" && isFinite(n) ? USD.format(n) : String(n ?? ""));
export const count = (n) => (typeof n === "number" && isFinite(n) ? INT.format(n) : "—");

/** Latency with one decimal below 100 ms, none above. A missing value is a dash, never a zero. */
export function ms(value) {
  if (typeof value !== "number" || !isFinite(value)) return "—";
  return value < 100 ? `${value.toFixed(1)} ms` : `${Math.round(value)} ms`;
}

export function fmtTime(ts) {
  const d = new Date(ts);
  if (isNaN(d)) return String(ts ?? "");
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/** "ProductService" -> "Product service" */
export const serviceLabel = (name) => String(name).replace(/Service$/, "").replace(/^(.)/, (c) => c.toUpperCase()) + " service";

export function announce(text) {
  const region = document.getElementById("announcer");
  if (!region) return;
  region.textContent = ""; // re-set so identical consecutive messages are announced again
  setTimeout(() => { region.textContent = text; }, 30);
}
