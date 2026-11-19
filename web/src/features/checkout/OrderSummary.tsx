import { formatMoney } from "../../api/money";
import { colorLabel, sizeLabel } from "../catalog/labels";
import styles from "./OrderSummary.module.css";

export interface SummaryLine { skuId: string; name: string; size: string; color: string; quantity: number; unitPriceCents: number }
export interface SummaryTotals { subtotalCents: number; shippingCents: number; totalCents: number }

/** Lines and the money, for the checkout page (from the cart) and the confirmation (from the order). Same layout both times. */
export function OrderSummary({ lines, totals, label }: { lines: SummaryLine[]; totals: SummaryTotals; label: string }) {
  return (
    <section className={styles.summary} aria-label={label}>
      <ul className={styles.lines}>
        {lines.map((l) => (
          <li key={l.skuId} className={styles.line}>
            <span className={styles.what}>
              <span className={styles.name}>{l.name}</span>
              <span className="muted small">{sizeLabel(l.size)} · {colorLabel(l.color)} · {l.quantity} × {formatMoney(l.unitPriceCents)}</span>
            </span>
            <span className={styles.amount}>{formatMoney(l.unitPriceCents * l.quantity)}</span>
          </li>
        ))}
      </ul>
      <dl className={styles.sums}>
        <div><dt>Subtotal</dt><dd>{formatMoney(totals.subtotalCents)}</dd></div>
        <div><dt>Shipping</dt><dd>{totals.shippingCents === 0 ? "Free" : formatMoney(totals.shippingCents)}</dd></div>
        <div className={styles.total}><dt>Total</dt><dd>{formatMoney(totals.totalCents)}</dd></div>
      </dl>
    </section>
  );
}
