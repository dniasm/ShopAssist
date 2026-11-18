import { Icon } from "../../components/Icon";
import { clampQuantity } from "./variants";
import styles from "./QuantityStepper.module.css";

interface Props { value: number; max: number; disabled?: boolean; onChange: (quantity: number) => void }

export function QuantityStepper({ value, max, disabled = false, onChange }: Props) {
  const set = (n: number) => onChange(clampQuantity(n, max));
  return (
    <div className={styles.group} role="group" aria-label="Quantity">
      <button type="button" className={styles.button} onClick={() => set(value - 1)} disabled={disabled || value <= 1} aria-label="Decrease quantity">
        <Icon name="minus" />
      </button>
      <input
        className={styles.input}
        type="number"
        inputMode="numeric"
        min={1}
        max={Math.max(1, max)}
        value={value}
        disabled={disabled}
        aria-label="Quantity"
        onChange={(e) => set(e.target.valueAsNumber)}
      />
      <button type="button" className={styles.button} onClick={() => set(value + 1)} disabled={disabled || value >= max} aria-label="Increase quantity">
        <Icon name="plus" />
      </button>
    </div>
  );
}
