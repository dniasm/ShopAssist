import type { Decided } from "./transcript";

/** What survives a refresh of this tab: the shopper's own words per turn, and what they decided on each proposal. The assistant's
 *  side comes back from the socket's replay, so it is not stored. sessionStorage: it is gone when the tab is. */
export interface StoredChat { texts: Record<string, string>; decided: Record<string, Decided> }

const KEY = "shopassist.chat.v1";
const MAX_TEXTS = 60;

const isRec = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

export function parseStored(raw: string | null): StoredChat {
  const empty: StoredChat = { texts: {}, decided: {} };
  if (!raw) return empty;
  try {
    const v: unknown = JSON.parse(raw);
    if (!isRec(v)) return empty;
    const texts: Record<string, string> = {};
    if (isRec(v.texts)) for (const [k, t] of Object.entries(v.texts)) if (typeof t === "string") texts[k] = t;
    const decided: Record<string, Decided> = {};
    if (isRec(v.decided)) {
      for (const [k, d] of Object.entries(v.decided)) {
        if (!isRec(d) || (d.state !== "confirmed" && d.state !== "dismissed")) continue;
        decided[k] = { state: d.state, afterSeq: typeof d.afterSeq === "number" ? d.afterSeq : null, orderId: typeof d.orderId === "string" ? d.orderId : null };
      }
    }
    return { texts, decided };
  } catch {
    return empty;
  }
}

/** Keeps only the newest entries so a long session does not grow the stored record without bound. */
export function trimStored(stored: StoredChat): StoredChat {
  const entries = Object.entries(stored.texts);
  return { texts: Object.fromEntries(entries.slice(-MAX_TEXTS)), decided: stored.decided };
}

/** Stored per user: switching customers must never show someone else's questions. Every access is wrapped: the chat works without it. */
const keyFor = (userId: string | null) => `${KEY}:${userId ?? "anon"}`;

export function loadStored(userId: string | null): StoredChat {
  try { return parseStored(window.sessionStorage.getItem(keyFor(userId))); } catch { return { texts: {}, decided: {} }; }
}
export function saveStored(userId: string | null, stored: StoredChat): void {
  try { window.sessionStorage.setItem(keyFor(userId), JSON.stringify(trimStored(stored))); } catch { /* storage unavailable: a refresh just forgets the questions */ }
}
export function clearStored(userId: string | null): void {
  try { window.sessionStorage.removeItem(keyFor(userId)); } catch { /* ignore */ }
}
