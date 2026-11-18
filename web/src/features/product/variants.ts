/* Variant logic for the product page: a (colour, size) pair resolves to one SKU, and the stock array says how many of
 * it can be bought. Pure functions so the rules are testable without a browser. */

import type { Product, StockEntry } from "../../api/types";

/** Fewer than this many left is shown as "Only N left". */
export const LOW_STOCK_BELOW = 5;
/** The cart accepts at most 10 of one line (PLAN: PUT /cart/lines/{sku_id}, max 10). */
export const MAX_PER_LINE = 10;

export type Availability = "in_stock" | "low" | "sold_out" | "not_offered";

export interface ColorOption { color: string; availability: Exclude<Availability, "not_offered">; available: number }
export interface SizeOption { size: string; skuId: string | null; availability: Availability; available: number }

export function availabilityOf(available: number): Exclude<Availability, "not_offered"> {
  if (available <= 0) return "sold_out";
  return available < LOW_STOCK_BELOW ? "low" : "in_stock";
}

export function availabilityText(a: Availability, available: number): string {
  switch (a) {
    case "sold_out": return "Sold out";
    case "low": return `Only ${available} left`;
    case "not_offered": return "Not available in this colour";
    case "in_stock": return "In stock";
  }
}

export function findStock(stock: readonly StockEntry[], color: string, size: string): StockEntry | null {
  return stock.find((s) => s.color === color && s.size === size) ?? null;
}

/** One entry per colour of the product, in the product's own order. A colour is sold out when none of its SKUs has stock. */
export function colorOptions(product: Pick<Product, "colors">, stock: readonly StockEntry[]): ColorOption[] {
  return product.colors.map((color) => {
    const available = stock.filter((s) => s.color === color).reduce((sum, s) => sum + Math.max(0, s.available), 0);
    return { color, available, availability: availabilityOf(available) };
  });
}

/** One entry per size of the product, in display order, for the chosen colour. */
export function sizeOptions(product: Pick<Product, "sizes">, stock: readonly StockEntry[], color: string): SizeOption[] {
  return product.sizes.map((size) => {
    const entry = findStock(stock, color, size);
    if (!entry) return { size, skuId: null, available: 0, availability: "not_offered" };
    return { size, skuId: entry.sku_id, available: Math.max(0, entry.available), availability: availabilityOf(entry.available) };
  });
}

export interface Selection { color: string | null; size: string | null }

/** First colour that still has stock, otherwise the first colour. A single-size product ("ONE") selects its size. */
export function initialSelection(product: Pick<Product, "colors" | "sizes">, stock: readonly StockEntry[], wanted?: Partial<Selection>): Selection {
  const colors = colorOptions(product, stock);
  const wantedColor = colors.find((c) => c.color === wanted?.color);
  const color = (wantedColor ?? colors.find((c) => c.availability !== "sold_out") ?? colors[0])?.color ?? null;
  return { color, size: color ? validSize(product, stock, color, wanted?.size ?? null) : null };
}

/** Keeps `size` only if it can be bought in `color`; auto-selects a lone size. */
export function validSize(product: Pick<Product, "sizes">, stock: readonly StockEntry[], color: string, size: string | null): string | null {
  const options = sizeOptions(product, stock, color);
  const chosen = options.find((o) => o.size === size);
  if (chosen && chosen.availability !== "sold_out" && chosen.availability !== "not_offered") return chosen.size;
  const only = options.length === 1 ? options[0] : undefined;
  return only && only.availability !== "sold_out" && only.availability !== "not_offered" ? only.size : null;
}

export interface Resolved { skuId: string; availability: Availability; available: number; maxQuantity: number }

/** The SKU for the current selection, or null until both a colour and a size are chosen (or the pair does not exist). */
export function resolveSelection(stock: readonly StockEntry[], sel: Selection): Resolved | null {
  if (!sel.color || !sel.size) return null;
  const entry = findStock(stock, sel.color, sel.size);
  if (!entry) return null;
  const available = Math.max(0, entry.available);
  return { skuId: entry.sku_id, available, availability: availabilityOf(available), maxQuantity: Math.min(MAX_PER_LINE, available) };
}

export function clampQuantity(quantity: number, max: number): number {
  if (!Number.isFinite(quantity)) return 1;
  return Math.max(1, Math.min(Math.max(1, max), Math.trunc(quantity)));
}
