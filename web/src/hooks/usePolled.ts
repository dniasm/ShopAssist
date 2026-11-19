import { useCallback, useEffect, useRef, useState } from "react";
import { isAbort } from "../api/client";

export interface Polled<T> {
  /** The last good answer. It stays on screen when a later refresh fails. */
  data: T | null;
  /** The most recent refresh failed (data, if any, is older than it should be). */
  error: unknown;
  /** When `data` was received (ms since epoch), or null before the first answer. */
  receivedAt: number | null;
  refreshing: boolean;
  refresh: () => void;
}

/** Calls `fetcher` now and then every `everyMs`, one request at a time, only while the tab is visible (and straight away when it
 *  becomes visible again). A failed refresh keeps the last good data and sets `error`. */
export function usePolled<T>(fetcher: (signal: AbortSignal) => Promise<T>, everyMs: number): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [receivedAt, setReceivedAt] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const inFlight = useRef(false);
  const controller = useRef<AbortController | null>(null);

  const run = useCallback(() => {
    if (inFlight.current) return;
    inFlight.current = true;
    setRefreshing(true);
    const c = new AbortController();
    controller.current = c;
    fetcherRef.current(c.signal).then(
      (value) => { if (c.signal.aborted) return; setData(value); setError(null); setReceivedAt(Date.now()); },
      (err: unknown) => { if (c.signal.aborted || isAbort(err)) return; setError(err); },
    ).finally(() => { inFlight.current = false; if (!c.signal.aborted) setRefreshing(false); });
  }, []);

  useEffect(() => {
    run();
    const timer = window.setInterval(() => { if (!document.hidden) run(); }, everyMs);
    const onVisible = () => { if (!document.hidden) run(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      controller.current?.abort();
      inFlight.current = false;
    };
  }, [run, everyMs]);

  return { data, error, receivedAt, refreshing, refresh: run };
}
