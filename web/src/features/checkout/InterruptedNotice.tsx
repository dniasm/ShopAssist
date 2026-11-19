import styles from "./InterruptedNotice.module.css";

export const DEFAULT_INTERRUPTED_MESSAGE =
  "We lost contact while placing your order. You will only be charged if it is confirmed.";

interface Props {
  /** The server's own message from the `interrupted` event. */
  message: string;
  checking: boolean;
  lastCheck: "pending" | "error" | null;
  onCheck: () => void;
}

/** Shown when the saga stream broke. The outcome is unknown, so this says exactly that: no success, no failure, no claim about
 *  money beyond what the server said. The order record is read every 10 s and on request. */
export function InterruptedNotice({ message, checking, lastCheck, onCheck }: Props) {
  return (
    <section className={styles.notice} aria-labelledby="interrupted-title" role="status">
      <h2 id="interrupted-title" className="section-title">We&rsquo;re waiting for confirmation</h2>
      <p>{message || DEFAULT_INTERRUPTED_MESSAGE}</p>
      <p className="muted">
        This usually settles within a couple of minutes. We check the order every 10 seconds and this page updates by itself, so you
        can leave it open or come back to this address.
      </p>
      <div className={styles.actions}>
        <button type="button" className="btn" onClick={onCheck} disabled={checking}>{checking ? "Checking…" : "Check order status"}</button>
        <span className="muted small" aria-live="polite">
          {lastCheck === "pending" && "Still being processed. It is not confirmed yet."}
          {lastCheck === "error" && "Couldn’t read the order just now. Try again in a moment."}
        </span>
      </div>
    </section>
  );
}
