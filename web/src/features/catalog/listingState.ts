/* What the catalog remembers about a listing so that Back from a product page lands where the shopper was:
 * how many "Load more" pages were loaded, and how far they had scrolled. It lives in the history entry's own state
 * (window.history.state.listing), next to the router's fields, so it belongs to exactly one entry and never leaks
 * into another visit of the same URL. These are the pure rules; hooks/useRestoredListing.ts applies them. */

export interface ListingSnapshot {
  /** The serialised catalog query this snapshot answers (features/catalog/query.ts). A different query never restores it. */
  key: string;
  pages: number;
  scrollY: number;
}

/** A safety cap: restoring is sequential requests, and nobody scrolls through 20 pages by accident. */
export const MAX_RESTORE_PAGES = 20;
export const NO_SNAPSHOT = { pages: 1, scrollY: 0 } as const;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** The snapshot stored in a history state, or null if there is none or it is malformed. */
export function readSnapshot(state: unknown): ListingSnapshot | null {
  if (!isRecord(state) || !isRecord(state.listing)) return null;
  const { key, pages, scrollY } = state.listing;
  if (typeof key !== "string" || typeof pages !== "number" || typeof scrollY !== "number") return null;
  if (!Number.isFinite(pages) || !Number.isFinite(scrollY)) return null;
  return { key, pages, scrollY };
}

/** What to restore for `key`: the saved page count and scroll if the entry was saved for this very query, else the defaults. */
export function restorableSnapshot(state: unknown, key: string): { pages: number; scrollY: number } {
  const snap = readSnapshot(state);
  if (!snap || snap.key !== key) return { ...NO_SNAPSHOT };
  return {
    pages: Math.max(1, Math.min(MAX_RESTORE_PAGES, Math.trunc(snap.pages))),
    scrollY: Math.max(0, Math.round(snap.scrollY)),
  };
}

/** A copy of `state` with `listing` set, keeping every other field (the router's usr / key / idx) untouched. */
export function withSnapshot(state: unknown, snapshot: ListingSnapshot): Record<string, unknown> {
  return { ...(isRecord(state) ? state : {}), listing: { key: snapshot.key, pages: snapshot.pages, scrollY: Math.max(0, Math.round(snapshot.scrollY)) } };
}

/** How many pages of `pageSize` the loaded rows span (at least 1). */
export function pagesFor(loadedCount: number, pageSize: number): number {
  return Math.max(1, Math.ceil(loadedCount / Math.max(1, pageSize)));
}
