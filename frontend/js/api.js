/* Where the orchestrator lives. Served by it: same origin. Opened any other way: ?api=... or the default port. */

function resolveApiBase() {
  const fromQuery = new URLSearchParams(location.search).get("api");
  if (fromQuery) return fromQuery.replace(/\/+$/, "");
  return location.protocol === "http:" || location.protocol === "https:" ? location.origin : "http://localhost:8000";
}

export const API = resolveApiBase();
export const WS_BASE = API.replace(/^http/, "ws");

/** GET a JSON path with a hard timeout. Throws on a network error, a timeout or a non-2xx status. */
export async function getJson(path, timeoutMs = 4000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(`${API}${path}`, { signal: ctrl.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } finally {
    clearTimeout(timer);
  }
}
