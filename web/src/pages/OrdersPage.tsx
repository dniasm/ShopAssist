import { Link } from "react-router";
import { describeError } from "../api/errors";
import { formatMoney } from "../api/money";
import { EmptyState, ErrorBlock } from "../components/StateBlocks";
import { useAuth } from "../features/auth/AuthContext";
import { pluralize } from "../features/catalog/labels";
import { formatDateTime } from "../features/orders/format";
import { ProductTile } from "../features/tile/ProductTile";
import { itemCount } from "../features/orders/orderStatus";
import { StatusPill } from "../features/orders/StatusPill";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useOrders, type OrderRowData } from "../hooks/useOrders";
import styles from "./OrdersPage.module.css";

/** /orders: the shopper's orders, newest first. Each row's status pill follows the live `order` events. */
export function OrdersPage() {
  useDocumentTitle("My orders");
  const { user, openSignIn } = useAuth();

  if (!user) {
    return (
      <>
        <div className="title-row"><h1>My orders</h1></div>
        <EmptyState title="Sign in to see your orders" action={<button type="button" className="btn primary" onClick={() => openSignIn()}>Sign in</button>}>
          Orders belong to the customer who placed them.
        </EmptyState>
      </>
    );
  }
  // key: switching user starts from that user's list
  return <OrdersList key={user.user_id} />;
}

function OrdersList() {
  const orders = useOrders();
  const sub = orders.status === "ready"
    ? orders.orders.length === 0 ? "Nothing ordered yet." : `${pluralize(orders.orders.length, "order")}${orders.hasMore ? " shown, newest first" : ", newest first"}.`
    : " ";

  return (
    <>
      <div className="title-row">
        <div>
          <h1>My orders</h1>
          <p className="sub">{sub}</p>
        </div>
      </div>

      {orders.status === "loading" && <OrdersSkeleton />}
      {orders.status === "error" && <ErrorBlock error={orders.error} what="your orders" onRetry={orders.retry} />}
      {orders.status === "ready" && orders.orders.length === 0 && (
        <EmptyState title="No orders yet" action={<Link to="/shop" className="btn primary">Browse the shop</Link>}>
          When you place an order it shows up here, and you can follow it until it arrives.
        </EmptyState>
      )}
      {orders.status === "ready" && orders.orders.length > 0 && (
        <>
          <ul className={styles.list} aria-label="Orders">
            {orders.orders.map((o) => <OrderRow key={o.order_id} order={o} />)}
          </ul>
          <div className={styles.more}>
            {orders.moreError !== null && <p className={styles.moreError} role="alert">{describeError(orders.moreError)}</p>}
            {orders.hasMore && (
              <button type="button" className="btn" onClick={orders.loadMore} disabled={orders.loadingMore}>
                {orders.loadingMore ? "Loading…" : orders.moreError !== null ? "Try again" : "Load more"}
              </button>
            )}
          </div>
        </>
      )}
    </>
  );
}

function OrderRow({ order }: { order: OrderRowData }) {
  const count = itemCount(order);
  const shown = order.lines.slice(0, 2);
  const extra = order.lines.length - shown.length;
  return (
    <li>
      <Link to={`/orders/${encodeURIComponent(order.order_id)}`} className={styles.row}>
        <span className={styles.tiles} aria-hidden="true">
          {shown.map((l) => <span key={l.sku_id} className={styles.tile}><ProductTile productId={l.product_id} category={l.category} color={l.color} /></span>)}
          {extra > 0 && <span className={styles.extra}>+{extra}</span>}
        </span>
        <span className={styles.who}>
          <span className={styles.id}>{order.order_id}</span>
          <span className="muted small">{formatDateTime(order.created_at)}</span>
        </span>
        <span className={styles.items}>{pluralize(count, "item")}</span>
        <span className={styles.status}><StatusPill status={order.liveStatus} /></span>
        <span className={styles.total}>{formatMoney(order.total_cents)}</span>
      </Link>
    </li>
  );
}

function OrdersSkeleton() {
  return (
    <ul className={styles.list} aria-busy="true" aria-label="Loading orders">
      {[0, 1, 2, 3].map((i) => (
        <li key={i} className={styles.row} aria-hidden="true">
          <span className={styles.tiles}><span className={`${styles.tile} ${styles.skelTile}`} /></span>
          <span className={styles.who}><span className="skel" style={{ width: 90 }} /><span className="skel" style={{ width: 130 }} /></span>
          <span className={styles.items}><span className="skel" style={{ width: 50 }} /></span>
          <span className={styles.status}><span className="skel" style={{ width: 70, height: 22 }} /></span>
          <span className={styles.total}><span className="skel" style={{ width: 60 }} /></span>
        </li>
      ))}
    </ul>
  );
}
