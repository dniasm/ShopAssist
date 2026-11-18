/* The shared header's live readout: one line saying whether the system can answer right now.
 * The header itself (mark, name, the two links) is plain HTML in each page so it works before any script runs. */
import { subscribe } from "./status.js";

const NAMES = { product: "Product", inventory: "Inventory", order: "Order" };

/** Pure: state -> {state, text}. state is one of checking | up | warn | down. */
export function readoutFor(s) {
  if (!s.loaded) return { state: "checking", text: "Checking…" };
  if (s.healthError && !s.health) return { state: "down", text: "Backend offline" };
  if (s.healthError) return { state: "down", text: "Backend not answering" };
  const down = s.health.services.filter((x) => x.status === "down");
  if (down.length) {
    return { state: "down", text: down.length === 1 ? `${NAMES[down[0].name]} service down` : `${down.length} services down` };
  }
  const up = `${s.health.services.length} services up`;
  const llm = s.llm;
  if (!llm || !llm.enabled) return { state: "up", text: `${up} · Rules only` };
  const ok = llm.keys.filter((k) => k.state === "ok").length;
  return { state: ok === llm.keys.length ? "up" : ok === 0 ? "down" : "warn", text: `${up} · Groq ${ok}/${llm.keys.length} keys` };
}

export function mountReadout() {
  const node = document.getElementById("readout");
  const text = document.getElementById("readout-text");
  if (!node || !text) return;
  subscribe((s) => {
    const r = readoutFor(s);
    node.dataset.state = r.state;
    text.textContent = r.text;
    node.title = r.text;
  });
}
