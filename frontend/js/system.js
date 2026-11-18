/* The System page: how it works, live.
 * Everything here comes from the shared poller (GET /services/health, GET /llm/status); nothing is typed in.
 */
import { el, ms as fmtMs, count, fmtTime } from "./dom.js";
import { API } from "./api.js";
import { mountReadout } from "./shell.js";
import { subscribe, refresh, average, POLL_MS } from "./status.js";

const SERVICES = [
  { name: "product", label: "Product service", db: "products.db" },
  { name: "inventory", label: "Inventory service", db: "inventory.db" },
  { name: "order", label: "Order service", db: "orders.db" },
];
const LIMITS = [["rpm", "Requests per minute"], ["rpd", "Requests per day"], ["tpm", "Tokens per minute"], ["tpd", "Tokens per day"]];
const LOW_HEADROOM = 0.2; // under 20% left a meter turns to the warning colour

const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ the finding (computed, never typed)

export function findingFor(s) {
  if (!s.loaded) return { bad: false, text: "Checking the services…" };
  if (s.healthError && !s.health) return { bad: true, text: `Can't reach the backend at ${API}. Is the orchestrator running?` };
  if (s.healthError) return { bad: true, text: `The backend stopped answering. Last good check ${fmtTime(s.lastGoodAt)}.` };
  const svcs = s.health.services;
  const down = svcs.filter((x) => x.status === "down");
  const parts = [];
  if (down.length === 0) {
    const slowest = Math.max(...svcs.map((x) => x.latency_ms));
    parts.push(`All ${svcs.length} services answer, the slowest in ${fmtMs(slowest)}.`);
  } else {
    const names = down.map((x) => SERVICES.find((d) => d.name === x.name).label).join(" and ");
    parts.push(`${names} ${down.length === 1 ? "is" : "are"} down (${down.map((x) => x.error).join(", ")}). Questions that need ${down.length === 1 ? "it" : "them"} will fail.`);
  }
  const llm = s.llm;
  if (llm && llm.enabled) {
    const ok = llm.keys.filter((k) => k.state === "ok").length;
    parts.push(`${ok} of ${llm.keys.length} Groq keys have headroom.`);
  } else if (llm) {
    parts.push("No Groq keys are set, so intents come from the rules.");
  }
  return { bad: down.length > 0, text: parts.join(" ") };
}

function renderTitle(s) {
  const f = findingFor(s);
  const node = $("finding");
  node.textContent = f.text;
  node.classList.toggle("finding-bad", f.bad);
  $("stamp").textContent = s.checkedAt ? `Checked ${fmtTime(s.checkedAt)}, every ${POLL_MS / 1000} s` : "";
}

// ------------------------------------------------------------------ (a) the diagram: status coloured onto the service nodes

function renderDiagram(s) {
  for (const def of SERVICES) {
    const node = document.querySelector(`.n-svc[data-service="${def.name}"]`);
    const entry = s.health && s.health.services.find((x) => x.name === def.name);
    const state = s.healthError && !entry ? "checking" : entry ? entry.status : "checking";
    node.dataset.state = state;
    node.querySelector(".svc-word").textContent = state === "up" ? "Up" : state === "down" ? "Down" : s.loaded ? "Unknown" : "Checking";
  }
}

// ------------------------------------------------------------------ shared: the error and empty frames

function retryBlock(message) {
  return el("div", { class: "block-error", role: "alert" }, el("span", { text: message }),
    el("button", { type: "button", class: "btn", text: "Retry", onclick: () => refresh() }));
}

function skeletonRows(n, columns) {
  return el("table", { class: "stack-table" }, el("tbody", {}, Array.from({ length: n }, () =>
    el("tr", { class: "skel-row" }, Array.from({ length: columns }, (_, i) => el("td", {}, el("span", { class: "skel", style: `width: ${i === 0 ? 70 : 50}%` })))))));
}

// ------------------------------------------------------------------ (b) service health

function serviceRow(def, entry, checks) {
  const up = entry && entry.status === "up";
  const avg = average(def.name);
  return el("tr", {},
    el("td", { "data-label": "Service" }, el("span", { class: "name", text: def.label }),
      el("span", { class: "cell-sub", text: entry ? entry.address : "" })),
    el("td", { "data-label": "Status" }, el("span", { class: `pill ${up ? "pos" : "neg"}`, text: up ? "Up" : "Down" }),
      !up && entry && entry.error && el("span", { class: "cell-sub", text: entry.error })),
    el("td", { class: "n", "data-label": "Latency" }, up ? fmtMs(entry.latency_ms) : el("span", { class: "dash", text: "—" })),
    el("td", { class: "n", "data-label": "This visit" },
      avg === null ? el("span", { class: "dash", text: "—" }) : `${fmtMs(avg)} average`,
      el("span", { class: "cell-sub", text: `${checks} ${checks === 1 ? "check" : "checks"}` })));
}

function renderServices(s) {
  const body = $("services-body");
  if (!s.loaded) { body.replaceChildren(skeletonRows(3, 4)); return; }
  if (!s.health) { body.replaceChildren(retryBlock(`Couldn't load service health from ${API}.`)); return; }
  const banner = s.healthError ? retryBlock(`Couldn't refresh. Showing the check from ${fmtTime(s.lastGoodAt)}.`) : null;
  const rows = SERVICES.map((def) => serviceRow(def, s.health.services.find((x) => x.name === def.name), s.history[def.name].length));
  body.replaceChildren(...[banner, el("div", { class: "table-wrap" }, el("table", { class: "services stack-table" },
    el("thead", {}, el("tr", {}, [["Service", ""], ["Status", ""], ["Latency", "n"], ["This visit", "n"]].map(([h, c]) =>
      el("th", { class: c, text: h })))),
    el("tbody", {}, rows)))].filter(Boolean));
}

// ------------------------------------------------------------------ (c) the key pool: four limits as real numbers against their caps

function meterCell(label, left, cap, off) {
  const ratio = cap > 0 ? Math.max(0, Math.min(1, left / cap)) : 0;
  const bar = el("span", { class: "fill" });
  bar.style.width = `${(ratio * 100).toFixed(1)}%`; // a number computed here, never a server string
  return el("td", { class: `meter${!off && ratio < LOW_HEADROOM ? " low" : ""}`, "data-label": label },
    el("span", { class: "meter-nums" }, el("b", { text: count(left) }), ` of ${count(cap)} left`),
    el("span", { class: "bar", role: "img", "aria-label": `${count(left)} of ${count(cap)} ${label.toLowerCase()} left` }, bar));
}

function keyRow(k, limits) {
  const off = k.state !== "ok";
  const pill = { ok: ["pos", "OK"], cooling: ["warn", "Cooling"], disabled: ["neg", "Disabled"] }[k.state] || ["", String(k.state)];
  const note = k.state === "cooling" && k.cooling_until ? `Back at ${fmtTime(k.cooling_until)}`
    : k.state === "disabled" ? "Rejected by Groq" : null;
  return el("tr", { class: off ? "row-off" : "" },
    el("td", { "data-label": "Key" }, el("span", { class: "name", text: String(k.key) })),
    el("td", { "data-label": "State" }, el("span", { class: `pill ${pill[0]}`.trim(), text: pill[1] }), note && el("span", { class: "cell-sub", text: note })),
    LIMITS.map(([id, label]) => meterCell(label, k[`${id}_left`], limits[id], off)));
}

function renderKeys(s) {
  const body = $("keys-body");
  const meta = $("keys-meta");
  if (!s.llm) {
    meta.textContent = "Each call goes to the key with the most headroom on all four limits.";
    body.replaceChildren(!s.loaded ? skeletonRows(2, 6) : retryBlock(`Couldn't load the key pool from ${API}.`));
    return;
  }
  const llm = s.llm;
  meta.textContent = llm.enabled
    ? `${llm.model}. Each call goes to the key with the most headroom on all four limits.`
    : "Not in use.";
  if (!llm.enabled) {
    body.replaceChildren(el("div", { class: "empty" }, el("p", { text: "No Groq keys are set." }),
      el("p", { class: "small", text: "Intents come from the built-in rules, which need no network and no key." })));
    return;
  }
  const banner = s.llmError ? retryBlock(`Couldn't refresh. Showing the pool as of the last good check.`) : null;
  body.replaceChildren(...[banner, el("div", { class: "table-wrap" }, el("table", { class: "keys stack-table" },
    el("thead", {}, el("tr", {}, [["Key", ""], ["State", ""], ...LIMITS.map(([, l]) => [l, ""])].map(([h]) => el("th", { text: h })))),
    el("tbody", {}, llm.keys.map((k) => keyRow(k, llm.limits)))))].filter(Boolean));
}

// ------------------------------------------------------------------ start-up

function init() {
  mountReadout();
  const button = $("refresh");
  button.addEventListener("click", async () => {
    button.disabled = true;
    await refresh();
    setTimeout(() => { button.disabled = false; }, 400);
  });
  subscribe((s) => {
    renderTitle(s);
    renderDiagram(s);
    renderServices(s);
    renderKeys(s);
  });
}

init();
