import { formatMoney } from "../../api/money";
import { Icon } from "../../components/Icon";
import { colorLabel, sizeLabel, slugLabel } from "./labels";
import {
  LIST_FIELDS, activeFilterCount, normalizeCatalogQuery, toggleListValue, withPrice, withText, clearFilters,
  type CatalogQuery, type ListField,
} from "./query";
import styles from "./ActiveFilters.module.css";

interface Chip { key: string; label: string; remove: () => CatalogQuery }

function chipLabel(field: ListField, value: string): string {
  if (field === "category") return slugLabel(value);
  if (field === "color") return colorLabel(value);
  if (field === "size") return `Size ${sizeLabel(value)}`;
  return value;
}

function priceLabel(min: number | null, max: number | null): string {
  if (min !== null && max !== null) return `${formatMoney(min)} to ${formatMoney(max)}`;
  if (min !== null) return `From ${formatMoney(min)}`;
  return `Up to ${formatMoney(max ?? 0)}`;
}

export function chipsFor(query: CatalogQuery): Chip[] {
  const chips: Chip[] = [];
  if (query.q) chips.push({ key: "q", label: `“${query.q}”`, remove: () => withText(query, "") });
  for (const field of LIST_FIELDS) {
    for (const value of query[field]) {
      chips.push({ key: `${field}:${value}`, label: chipLabel(field, value), remove: () => toggleListValue(query, field, value) });
    }
  }
  if (query.minPriceCents !== null || query.maxPriceCents !== null) {
    chips.push({ key: "price", label: priceLabel(query.minPriceCents, query.maxPriceCents), remove: () => withPrice(query, null, null) });
  }
  return chips;
}

/** What is narrowing the results right now, each removable with one click, plus "Clear all". */
export function ActiveFilters({ query, onChange }: { query: CatalogQuery; onChange: (next: CatalogQuery) => void }) {
  const chips = chipsFor(query);
  if (chips.length === 0) return null;
  const clearable = activeFilterCount(query) + (query.q ? 1 : 0) > 1;
  return (
    <ul className={styles.row} aria-label="Active filters">
      {chips.map((chip) => (
        <li key={chip.key}>
          <button type="button" className={styles.chip} onClick={() => onChange(normalizeCatalogQuery(chip.remove()))} aria-label={`Remove filter: ${chip.label}`}>
            <span>{chip.label}</span>
            <Icon name="close" size={12} />
          </button>
        </li>
      ))}
      {clearable && (
        <li>
          <button type="button" className="btn text" onClick={() => onChange({ ...clearFilters(query), q: "" })}>Clear all</button>
        </li>
      )}
    </ul>
  );
}
