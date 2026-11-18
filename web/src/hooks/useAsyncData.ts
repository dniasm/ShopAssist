import { useCallback, useEffect, useRef, useState } from "react";
import { isAbort } from "../api/client";

export type AsyncState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "ready"; data: T; error: null }
  | { status: "error"; data: null; error: unknown };

/** Runs `load` whenever `key` changes (or `reload()` is called), aborting the request it replaces. */
export function useAsyncData<T>(load: (signal: AbortSignal) => Promise<T>, key: string): AsyncState<T> & { reload: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ status: "loading", data: null, error: null });
  const [attempt, setAttempt] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading", data: null, error: null });
    loadRef.current(controller.signal).then(
      (data) => { if (!controller.signal.aborted) setState({ status: "ready", data, error: null }); },
      (error: unknown) => { if (!controller.signal.aborted && !isAbort(error)) setState({ status: "error", data: null, error }); },
    );
    return () => controller.abort();
  }, [key, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, reload };
}
