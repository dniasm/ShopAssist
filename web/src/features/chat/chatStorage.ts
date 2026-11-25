import { newId } from "../session/ids";
import type { Decided } from "./transcript";

/** What survives a refresh of this tab: the shopper's own words per turn, and what they decided on each proposal. The assistant's
 *  side comes back from the socket's replay, so it is not stored. sessionStorage: it is gone when the tab is.
 *  Stored per conversation id: a new conversation starts empty and an old one can never leak into it. */
export interface StoredChat { texts: Record<string, string>; decided: Record<string, Decided> }

const KEY = "shopassist.chat.v2";
const CURRENT_KEY = "shopassist.chat.current";
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

/** Which conversation this tab is in: the one remembered for `identity` (a user id, or "anon"), else a new one. The remembered one
 *  belongs to exactly one identity, so signing in or out never carries a conversation across. */
export function pickConversation(raw: string | null, identity: string, make: () => string = newId): string {
  try {
    const v: unknown = raw ? JSON.parse(raw) : null;
    if (isRec(v) && v.identity === identity && typeof v.id === "string" && v.id) return v.id;
  } catch { /* unreadable: start a new one */ }
  return make();
}

/** What of the transcript state goes to sessionStorage: a guest's own words and decisions; nothing for a signed-in user, whose
 *  transcript lives on the server (only the current conversation id is remembered, see rememberConversation). */
export function persistable(identity: string, stored: StoredChat): StoredChat | null {
  return identity === "anon" ? stored : null;
}

// Every access below is wrapped: the chat works without storage, a refresh just forgets the questions.
export function currentConversation(identity: string): string {
  let raw: string | null = null;
  try { raw = window.sessionStorage.getItem(CURRENT_KEY); } catch { raw = null; }
  const id = pickConversation(raw, identity);
  rememberConversation(identity, id);
  return id;
}
export function rememberConversation(identity: string, id: string): void {
  try { window.sessionStorage.setItem(CURRENT_KEY, JSON.stringify({ identity, id })); } catch { /* ignore */ }
}

export function loadStored(conversationId: string): StoredChat {
  try { return parseStored(window.sessionStorage.getItem(`${KEY}:${conversationId}`)); } catch { return { texts: {}, decided: {} }; }
}
export function saveStored(conversationId: string, stored: StoredChat): void {
  try { window.sessionStorage.setItem(`${KEY}:${conversationId}`, JSON.stringify(trimStored(stored))); } catch { /* storage unavailable */ }
}
export function clearStored(conversationId: string): void {
  try { window.sessionStorage.removeItem(`${KEY}:${conversationId}`); } catch { /* ignore */ }
}
