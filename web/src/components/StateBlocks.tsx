import type { ReactNode } from "react";
import { describeError } from "../api/errors";
import styles from "./StateBlocks.module.css";

/** Errors live inside the block that failed, with one sentence and a retry. Never a full-screen banner. */
export function ErrorBlock({ error, what, onRetry }: { error: unknown; what: string; onRetry?: () => void }) {
  return (
    <div className={styles.error} role="alert">
      <p>
        <strong>Couldn&rsquo;t load {what}.</strong> {describeError(error)}
      </p>
      {onRetry && <button type="button" className="btn" onClick={onRetry}>Try again</button>}
    </div>
  );
}

/** An empty section keeps its frame and says one thing, with the next step beside it. */
export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={styles.empty}>
      <p className={styles.emptyTitle}>{title}</p>
      {children && <p className="muted">{children}</p>}
      {action && <div className={styles.emptyAction}>{action}</div>}
    </div>
  );
}
