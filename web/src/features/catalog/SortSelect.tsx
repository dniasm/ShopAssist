import type { SortKey } from "../../api/types";
import { SORT_KEYS, SORT_LABELS } from "./query";
import styles from "./SortSelect.module.css";

export function SortSelect({ value, onChange }: { value: SortKey; onChange: (sort: SortKey) => void }) {
  return (
    <label className={styles.label}>
      <span className="muted">Sort by</span>
      <select className={`field ${styles.select}`} value={value} onChange={(e) => { const next = SORT_KEYS.find((k) => k === e.target.value); if (next) onChange(next); }}>
        {SORT_KEYS.map((k) => <option key={k} value={k}>{SORT_LABELS[k]}</option>)}
      </select>
    </label>
  );
}
