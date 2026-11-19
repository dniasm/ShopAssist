import type { PaymentMethod } from "../../api/types";
import styles from "./PaymentMethods.module.css";

export const PAYMENT_OPTIONS: { value: PaymentMethod; label: string; note: string }[] = [
  { value: "card_ok", label: "Card that works", note: "Always approved." },
  { value: "card_declined", label: "Card that is declined", note: "Always declined, so you can see what a failed payment looks like." },
  { value: "card_flaky", label: "Unreliable card", note: "Fails about half the time. Try it a few times." },
];

/** Three honest demo payment methods. No card numbers are collected: nothing here is a real payment. */
export function PaymentMethods({ value, onChange, disabled }: { value: PaymentMethod; onChange: (m: PaymentMethod) => void; disabled: boolean }) {
  return (
    <fieldset className={styles.set} disabled={disabled}>
      <legend className={styles.legend}>Payment method</legend>
      <p className={`${styles.lead} muted`}>This is a demo shop. Pick how the payment should behave.</p>
      <div className={styles.options}>
        {PAYMENT_OPTIONS.map((o) => (
          <label key={o.value} className={styles.option}>
            <input type="radio" name="payment_method" className={styles.input} value={o.value} checked={value === o.value} onChange={() => onChange(o.value)} />
            <span className={styles.face}>
              <span className={styles.radio} aria-hidden="true" />
              <span className={styles.text}>
                <span className={styles.label}>{o.label}</span>
                <span className="muted small">{o.note}</span>
              </span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
