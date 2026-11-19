import { sagaRows, type SagaRow, type SagaState } from "./saga";
import styles from "./SagaView.module.css";

/** The server's step messages are for what is happening or went wrong. A finished step's message is bookkeeping ("total 3198
 *  cents"), so it stays out of the way; the undo row always explains itself. */
function showMessage(row: SagaRow): boolean {
  if (!row.message || row.message.trim().toLowerCase() === row.label.toLowerCase()) return false;
  return row.status === "active" || row.status === "failed" || (row.compensation && row.status !== "pending");
}

const STATUS_WORDS: Record<SagaRow["status"], string> = { pending: "waiting", active: "in progress", done: "done", failed: "failed" };

/** The route of an order: a line and nodes, the same language as the v1 route view. Colour only says state: the data hue for
 *  what is done, a pulse for what is running, red for what failed, amber for the undo step. Driven entirely by the saga state. */
export function SagaView({ saga, paused = false }: { saga: SagaState; paused?: boolean }) {
  const rows = sagaRows(saga);
  return (
    <ol className={styles.trace} aria-label="Checkout progress" data-paused={paused}>
      {rows.map((row) => (
        <li key={row.key} className={styles.hop} data-state={row.status} data-undo={row.compensation} aria-current={row.status === "active" ? "step" : undefined}>
          <span className={styles.dot} aria-hidden="true" />
          <span className={styles.body}>
            <span className={styles.title}>
              {row.label}
              <span className="sr-only">, {paused && row.status === "active" ? "last known step, outcome unknown" : STATUS_WORDS[row.status]}</span>
            </span>
            {showMessage(row) && <span className={`${styles.sub} muted`}>{row.message}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}
