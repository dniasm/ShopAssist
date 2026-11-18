import type { Facet } from "../../api/types";
import { colorLabel, sizeLabel, slugLabel, sortSizes } from "./labels";
import type { ListField } from "./query";

export interface FacetOption { value: string; label: string; count: number; selected: boolean }

const API_FIELD: Record<ListField, string> = { category: "category", brand: "brand", color: "color", size: "size" };

function labelFor(field: ListField, value: string): string {
  if (field === "category") return slugLabel(value);
  if (field === "color") return colorLabel(value);
  return sizeLabel(value);
}

/** The options to draw for one filter group.
 *  - Counts come straight from the API ("products matching every OTHER active filter, plus this value").
 *  - A selected value the API no longer lists (its count fell to 0) stays, with count 0, so it can still be unticked.
 *  - Unselected values with no products are dropped.
 *  - Order: sizes in shopper order, colours by popularity, categories and brands alphabetically. */
export function facetOptions(facets: readonly Facet[], field: ListField, selected: readonly string[]): FacetOption[] {
  const counts = new Map<string, number>();
  for (const v of facets.find((f) => f.field === API_FIELD[field])?.values ?? []) counts.set(v.value, v.count);
  for (const s of selected) if (!counts.has(s)) counts.set(s, 0);

  const options = [...counts.entries()]
    .filter(([value, count]) => count > 0 || selected.includes(value))
    .map(([value, count]) => ({ value, count, label: labelFor(field, value), selected: selected.includes(value) }));

  if (field === "size") {
    const order = sortSizes(options.map((o) => o.value));
    return options.sort((a, b) => order.indexOf(a.value) - order.indexOf(b.value));
  }
  if (field === "color") return options.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  return options.sort((a, b) => a.label.localeCompare(b.label));
}
