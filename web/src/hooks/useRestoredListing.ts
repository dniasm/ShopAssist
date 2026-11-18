import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { pagesFor, restorableSnapshot, withSnapshot } from "../features/catalog/listingState";

/** What to restore for this query, read from the current history entry. Changes only when the query does, so it is the
 *  value to hand to useProducts as `restorePages` (back from a product page, or Back between filter states). */
export function useListingSnapshot(key: string): { pages: number; scrollY: number } {
  // history.state is deliberately read once per query, not on every render
  return useMemo(() => restorableSnapshot(window.history.state, key), [key]);
}

interface Options {
  key: string;
  snapshot: { pages: number; scrollY: number };
  loadedCount: number;
  pageSize: number;
  /** The rows on screen answer `key` (not a stale previous query, not loading). */
  ready: boolean;
}

const SAVE_EVERY_MS = 150;

/** Keeps the listing's position in its own history entry, so Back from a product page returns the shopper to where they
 *  were: how many "Load more" pages were loaded (written when it changes) and how far they had scrolled (written while
 *  scrolling and, synchronously, the instant a link is activated, before the router leaves the page). On return it
 *  scrolls to the saved position once the restored rows have rendered. */
export function useRestoredListing({ key, snapshot, loadedCount, pageSize, ready }: Options): void {
  const latest = useRef({ key, pages: 1, scrollY: 0, ready: false, entry: "" });
  const restored = useRef<string | null>(null);

  // Which history entry this listing belongs to: everything is written to that entry only.
  useEffect(() => {
    latest.current.key = key;
    latest.current.entry = historyEntryKey();
    latest.current.scrollY = window.scrollY;
  }, [key]);

  useEffect(() => {
    latest.current.ready = ready;
    if (ready) latest.current.pages = pagesFor(loadedCount, pageSize);
    if (ready) save(latest.current);
  }, [ready, loadedCount, pageSize]);

  useLayoutEffect(() => {
    if (!ready || restored.current === key) return;
    restored.current = key;
    if (snapshot.scrollY > 0 && loadedCount > 0) window.scrollTo(0, snapshot.scrollY);
  }, [ready, key, snapshot.scrollY, loadedCount]);

  useEffect(() => {
    let timer: number | undefined;
    const onScroll = () => {
      latest.current.scrollY = window.scrollY;
      if (timer === undefined) timer = window.setTimeout(() => { timer = undefined; save(latest.current); }, SAVE_EVERY_MS);
    };
    // Capture phase: runs before the router's own click handler pushes the next entry.
    const flush = () => { latest.current.scrollY = window.scrollY; save(latest.current); };
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("click", flush, true);
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("click", flush, true);
      window.removeEventListener("pagehide", flush);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);
}

function historyEntryKey(): string {
  const state: unknown = window.history.state;
  return typeof state === "object" && state !== null && "key" in state && typeof state.key === "string" ? state.key : "";
}

function save(s: { key: string; pages: number; scrollY: number; ready: boolean; entry: string }): void {
  // Only while the rows on screen are this query's, and only into the entry the listing was opened in.
  if (!s.ready || historyEntryKey() !== s.entry) return;
  try {
    window.history.replaceState(withSnapshot(window.history.state, { key: s.key, pages: s.pages, scrollY: s.scrollY }), "");
  } catch { /* some browsers throttle or forbid replaceState: the listing just opens at the top next time */ }
}
