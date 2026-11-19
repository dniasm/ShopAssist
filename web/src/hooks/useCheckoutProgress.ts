import { useCallback, useEffect, useRef, useState } from "react";
import { getOrder } from "../api/endpoints";
import type { Order } from "../api/types";
import { initialSaga, orderStatusToEvent, reduceSaga, type SagaState } from "../features/checkout/saga";
import { checkoutOrderId, toCheckoutEvent, toOrderStatus, type CheckoutEvent } from "../features/session/events";
import { useSessionSocket, useSocketChannel } from "../features/session/SessionSocket";

/** While an order is in doubt (we lost contact, or nothing has arrived for a while) its record is read this often. */
export const STATUS_POLL_MS = 10_000;
const SILENCE_BEFORE_POLL_MS = 8_000;

export interface CheckoutProgress {
  saga: SagaState;
  socketStatus: ReturnType<typeof useSessionSocket>["status"];
  /** The order record, once it has been read (used for product names in a failure). */
  order: Order | null;
  /** Reads the order now ("Check order status"). Resolves when done; the saga updates if the order is final. */
  checkStatus: () => Promise<void>;
  checking: boolean;
  /** What the last check found: still PENDING, or it could not be read. null before any check. */
  lastCheck: "pending" | "error" | null;
}

/** The saga for one order, driven by the socket's `checkout` events. `replay` hands over everything the socket received
 *  before this page subscribed, so a refresh of /checkout/:orderId rebuilds the same picture from the server's replay.
 *
 *  When the saga stream is lost (`interrupted`) the outcome is unknown. The order's record is then read every 10 s, on the
 *  shopper's request, and whenever the `order` channel reports a status change for it; as soon as the order is CONFIRMED or
 *  FAILED the matching outcome is shown, and polling stops. The same read is the safety net if no event has arrived for a
 *  while (for instance the socket's replay buffer no longer holds this order). Events always win while they flow. */
export function useCheckoutProgress(orderId: string): CheckoutProgress {
  const { status } = useSessionSocket();
  const [saga, setSaga] = useState(() => initialSaga(orderId));
  const [order, setOrder] = useState<Order | null>(null);
  const [checking, setChecking] = useState(false);
  const [lastCheck, setLastCheck] = useState<CheckoutProgress["lastCheck"]>(null);
  const lastEventAt = useRef(Date.now());
  const finalRef = useRef(false);
  finalRef.current = saga.outcome !== null;

  const apply = useCallback((event: CheckoutEvent) => {
    if (checkoutOrderId(event) !== orderId) return;
    lastEventAt.current = Date.now();
    setSaga((s) => reduceSaga(s, event));
  }, [orderId]);

  useSocketChannel("checkout", (env) => {
    const event = toCheckoutEvent(env);
    if (event) apply(event);
  }, { replay: true });

  /** Reads the order record; a final status becomes the saga's outcome, PENDING changes nothing. */
  const inflight = useRef<Promise<void> | null>(null);
  const settle = useCallback((signal?: AbortSignal): Promise<void> => {
    // One read at a time: a burst of replayed order events must not fire a burst of requests.
    if (inflight.current) return inflight.current;
    const run = (async () => {
      try {
        const record = await getOrder(orderId, signal);
        if (signal?.aborted) return;
        setOrder(record);
        const event = orderStatusToEvent(record);
        if (event) { apply(event); setLastCheck(null); } else setLastCheck("pending");
      } catch {
        if (!signal?.aborted) setLastCheck("error");
      } finally {
        inflight.current = null;
      }
    })();
    inflight.current = run;
    return run;
  }, [orderId, apply]);

  const checkStatus = useCallback(async () => {
    setChecking(true);
    try { await settle(); } finally { setChecking(false); }
  }, [settle]);

  // The final status of a recovered checkout arrives on the `order` channel: read the record right away.
  useSocketChannel("order", (env) => {
    const change = toOrderStatus(env);
    if (change && change.order_id === orderId && !finalRef.current) void settle();
  }, { replay: true });

  const final = saga.outcome !== null;
  const inDoubt = saga.interrupted !== null;
  useEffect(() => {
    if (final) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      const silent = Date.now() - lastEventAt.current >= SILENCE_BEFORE_POLL_MS;
      if (inDoubt || silent) void settle(controller.signal);
    }, STATUS_POLL_MS);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [final, inDoubt, settle]);

  // Names for a failure's shortages come from the order's own lines (the cart may already have changed).
  const failed = saga.outcome?.kind === "failed";
  useEffect(() => {
    if (!failed || order) return;
    const controller = new AbortController();
    getOrder(orderId, controller.signal).then(setOrder, () => undefined);
    return () => controller.abort();
  }, [failed, order, orderId]);

  return { saga, socketStatus: status, order, checkStatus, checking, lastCheck };
}
