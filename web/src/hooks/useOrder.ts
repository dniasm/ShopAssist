import { useCallback, useEffect, useMemo, useState } from "react";
import { isAbort } from "../api/client";
import { getOrder } from "../api/endpoints";
import type { Order, OrderStatus } from "../api/types";
import { effectiveStatus, timeline, type LiveEvent, type Timeline } from "../features/orders/timeline";
import { toOrderStatus } from "../features/session/events";
import { useSocketChannel } from "../features/session/SessionSocket";

export type OrderState =
  | { status: "loading"; order: null; error: null }
  | { status: "ready"; order: Order; error: null }
  | { status: "error"; order: null; error: unknown };

export interface OrderView {
  state: OrderState;
  /** The order's status now: the record moved forward by live events. */
  currentStatus: OrderStatus | null;
  timeline: Timeline | null;
  reload: () => void;
  /** Replaces the record with one the API just returned (the cancel response). */
  replace: (order: Order) => void;
}

/** One order and its delivery timeline. The timeline is a pure function of the order's `history` and the `order` events seen on
 *  the socket (the replay brings everything the server still holds, so a refresh mid-delivery shows the same steps). */
export function useOrder(orderId: string): OrderView {
  const [state, setState] = useState<OrderState>({ status: "loading", order: null, error: null });
  const [live, setLive] = useState<LiveEvent[]>([]);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState((s) => (s.status === "ready" ? s : { status: "loading", order: null, error: null }));   // a refetch keeps what is on screen
    getOrder(orderId, controller.signal).then(
      (order) => { if (!controller.signal.aborted) setState({ status: "ready", order, error: null }); },
      (error: unknown) => { if (!controller.signal.aborted && !isAbort(error)) setState({ status: "error", order: null, error }); },
    );
    return () => controller.abort();
  }, [orderId, attempt]);

  useSocketChannel("order", (message) => {
    const change = toOrderStatus(message);
    if (!change || change.order_id !== orderId) return;
    setLive((prev) => (prev.some((e) => e.event_id === change.event_id) ? prev : [...prev, { ...change, at: message.ts }]));
  }, { replay: true });

  // Our view may be stale after a long disconnect: read the order again.
  useSocketChannel("system", (message) => { if (message.type === "resync") setAttempt((n) => n + 1); });

  const tl = useMemo(() => (state.order ? timeline(state.order.history, live) : null), [state.order, live]);
  const currentStatus = state.order && tl ? effectiveStatus(state.order.status, tl) : null;

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  const replace = useCallback((order: Order) => setState({ status: "ready", order, error: null }), []);

  return { state, currentStatus, timeline: tl, reload, replace };
}
