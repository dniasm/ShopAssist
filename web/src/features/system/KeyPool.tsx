import { useEffect, useState } from "react";
import type { LlmKeyStatus, LlmLimits, LlmStatus } from "../../api/types";
import { coolingText, isRulesOnly, keyState, meter, poolSummary, secondsUntil, type MeterView } from "./llm";
import styles from "./KeyPool.module.css";

const LIMITS: { field: keyof LlmLimits; left: "rpm_left" | "rpd_left" | "tpm_left" | "tpd_left"; short: string; long: string }[] = [
  { field: "rpm", left: "rpm_left", short: "RPM", long: "Requests per minute" },
  { field: "rpd", left: "rpd_left", short: "RPD", long: "Requests per day" },
  { field: "tpm", left: "tpm_left", short: "TPM", long: "Tokens per minute" },
  { field: "tpd", left: "tpd_left", short: "TPD", long: "Tokens per day" },
];

/** The Groq key pool: each key's state and what is left of its four limits. A key is only ever shown as the masked label the API
 *  sends ("key#1 …WDDu"); nothing else about it exists in this app. With no keys the assistant runs on rules alone, and it says so. */
export function KeyPool({ status }: { status: LlmStatus }) {
  const now = useNow(status.keys.some((k) => k.state === "cooling"));
  if (isRulesOnly(status)) {
    return (
      <div className={styles.rules}>
        <p className={styles.rulesTitle}>LLM disabled {"—"} rules only</p>
        <p className="muted">There are no Groq keys in use, so the assistant understands questions with its rules parser alone. Nothing else changes.</p>
      </div>
    );
  }
  return (
    <div className={styles.pool}>
      <p className={styles.summary}>
        <span><span className="muted">Model</span> <code>{status.model}</code></span>
        <span className="muted">{poolSummary(status)}</span>
      </p>
      <ul className={styles.keys} aria-label="Keys">
        {status.keys.map((k) => <KeyRow key={k.key} k={k} limits={status.limits} now={now} />)}
      </ul>
      <p className={`${styles.note} muted`}>
        Each call goes to the key with the most room on all four limits. A meter shows what is left: the bar turns amber under 20%, and at zero the figure turns red.
      </p>
    </div>
  );
}

function KeyRow({ k, limits, now }: { k: LlmKeyStatus; limits: LlmLimits; now: number }) {
  const state = keyState(k);
  const cooling = k.state === "cooling" ? coolingText(secondsUntil(k.cooling_until, now)) : null;
  return (
    <li className={styles.key}>
      <div className={styles.keyHead}>
        <code className={styles.label}>{k.key}</code>
        <span className={`pill ${state.tone === "neutral" ? "" : state.tone}`}>{state.label}</span>
        {cooling && <span className="muted small">{cooling}</span>}
      </div>
      <div className={styles.meters}>
        {LIMITS.map((l) => <Meter key={l.field} label={l.long} short={l.short} limit={limits[l.field]} left={k[l.left]} />)}
      </div>
    </li>
  );
}

function Meter({ label, short, limit, left }: { label: string; short: string; limit: number; left: number | null | undefined }) {
  const m: MeterView = meter(left, limit);
  return (
    <div className={styles.meter}>
      <div className={styles.meterTop}>
        <span title={label}>{short}</span>
        <span className={m.tone === "empty" ? styles.emptyText : "muted small"}>{m.text}</span>
      </div>
      <div
        className={styles.track}
        role="meter"
        aria-label={`${label} left`}
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={m.fraction === null ? undefined : Math.round(m.fraction * limit)}
        aria-valuetext={m.text}
      >
        {m.fraction !== null && <span className={styles.fill} data-tone={m.tone} style={{ width: `${Math.max(m.fraction * 100, m.tone === "empty" ? 0 : 2)}%` }} />}
      </div>
    </div>
  );
}

/** A clock that ticks every second, but only while something on the page counts down. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}
