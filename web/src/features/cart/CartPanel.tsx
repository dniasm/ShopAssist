import { Link, useNavigate } from "react-router";
import { formatMoney } from "../../api/money";
import type { CartLine } from "../../api/types";
import { ErrorBlock } from "../../components/StateBlocks";
import { useAuth } from "../auth/AuthContext";
import { sizeLabel, colorLabel } from "../catalog/labels";
import { useDrawer } from "../drawer/DrawerContext";
import { QuantityStepper } from "../product/QuantityStepper";
import { ProductTile } from "../tile/ProductTile";
import { cartView, issueText, lineIssue, lineMax } from "./cartMath";
import { useCart } from "./CartContext";
import styles from "./CartPanel.module.css";

/** The cart drawer's content: lines, the money, and the way to check out. */
export function CartPanel() {
  const { user, openSignIn } = useAuth();
  const { cart, status, busySku, actionError, refresh, setQuantity, removeLine, clearActionError } = useCart();
  const { close } = useDrawer();
  const navigate = useNavigate();
  const view = cartView(cart);

  if (!user) {
    return (
      <div className={styles.message}>
        <p className={styles.messageTitle}>Sign in to use the cart</p>
        <p className="muted">Pick a customer to shop as and your cart will be kept for you.</p>
        <button type="button" className="btn primary" onClick={() => openSignIn()}>Sign in</button>
      </div>
    );
  }
  if (status === "loading" && !cart) {
    return (
      <div className={styles.scroll} aria-busy="true" aria-label="Loading your cart">
        {[0, 1].map((i) => (
          <div key={i} className={styles.skelLine}>
            <span className={styles.skelTile} />
            <span className={styles.skelText}><span className="skel" style={{ width: "80%" }} /><span className="skel" style={{ width: "50%" }} /><span className="skel" style={{ width: 96, height: 32 }} /></span>
          </div>
        ))}
      </div>
    );
  }
  if (status === "error" && !cart) {
    return <div className={styles.scroll}><ErrorBlock error={actionError} what="your cart" onRetry={() => void refresh()} /></div>;
  }
  if (!cart || cart.lines.length === 0) {
    return (
      <div className={styles.message}>
        <p className={styles.messageTitle}>Your cart is empty</p>
        <p className="muted">Things you add show up here, with their price and whether they are still in stock.</p>
        <Link to="/shop" className="btn primary" onClick={close}>Browse the shop</Link>
      </div>
    );
  }

  function checkout() {
    close();
    navigate("/checkout");
  }

  return (
    <div className={styles.panel}>
      <div className={styles.scroll}>
        {actionError && (
          <p className={styles.alert} role="alert">
            {actionError} <button type="button" className="btn text" onClick={clearActionError}>Dismiss</button>
          </p>
        )}
        <ul className={styles.lines} aria-label={`${view.count} ${view.count === 1 ? "item" : "items"} in your cart`}>
          {cart.lines.map((line) => (
            <CartLineItem
              key={line.sku_id}
              line={line}
              busy={busySku === line.sku_id}
              onQuantity={(q) => void setQuantity(line.sku_id, q)}
              onRemove={() => void removeLine(line.sku_id)}
              onNavigate={close}
            />
          ))}
        </ul>
      </div>

      <footer className={styles.foot}>
        <dl className={styles.sums}>
          <div><dt>Subtotal</dt><dd>{view.subtotal}</dd></div>
          <div><dt>Shipping</dt><dd>{view.shipping}</dd></div>
          <div className={styles.total}><dt>Total</dt><dd>{view.total}</dd></div>
        </dl>
        {view.freeShippingHint && <p className={`${styles.hint} muted`}>{view.freeShippingHint}</p>}
        {!view.canCheckout && view.issues.length > 0 && (
          <p className={styles.blocked} role="status">
            {view.issues.length === 1 ? "One item needs attention" : `${view.issues.length} items need attention`} before you can check out.
          </p>
        )}
        <button
          type="button"
          className="btn primary large"
          aria-disabled={!view.canCheckout}
          onClick={() => { if (view.canCheckout) checkout(); }}
        >
          Checkout · {view.total}
        </button>
      </footer>
    </div>
  );
}

function CartLineItem({ line, busy, onQuantity, onRemove, onNavigate }: {
  line: CartLine; busy: boolean; onQuantity: (quantity: number) => void; onRemove: () => void; onNavigate: () => void;
}) {
  const issue = lineIssue(line);
  return (
    <li className={styles.line} data-issue={issue ? issue.kind : undefined} aria-busy={busy}>
      <Link to={`/p/${encodeURIComponent(line.product_id)}`} className={styles.tile} onClick={onNavigate} aria-hidden="true" tabIndex={-1}>
        <ProductTile productId={line.product_id} category={line.category} color={line.color} />
      </Link>
      <div className={styles.info}>
        <Link to={`/p/${encodeURIComponent(line.product_id)}`} className={styles.name} onClick={onNavigate}>{line.name}</Link>
        <p className="muted small">{sizeLabel(line.size)} · {colorLabel(line.color)} · {formatMoney(line.unit_price_cents)} each</p>
        <div className={styles.controls}>
          <QuantityStepper compact value={line.quantity} max={lineMax(line)} disabled={busy || line.available <= 0} onChange={onQuantity} />
          <button type="button" className="btn text" onClick={onRemove} disabled={busy} aria-label={`Remove ${line.name} from your cart`}>Remove</button>
        </div>
        {issue && <p className={styles.issue} role="status">{issueText(issue, line.quantity)}</p>}
      </div>
      <span className={styles.lineTotal}>{formatMoney(line.line_total_cents)}</span>
    </li>
  );
}
