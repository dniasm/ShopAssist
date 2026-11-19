/* Random ids made in the browser: the guest id of a signed-out visitor and the id of each chat conversation. */

/** A random id (a UUID when the browser has one; crypto.randomUUID needs a secure context, so there is a fallback). */
export function newId(): string {
  const c = globalThis.crypto;
  if (typeof c?.randomUUID === "function") return c.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof c?.getRandomValues === "function") c.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

const GUEST_KEY = "shopassist.guest.v1";
/** What the backend accepts as a guest id. */
const GUEST_ID = /^[A-Za-z0-9_-]{8,64}$/;

type KeyValueStore = Pick<Storage, "getItem" | "setItem">;

/** The id kept in `store`, or a new one saved there. Every storage access is guarded: it may be blocked, in which case the id lives
 *  for as long as `fallback` keeps it. */
export function guestIdFrom(store: KeyValueStore | null, make: () => string = newId): string {
  try {
    const saved = store?.getItem(GUEST_KEY);
    if (saved && GUEST_ID.test(saved)) return saved;
  } catch { /* blocked storage: make one */ }
  const id = make();
  try { store?.setItem(GUEST_KEY, id); } catch { /* not kept: a new visit gets a new id */ }
  return id;
}

let inMemory: string | null = null;

/** One guest id per browser (localStorage), used to chat while signed out. */
export function guestIdForBrowser(): string {
  if (inMemory) return inMemory;
  let store: KeyValueStore | null = null;
  try { store = window.localStorage; } catch { store = null; }
  inMemory = guestIdFrom(store);
  return inMemory;
}
