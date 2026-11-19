import { useCallback, useEffect, useRef, useState } from "react";
import { isAbort } from "../api/client";
import { listOrders } from "../api/endpoints";
import type { OrderStatus, OrderStatusData, OrderSummary } from "../api/types";
import { advanceStatus } from "../features/orders/timeline";
import { toOrderStatus } from "../features/session/events";
import { useSocketChannel } from "../features/session/SessionSocket";

export const ORDERS_PAGE_SIZE = 10;

export interface OrderRowData extends OrderSummary {
  /** The recorded status moved forward by `order` events seen since the list was fetched. */
  liveStatus: OrderStatus;
}

export interface OrdersState {
  status: "loading" | "ready" | "error";
  orders: OrderRowData[];
  hasMore: boolean;
  loadingMore: boolean;
  error: unknown;
  moreError: unknown;
  retry: () => void;
  loadMore: () => void;
}

/** The signed-in shopper's orders, newest first, with "Load more". A row's status follows the socket's `order` events without a
 *  refetch (forward only, terminal is final). An order that appears live which the list has not seen (placed in another tab)
 *  makes the first page load again and merge in at the top. */
export function useOrders(): OrdersState {
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [next, setNext] = useState("");
  const [status, setStatus] = useState<OrdersState["status"]>("loading");
  const [error, setError] = useState<unknown>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const [events, setEvents] = useState<Record<string, OrderStatusData[]>>({});
  const knownIds = useRef(new Set<string>());
  const readyRef = useRef(false);
  const refreshing = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading");
    setError(null);
    readyRef.current = false;
    listOrders({ page_size: ORDERS_PAGE_SIZE }, controller.signal).then(
      (res) => {
        if (controller.signal.aborted) return;
        knownIds.current = new Set(res.orders.map((o) => o.order_id));
        setOrders(res.orders);
        setNext(res.next_page_token);
        setEvents({});            // fresh records already include everything up to now; later events move rows on
        setStatus("ready");
        readyRef.current = true;
      },
      (err: unknown) => {
        if (controller.signal.aborted || isAbort(err)) return;
        setError(err);
        setStatus("error");
      },
    );
    return () => controller.abort();
  }, [attempt]);

  // Row status: every event for an order on the socket, kept by event_id (the replay can repeat them).
  useSocketChannel("order", (message) => {
    const change = toOrderStatus(message);
    if (!change) return;
    setEvents((prev) => {
      const existing = prev[change.order_id] ?? [];
      if (existing.some((e) => e.event_id === change.event_id)) return prev;
      return { ...prev, [change.order_id]: [...existing, change] };
    });
  }, { replay: true });

  // A brand-new order (live, not replayed) that the list does not hold: load the first page again and merge it in.
  useSocketChannel("order", (message) => {
    const change = toOrderStatus(message);
    if (!change || !readyRef.current || knownIds.current.has(change.order_id) || refreshing.current) return;
    refreshing.current = true;
    listOrders({ page_size: ORDERS_PAGE_SIZE }).then(
      (res) => {
        setOrders((prev) => {
          const fresh = new Map(res.orders.map((o) => [o.order_id, o]));
          const merged = [...res.orders, ...prev.filter((o) => !fresh.has(o.order_id))];
          knownIds.current = new Set(merged.map((o) => o.order_id));
          return merged;
        });
        setEvents({});
      },
      () => undefined,
    ).finally(() => { refreshing.current = false; });
  });

  const loadMore = useCallback(() => {
    if (!next || loadingMore) return;
    setLoadingMore(true);
    setMoreError(null);
    listOrders({ page_size: ORDERS_PAGE_SIZE, page_token: next }).then(
      (res) => {
        setOrders((prev) => {
          const seen = new Set(prev.map((o) => o.order_id));
          const merged = [...prev, ...res.orders.filter((o) => !seen.has(o.order_id))];
          knownIds.current = new Set(merged.map((o) => o.order_id));
          return merged;
        });
        setNext(res.next_page_token);
        setLoadingMore(false);
      },
      (err: unknown) => { setMoreError(err); setLoadingMore(false); },
    );
  }, [next, loadingMore]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const rows: OrderRowData[] = orders.map((o) => ({
    ...o,
    liveStatus: [...(events[o.order_id] ?? [])].sort((a, b) => a.event_id - b.event_id).reduce(advanceStatus, o.status),
  }));

  return { status, orders: rows, hasMore: next !== "", loadingMore, error, moreError, retry, loadMore };
}
