import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { placeOrder } from "../api/endpoints";
import { describeError } from "../api/errors";
import { formatMoney } from "../api/money";
import type { PaymentMethod } from "../api/types";
import { EmptyState } from "../components/StateBlocks";
import { useAuth } from "../features/auth/AuthContext";
import { cartView, issueText } from "../features/cart/cartMath";
import { useCart } from "../features/cart/CartContext";
import { useDrawer } from "../features/drawer/DrawerContext";
import { newIdempotencyKey } from "../features/checkout/idempotency";
import { OrderSummary } from "../features/checkout/OrderSummary";
import { PaymentMethods } from "../features/checkout/PaymentMethods";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import styles from "./CheckoutPage.module.css";

export function CheckoutPage() {
  useDocumentTitle("Checkout");
  const { user, openSignIn } = useAuth();
  const { cart, status, refresh } = useCart();
  const signedIn = user !== null;
  // The cart may have been changed elsewhere (the assistant, another tab): read it fresh each time this page opens.
  const [fresh, setFresh] = useState(false);
  useEffect(() => {
    if (!signedIn) return;
    let live = true;
    void refresh().finally(() => { if (live) setFresh(true); });
    return () => { live = false; };
  }, [signedIn, refresh]);

  if (!user) {
    return (
      <>
        <div className="title-row"><h1>Checkout</h1></div>
        <EmptyState title="Sign in to check out" action={<button type="button" className="btn primary" onClick={() => openSignIn()}>Sign in</button>}>
          Your cart belongs to the customer you shop as.
        </EmptyState>
      </>
    );
  }
  if (!fresh || (status === "loading" && !cart)) {
    return (
      <>
        <div className="title-row"><h1>Checkout</h1></div>
        <div className={styles.layout} aria-busy="true" aria-label="Loading your cart">
          <div className={styles.skel}><span className="skel" style={{ width: 160, height: 20 }} /><span className="skel" style={{ height: 64 }} /><span className="skel" style={{ height: 64 }} /></div>
          <div className={styles.skel}><span className="skel" style={{ height: 20, width: 120 }} /><span className="skel" style={{ height: 48 }} /><span className="skel" style={{ height: 48 }} /></div>
        </div>
      </>
    );
  }
  if (!cart || cart.lines.length === 0) {
    return (
      <>
        <div className="title-row"><h1>Checkout</h1></div>
        <EmptyState title="Your cart is empty" action={<Link to="/shop" className="btn primary">Browse the shop</Link>}>
          Add something first, then come back to check out.
        </EmptyState>
      </>
    );
  }
  // key: a new visit to /checkout starts with a new idempotency key (see CheckoutForm)
  return <CheckoutForm />;
}

function CheckoutForm() {
  const { cart } = useCart();
  const { show } = useDrawer();
  const navigate = useNavigate();
  const [method, setMethod] = useState<PaymentMethod>("card_ok");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ONE key per opening of this page, reused for every click and retry: the server treats the same key as the same order,
  // so a double click or a retry after a flaky network can never create a second order.
  const [idempotencyKey] = useState(newIdempotencyKey);
  // A ref, not state: the second click of a double click lands before React re-renders with `submitting`.
  const inFlight = useRef(false);

  const view = cartView(cart);
  if (!cart) return null;

  async function submit() {
    if (inFlight.current || !view.canCheckout) return;
    inFlight.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const accepted = await placeOrder(idempotencyKey, method);
      // The saga's progress lives on its own address so a refresh brings it back.
      navigate(`/checkout/${encodeURIComponent(accepted.order_id)}`, { replace: true });
    } catch (err) {
      setError(describeError(err));
      inFlight.current = false;
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="title-row">
        <div>
          <h1>Checkout</h1>
          <p className="sub">{view.count} {view.count === 1 ? "item" : "items"}, {formatMoney(cart.total_cents)} with shipping.</p>
        </div>
      </div>

      <div className={styles.layout}>
        <div className={styles.main}>
          {!view.canCheckout && (
            <div className={styles.blocked} role="alert">
              <p><strong>Fix these items before you can place the order.</strong></p>
              <ul>
                {view.issues.map(({ skuId, issue }) => {
                  const line = cart.lines.find((l) => l.sku_id === skuId);
                  return line ? <li key={skuId}>{line.name}: {issueText(issue, line.quantity)}</li> : null;
                })}
              </ul>
              <button type="button" className="btn" onClick={() => show("cart")}>Open your cart</button>
            </div>
          )}

          <PaymentMethods value={method} onChange={setMethod} disabled={submitting} />

          <div className={styles.place}>
            <button type="button" className="btn primary large" aria-disabled={submitting || !view.canCheckout} onClick={() => void submit()}>
              {submitting ? "Placing your order…" : `Place order · ${view.total}`}
            </button>
            <p className="muted small">No real payment happens. You will watch each step as it runs.</p>
            {error && <p className={styles.error} role="alert">{error} You can press Place order again; it will not create a second order.</p>}
          </div>
        </div>

        <aside className={styles.side} aria-label="Order summary">
          <h2 className="section-title">Order summary</h2>
          <OrderSummary
            label="Items in your order"
            lines={cart.lines.map((l) => ({ skuId: l.sku_id, name: l.name, size: l.size, color: l.color, quantity: l.quantity, unitPriceCents: l.unit_price_cents }))}
            totals={{ subtotalCents: cart.subtotal_cents, shippingCents: cart.shipping_cents, totalCents: cart.total_cents }}
          />
          {view.freeShippingHint && <p className="muted small">{view.freeShippingHint}</p>}
        </aside>
      </div>
    </>
  );
}
