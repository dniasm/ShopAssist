import { colorHex } from "../tile/colors";
import { colorLabel } from "../catalog/labels";
import { availabilityText, type ColorOption, type SizeOption } from "./variants";
import styles from "./VariantPickers.module.css";

/** Swatches as a radio group: arrow keys move between them, the whole group is one tab stop. */
export function ColorPicker({ options, value, onChange }: { options: ColorOption[]; value: string | null; onChange: (color: string) => void }) {
  return (
    <fieldset className={styles.set}>
      <legend className={styles.legend}>
        Colour{value && <span className={styles.chosen}>{colorLabel(value)}</span>}
      </legend>
      <div className={styles.swatches}>
        {options.map((o) => {
          const soldOut = o.availability === "sold_out";
          const name = `${colorLabel(o.color)}${soldOut ? ", sold out" : ""}`;
          return (
            <label key={o.color} className={styles.swatchLabel} title={name}>
              <input type="radio" name="color" className={styles.input} value={o.color} checked={value === o.color} onChange={() => onChange(o.color)} />
              <span className={styles.swatch} data-sold-out={soldOut} style={{ background: colorHex(o.color) }} />
              <span className="sr-only">{name}</span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

export function SizePicker({ options, value, onChange }: { options: SizeOption[]; value: string | null; onChange: (size: string) => void }) {
  return (
    <fieldset className={styles.set}>
      <legend className={styles.legend}>
        Size{value && <span className={styles.chosen}>{value}</span>}
      </legend>
      <div className={styles.sizes}>
        {options.map((o) => {
          const disabled = o.availability === "sold_out" || o.availability === "not_offered";
          const hint = availabilityText(o.availability, o.available);
          return (
            <label key={o.size} className={styles.sizeLabel} title={disabled || o.availability === "low" ? hint : undefined}>
              <input type="radio" name="size" className={styles.input} value={o.size} disabled={disabled} checked={value === o.size} onChange={() => onChange(o.size)} />
              <span className={styles.size} data-low={o.availability === "low"}>{o.size === "ONE" ? "One size" : o.size}</span>
              <span className="sr-only">{disabled || o.availability === "low" ? `, ${hint.toLowerCase()}` : ""}</span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
