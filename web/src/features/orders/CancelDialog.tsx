import { useEffect, useRef } from "react";
import type { Order } from "../../api/types";
import { TOO_LATE_MESSAGE, refundText } from "./orderStatus";
import styles from "./CancelDialog.module.css";

interface Props {
  open: boolean;
  order: Pick<Order, "order_id" | "total_cents">;
  /** The order can still be cancelled right now (it may have moved on while the dialog was open). */
  cancellable: boolean;
  busy: boolean;
  /** What went wrong with the last attempt, in words. */
  problem: string | null;
  onKeep: () => void;
  onConfirm: () => void;
}

/** "Cancel order?" with the refund amount and what happens to the items. If the order ships while this is open, or the server
 *  answers 409, it says so plainly and offers only Close. */
export function CancelDialog({ open, order, cancellable, busy, problem, onKeep, onConfirm }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const keepButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) { dialog.showModal(); keepButton.current?.focus(); }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const tooLate = !cancellable;
  return (
    <dialog ref={ref} className={styles.dialog} aria-labelledby="cancel-title" onClose={onKeep} onClick={(e) => { if (e.target === ref.current && !busy) onKeep(); }}>
      <div className={styles.body}>
        <h2 id="cancel-title" className="section-title">{tooLate ? "This order can't be cancelled" : `Cancel order ${order.order_id}?`}</h2>
        {tooLate ? <p>{problem ?? TOO_LATE_MESSAGE}</p> : (
          <>
            <p>{refundText(order)}</p>
            <p className="muted">You can't undo this. If you still want the items you will need to order them again.</p>
          </>
        )}
        {problem && !tooLate && <p className={styles.problem} role="alert">{problem}</p>}
        <div className={styles.actions}>
          <button type="button" ref={keepButton} className="btn" onClick={onKeep} disabled={busy}>{tooLate ? "Close" : "Keep order"}</button>
          {!tooLate && (
            <button type="button" className={`btn ${styles.danger}`} onClick={onConfirm} aria-disabled={busy}>
              {busy ? "Cancelling…" : "Cancel order"}
            </button>
          )}
        </div>
      </div>
    </dialog>
  );
}
