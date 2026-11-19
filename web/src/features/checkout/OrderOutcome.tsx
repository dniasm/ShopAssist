import { Link } from "react-router";
import type { Order } from "../../api/types";
import { useCart } from "../cart/CartContext";
import { useDrawer } from "../drawer/DrawerContext";
import { describeFailure, type Failure } from "./saga";
import { OrderSummary } from "./OrderSummary";
import styles from "./OrderOutcome.module.css";

export function SuccessPanel({ order }: { order: Order }) {
  return (
    <section className={styles.panel} aria-labelledby="outcome-title">
      <h2 id="outcome-title" className="section-title">Order confirmed</h2>
      <p>
        Thanks. Your order <strong className={styles.id}>{order.order_id}</strong> is paid and its items are set aside for you.
      </p>
      <OrderSummary
        label="What you ordered"
        lines={order.lines.map((l) => ({ skuId: l.sku_id, name: l.name, size: l.size, color: l.color, quantity: l.quantity, unitPriceCents: l.unit_price_cents }))}
        totals={{ subtotalCents: order.subtotal_cents, shippingCents: order.shipping_cents, totalCents: order.total_cents }}
      />
      <div className={styles.actions}>
        <Link to={`/orders/${encodeURIComponent(order.order_id)}`} className="btn primary">Track this order</Link>
        <Link to="/shop" className="btn">Continue shopping</Link>
      </div>
    </section>
  );
}

/** What went wrong, in plain words, what happened to the money and the cart, and the one sensible next step. */
export function FailurePanel({ failure, nameOf }: { failure: Failure; nameOf: (skuId: string) => string }) {
  const view = describeFailure(failure, nameOf);
  const { show } = useDrawer();
  const { cart } = useCart();
  return (
    <section className={`${styles.panel} ${styles.failure}`} aria-labelledby="outcome-title" role="alert">
      <h2 id="outcome-title" className="section-title">{view.title}</h2>
      {view.details.length > 0 && (
        <ul className={styles.details}>
          {view.details.map((d) => <li key={d}>{d}</li>)}
        </ul>
      )}
      <p>{view.reassurance}</p>
      <div className={styles.actions}>
        {view.next === "review_cart" && <button type="button" className="btn primary" onClick={() => show("cart")}>Review your cart{cart ? ` (${cart.lines.length})` : ""}</button>}
        {view.next === "choose_method" && <Link to="/checkout" className="btn primary">Choose another payment method</Link>}
        {view.next === "try_again" && <Link to="/checkout" className="btn primary">Back to checkout</Link>}
        <Link to="/shop" className="btn">Continue shopping</Link>
      </div>
    </section>
  );
}
