import { formatTime } from "./format";
import type { Timeline } from "./timeline";
import styles from "./OrderTimeline.module.css";

/** The delivery route: the same line-and-nodes language as the checkout steps. Filled = reached, pulsing = where the order is now,
 *  hollow = still to come. A cancelled or failed order is a banner elsewhere, not a step here: the steps it reached just stop. */
export function OrderTimeline({ timeline, stopped }: { timeline: Timeline; stopped: boolean }) {
  return (
    <ol className={styles.trace} aria-label="Delivery progress" data-stopped={stopped}>
      {timeline.steps.map((step) => (
        <li key={step.status} className={styles.hop} data-state={step.state} aria-current={step.state === "current" ? "step" : undefined}>
          <span className={styles.dot} aria-hidden="true" />
          <span className={styles.body}>
            <span className={styles.title}>
              {step.label}
              <span className="sr-only">, {step.state === "done" ? "done" : step.state === "current" ? "current step" : "not yet"}</span>
            </span>
            {step.at && <time className={`${styles.sub} muted`} dateTime={step.at}>{formatTime(step.at)}</time>}
            {step.message && step.state !== "pending" && <span className={`${styles.sub} muted`}>{step.message}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}
