import { useState } from "react";
import { Icon } from "../../components/Icon";
import { argsSummary, toolLabel } from "./trace";
import type { ToolStep } from "./transcript";
import styles from "./ToolTrace.module.css";

/** The route the assistant took, one line per tool: what it did, with what, and what came back. Open while it is working, folded away
 *  once the answer is there (the shopper can open it again). */
export function ToolTrace({ tools, working }: { tools: ToolStep[]; working: boolean }) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  if (tools.length === 0) return null;
  const open = userOpen ?? working;
  const label = working ? "Looking things up\u2026" : `Looked things up \u00B7 ${tools.length} ${tools.length === 1 ? "step" : "steps"}`;
  return (
    <div className={styles.trace}>
      <button type="button" className={styles.toggle} aria-expanded={open} onClick={() => setUserOpen(!open)}>
        <span className={styles.caret} data-open={open}><Icon name="chevron" size={14} /></span>
        <span>{label}</span>
      </button>
      {open && (
        <ol className={styles.steps}>
          {tools.map((t, i) => {
            const args = argsSummary(t.args);
            return (
              <li key={`${t.tool}-${i}`} className={styles.step} data-state={t.state}>
                <span className={styles.dot} aria-hidden="true" />
                <span className={styles.stepText}>
                  <span className={styles.stepName}>{toolLabel(t.tool, t.state === "running")}{args && <span className="muted"> {"\u00B7"} {args}</span>}</span>
                  {t.summary && <span className="muted small">{t.summary}</span>}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
