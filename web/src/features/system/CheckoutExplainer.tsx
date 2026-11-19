import { Link } from "react-router";
import { listOrders } from "../../api/endpoints";
import { useAuth } from "../auth/AuthContext";
import { useAsyncData } from "../../hooks/useAsyncData";
import { CHECKOUT_STEPS, COMPENSATION_NOTE, DELIVERY_NOTE, SAFETY_NOTES } from "./explainer";
import styles from "./CheckoutExplainer.module.css";

/** "What happens when you check out": the saga's steps in plain words, what is undone when one fails, and, for a signed-in shopper
 *  with an order, a link to watch a real one. */
export function CheckoutExplainer() {
  return (
    <div className={styles.root}>
      <ol className={styles.steps}>
        {CHECKOUT_STEPS.map((s) => (
          <li key={s.step} className={styles.step}>
            <h3 className={styles.stepName}>{s.label}</h3>
            <p>{s.does}</p>
            <p className={`${styles.fails} muted`}><strong>If it fails:</strong> {s.ifFails}</p>
          </li>
        ))}
      </ol>
      <p>{COMPENSATION_NOTE}</p>
      <p>{DELIVERY_NOTE}</p>
      <ul className={styles.notes}>
        {SAFETY_NOTES.map((n) => <li key={n}>{n}</li>)}
      </ul>
      <LiveOrderLink />
    </div>
  );
}

function LiveOrderLink() {
  const { user } = useAuth();
  if (!user) return <p className="muted">Sign in and place an order to watch these steps happen on a real one. <Link to="/shop" className="link">Browse the shop</Link></p>;
  return <LatestOrder key={user.user_id} />;
}

function LatestOrder() {
  const latest = useAsyncData((signal) => listOrders({ page_size: 1 }, signal), "latest-order");
  if (latest.status === "loading") return <p className="muted">Looking for your latest order{"…"}</p>;
  const order = latest.status === "ready" ? latest.data.orders[0] : undefined;
  if (!order) {
    return <p className="muted">You have no orders yet. <Link to="/shop" className="link">Place one</Link> and follow it live.</p>;
  }
  return (
    <p>
      Your latest order is <code>{order.order_id}</code>. <Link to={`/orders/${encodeURIComponent(order.order_id)}`} className="link">Open it</Link> to see its steps and delivery timeline as they happen.
    </p>
  );
}
