import { useEffect, useState, type FormEvent } from "react";
import { centsToDollarsText, formatMoney, parseDollarsToCents } from "../../api/money";
import styles from "./PriceFilter.module.css";

interface Props {
  minCents: number | null;
  maxCents: number | null;
  /** The price range of the current results, for placeholders and the hint. */
  rangeMinCents: number;
  rangeMaxCents: number;
  onCommit: (minCents: number | null, maxCents: number | null) => void;
}

const asText = (cents: number | null) => (cents === null ? "" : centsToDollarsText(cents));

/** Two dollar fields. They commit on Enter, on Apply, or when focus leaves the pair; an unreadable value is reverted. */
export function PriceFilter({ minCents, maxCents, rangeMinCents, rangeMaxCents, onCommit }: Props) {
  const [min, setMin] = useState(asText(minCents));
  const [max, setMax] = useState(asText(maxCents));
  const [invalid, setInvalid] = useState(false);

  useEffect(() => { setMin(asText(minCents)); setMax(asText(maxCents)); setInvalid(false); }, [minCents, maxCents]);

  function commit() {
    const lo = min.trim() === "" ? null : parseDollarsToCents(min);
    const hi = max.trim() === "" ? null : parseDollarsToCents(max);
    if ((min.trim() !== "" && lo === null) || (max.trim() !== "" && hi === null)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    if (lo !== minCents || hi !== maxCents) onCommit(lo, hi);
  }

  function submit(e: FormEvent) { e.preventDefault(); commit(); }

  const hasRange = rangeMaxCents > 0;
  return (
    <form className={styles.form} onSubmit={submit} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) commit(); }}>
      <div className={styles.row}>
        <label className={styles.field}>
          <span className="sr-only">Minimum price in dollars</span>
          <span className={styles.prefix} aria-hidden="true">$</span>
          <input className={styles.input} inputMode="decimal" autoComplete="off" placeholder={hasRange ? String(Math.floor(rangeMinCents / 100)) : "Min"}
                 value={min} aria-invalid={invalid} onChange={(e) => setMin(e.target.value)} />
        </label>
        <span className="muted" aria-hidden="true">to</span>
        <label className={styles.field}>
          <span className="sr-only">Maximum price in dollars</span>
          <span className={styles.prefix} aria-hidden="true">$</span>
          <input className={styles.input} inputMode="decimal" autoComplete="off" placeholder={hasRange ? String(Math.ceil(rangeMaxCents / 100)) : "Max"}
                 value={max} aria-invalid={invalid} onChange={(e) => setMax(e.target.value)} />
        </label>
        <button type="submit" className="btn">Apply</button>
      </div>
      {invalid && <p className={styles.error} role="alert">Enter prices like 25 or 24.99.</p>}
      {hasRange && !invalid && (
        <p className="muted small">These results run from {formatMoney(rangeMinCents)} to {formatMoney(rangeMaxCents)}.</p>
      )}
    </form>
  );
}
