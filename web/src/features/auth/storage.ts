import type { Session, User } from "../../api/types";

const KEY = "shopassist.session.v1";

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isUser(v: unknown): v is User {
  return isRecord(v) && typeof v.user_id === "string" && typeof v.name === "string"
    && typeof v.email === "string" && typeof v.city === "string";
}

/** Validates what came out of localStorage. Anything unexpected means "signed out". */
export function parseStoredSession(raw: string | null): Session | null {
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (isRecord(value) && typeof value.token === "string" && value.token && isUser(value.user)) {
      return { token: value.token, user: value.user };
    }
  } catch { /* corrupt JSON */ }
  return null;
}

/** localStorage can throw (private windows, blocked site data). Every access is wrapped; the app works without it. */
export function loadSession(): Session | null {
  try { return parseStoredSession(window.localStorage.getItem(KEY)); } catch { return null; }
}

export function saveSession(session: Session | null): void {
  try {
    if (session) window.localStorage.setItem(KEY, JSON.stringify(session));
    else window.localStorage.removeItem(KEY);
  } catch { /* storage unavailable: the session lives for this tab only */ }
}
