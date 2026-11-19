import { useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { cancelOrder } from "../api/endpoints";
import { ApiError } from "../api/errors";
import { formatMoney } from "../api/money";
import type { Order, OrderStatus } from "../api/types";
import { EmptyState, ErrorBlock } from "../components/StateBlocks";
import { useAuth } from "../features/auth/AuthContext";
import { colorLabel, sizeLabel } from "../features/catalog/labels";
import { readableReason } from "../features/checkout/saga";
import { newIdempotencyKey } from "../features/checkout/idempotency";
import { CancelDialog } from "../features/orders/CancelDialog";
import { formatDateTime } from "../features/orders/format";
import { ProductTile } from "../features/tile/ProductTile";
import { OrderTimeline } from "../features/orders/OrderTimeline";
import { canCancel, describeCancelError } from "../features/orders/orderStatus";
import { StatusPill } from "../features/orders/StatusPill";
import type { Timeline } from "../features/orders/timeline";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useOrder } from "../hooks/useOrder";
import styles from "./OrderDetailPage.module.css";

/** /orders/:orderId. The record plus a delivery timeline that moves on live `order` events and is rebuilt after a refresh. */
export function OrderDetailPage() {
  const { orderId = "" } = useParams();
  const { user, openSignIn } = useAuth();
  useDocumentTitle(`Order ${orderId}`);

  if (!user) {
    return (
      <>
        <div className="title-row"><h1>Order {orderId}</h1></div>
        <EmptyState title="Sign in to see this order" action={<button type="button" className="btn primary" onClick={() => openSignIn()}>Sign in</button>}>
          Orders belong to the customer who placed them.
        </EmptyState>
      </>
    );
  }
  // key: another order (or another user) starts clean, with a fresh cancel idempotency key
  return <OrderDetail key={`${user.user_id}:${orderId}`} orderId={orderId} />;
}

function OrderDetail({ orderId }: { orderId: string }) {
  const view = useOrder(orderId);
  const { state, currentStatus, timeline, reload, replace } = view;

  // ONE key per opening of this page, reused for every cancel attempt: the same key always yields the same result.
  const [cancelKey] = useState(newIdempotencyKey);
  const inFlight = useRef(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  if (state.status === "loading") return <DetailSkeleton orderId={orderId} />;
  if (state.status === "error") {
    if (state.error instanceof ApiError && state.error.isNotFound) {
      return (
        <>
          <div className="title-row"><h1>Order {orderId}</h1></div>
          <EmptyState title="We can't find this order" action={<Link to="/orders" className="btn primary">My orders</Link>}>
            It may belong to another customer, or the link is mistyped.
          </EmptyState>
        </>
      );
    }
    return <ErrorBlock error={state.error} what="this order" onRetry={reload} />;
  }

  const order = state.order;
  const status: OrderStatus = currentStatus ?? order.status;
  const cancellable = canCancel(status);

  async function confirmCancel() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setProblem(null);
    try {
      replace(await cancelOrder(order.order_id, cancelKey));
      setDialogOpen(false);
    } catch (err) {
      const d = describeCancelError(err);
      setProblem(d.message);
      if (d.refetch) reload();       // shipped in the meantime: show where it really is
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <>
      <div className="title-row">
        <div>
          <h1>Order <span className={styles.id}>{order.order_id}</span></h1>
          <p className="sub">Placed {formatDateTime(order.created_at)}</p>
        </div>
        <div className={styles.actions}>
          <StatusPill status={status} />
          {cancellable && <button type="button" className="btn" onClick={() => { setProblem(null); setDialogOpen(true); }}>Cancel order</button>}
        </div>
      </div>

      <Banner order={order} status={status} timeline={timeline} />

      <div className={styles.layout}>
        <section className={styles.panel} aria-labelledby="delivery-title">
          <h2 id="delivery-title" className="section-title">Delivery</h2>
          {timeline && <OrderTimeline timeline={timeline} stopped={status === "CANCELLED" || status === "FAILED"} />}
        </section>

        <section className={styles.panel} aria-labelledby="items-title">
          <h2 id="items-title" className="section-title">Items</h2>
          <ul className={styles.lines}>
            {order.lines.map((l) => (
              <li key={l.sku_id} className={styles.line}>
                <span className={styles.tile} aria-hidden="true"><ProductTile productId={l.product_id} category={l.category} color={l.color} /></span>
                <span className={styles.what}>
                  <Link to={`/p/${encodeURIComponent(l.product_id)}`} className={styles.name}>{l.name}</Link>
                  <span className="muted small">{sizeLabel(l.size)} · {colorLabel(l.color)} · {l.quantity} × {formatMoney(l.unit_price_cents)}</span>
                </span>
                <span className={styles.amount}>{formatMoney(l.line_total_cents)}</span>
              </li>
            ))}
          </ul>
          <dl className={styles.sums}>
            <div><dt>Subtotal</dt><dd>{formatMoney(order.subtotal_cents)}</dd></div>
            <div><dt>Shipping</dt><dd>{order.shipping_cents === 0 ? "Free" : formatMoney(order.shipping_cents)}</dd></div>
            <div className={styles.total}><dt>Total</dt><dd>{formatMoney(order.total_cents)}</dd></div>
            <div className={styles.payment}><dt>Payment</dt><dd>{order.payment_id ? <code>{order.payment_id}</code> : "—"}</dd></div>
          </dl>
        </section>
      </div>

      <CancelDialog
        open={dialogOpen}
        order={order}
        cancellable={cancellable}
        busy={busy}
        problem={problem}
        onKeep={() => { if (!busy) setDialogOpen(false); }}
        onConfirm={() => void confirmCancel()}
      />
    </>
  );
}

/** Terminal and in-doubt states are banners, not steps. */
function Banner({ order, status, timeline }: { order: Order; status: OrderStatus; timeline: Timeline | null }) {
  if (status === "CANCELLED") {
    return (
      <p className={`${styles.banner} ${styles.warn}`} role="status">
        <strong>This order was cancelled.</strong> {formatMoney(order.total_cents)} was refunded to your card and the items went back to stock.
      </p>
    );
  }
  if (status === "FAILED") {
    const reason = readableReason(order.failure_reason || timeline?.terminal?.message || "");
    return (
      <p className={`${styles.banner} ${styles.bad}`} role="status">
        <strong>This order was not completed.</strong>{reason ? ` ${reason}.` : ""} Anything we set aside was released and any payment was refunded.
      </p>
    );
  }
  if (status === "PENDING") {
    return (
      <p className={`${styles.banner} ${styles.warn}`} role="status">
        <strong>We&rsquo;re still confirming this order.</strong> You will only be charged if it is confirmed.{" "}
        <Link to={`/checkout/${encodeURIComponent(order.order_id)}`} className="link">Watch it being placed</Link>
      </p>
    );
  }
  return null;
}

function DetailSkeleton({ orderId }: { orderId: string }) {
  return (
    <>
      <div className="title-row"><div><h1>Order <span className={styles.id}>{orderId}</span></h1><p className="sub">&nbsp;</p></div></div>
      <div className={styles.layout} aria-busy="true" aria-label="Loading order">
        <div className={styles.panel}><span className="skel" style={{ width: 90, height: 20 }} />{[0, 1, 2, 3, 4].map((i) => <span key={i} className="skel" style={{ width: `${50 + i * 8}%` }} />)}</div>
        <div className={styles.panel}><span className="skel" style={{ width: 70, height: 20 }} /><span className="skel" style={{ height: 48 }} /><span className="skel" style={{ height: 48 }} /></div>
      </div>
    </>
  );
}
