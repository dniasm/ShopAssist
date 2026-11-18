/* The catalog's filter state lives in the URL, and nowhere else. These pure functions are the only place that knows
 * how it is spelled:
 *
 *   /shop?q=rain+jacket&category=jackets&brand=Kestrel&brand=Lowtide&color=navy&size=M&min=40&max=120.5&sort=price_asc
 *
 * `min` / `max` are dollars (up to two decimals) so links read well; the API gets integer cents. Lists are sorted and
 * de-duplicated and the parameter order is fixed, so one state has exactly one URL (Back / forward / shared links agree). */

import { centsToDollarsText, parseDollarsToCents } from "../../api/money";
import type { ListProductsParams, SortKey } from "../../api/types";

export const SORT_KEYS: readonly SortKey[] = ["relevance", "price_asc", "price_desc", "rating", "newest"];
export const SORT_LABELS: Record<SortKey, string> = {
  relevance: "Relevance",
  price_asc: "Price: low to high",
  price_desc: "Price: high to low",
  rating: "Top rated",
  newest: "Newest",
};

export type ListField = "category" | "brand" | "color" | "size";
export const LIST_FIELDS: readonly ListField[] = ["category", "brand", "color", "size"];

export interface CatalogQuery {
  q: string;
  category: string[];
  brand: string[];
  color: string[];
  size: string[];
  minPriceCents: number | null;
  maxPriceCents: number | null;
  sort: SortKey;
}

export const EMPTY_QUERY: CatalogQuery = {
  q: "", category: [], brand: [], color: [], size: [], minPriceCents: null, maxPriceCents: null, sort: "relevance",
};

const MAX_Q_LENGTH = 200;
const MAX_CENTS = 99_999_999;

function cleanList(values: readonly string[]): string[] {
  const set = new Set<string>();
  for (const v of values) {
    const t = v.trim();
    if (t) set.add(t);
  }
  return [...set].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
}

function cleanText(value: string): string {
  return value.replace(/\s+/g, " ").trim().slice(0, MAX_Q_LENGTH).trim();
}

function isSortKey(v: string | null): v is SortKey {
  return v !== null && (SORT_KEYS as readonly string[]).includes(v);
}

function cleanPrice(cents: number | null): number | null {
  return cents !== null && Number.isInteger(cents) && cents >= 0 && cents <= MAX_CENTS ? cents : null;
}

/** Brings any query into its single canonical form (sorted lists, collapsed text, ordered price range). */
export function normalizeCatalogQuery(input: CatalogQuery): CatalogQuery {
  let min = cleanPrice(input.minPriceCents);
  let max = cleanPrice(input.maxPriceCents);
  if (min !== null && max !== null && min > max) [min, max] = [max, min];
  return {
    q: cleanText(input.q),
    category: cleanList(input.category),
    brand: cleanList(input.brand),
    color: cleanList(input.color),
    size: cleanList(input.size),
    minPriceCents: min,
    maxPriceCents: max,
    sort: input.sort,
  };
}

/** Reads a location search string ("?a=b" or "a=b") or URLSearchParams. Unknown / malformed parameters are ignored. */
export function parseCatalogQuery(input: string | URLSearchParams): CatalogQuery {
  const params = typeof input === "string" ? new URLSearchParams(input) : input;
  const sort = params.get("sort");
  const min = params.get("min");
  const max = params.get("max");
  return normalizeCatalogQuery({
    q: params.get("q") ?? "",
    category: params.getAll("category"),
    brand: params.getAll("brand"),
    color: params.getAll("color"),
    size: params.getAll("size"),
    minPriceCents: min === null ? null : parseDollarsToCents(min),
    maxPriceCents: max === null ? null : parseDollarsToCents(max),
    sort: isSortKey(sort) ? sort : "relevance",
  });
}

/** The canonical search string without the leading "?" ("" when nothing is set). */
export function serializeCatalogQuery(input: CatalogQuery): string {
  const q = normalizeCatalogQuery(input);
  const params = new URLSearchParams();
  if (q.q) params.set("q", q.q);
  for (const field of LIST_FIELDS) for (const v of q[field]) params.append(field, v);
  if (q.minPriceCents !== null) params.set("min", centsToDollarsText(q.minPriceCents));
  if (q.maxPriceCents !== null) params.set("max", centsToDollarsText(q.maxPriceCents));
  if (q.sort !== "relevance") params.set("sort", q.sort);
  return params.toString();
}

/** Stable identity of a query, used as a React effect key. */
export function queryKey(q: CatalogQuery): string {
  return serializeCatalogQuery(q);
}

export function toApiParams(q: CatalogQuery, extra: { pageToken?: string; pageSize?: number } = {}): ListProductsParams {
  const n = normalizeCatalogQuery(q);
  return {
    q: n.q || undefined,
    category: n.category.length ? n.category : undefined,
    brand: n.brand.length ? n.brand : undefined,
    color: n.color.length ? n.color : undefined,
    size: n.size.length ? n.size : undefined,
    min_price_cents: n.minPriceCents ?? undefined,
    max_price_cents: n.maxPriceCents ?? undefined,
    sort: n.sort,
    page_size: extra.pageSize,
    page_token: extra.pageToken || undefined,
  };
}

/** Filters only (not the text search or the sort). Used for the "Filters (3)" badge and "Clear filters". */
export function activeFilterCount(q: CatalogQuery): number {
  return q.category.length + q.brand.length + q.color.length + q.size.length
    + (q.minPriceCents !== null || q.maxPriceCents !== null ? 1 : 0);
}

export function hasAnyCriteria(q: CatalogQuery): boolean {
  return q.q !== "" || activeFilterCount(q) > 0;
}

export function toggleListValue(q: CatalogQuery, field: ListField, value: string): CatalogQuery {
  const has = q[field].includes(value);
  return normalizeCatalogQuery({ ...q, [field]: has ? q[field].filter((v) => v !== value) : [...q[field], value] });
}

export function clearFilters(q: CatalogQuery): CatalogQuery {
  return { ...EMPTY_QUERY, q: q.q, sort: q.sort };
}

export function withText(q: CatalogQuery, text: string): CatalogQuery {
  return normalizeCatalogQuery({ ...q, q: text });
}

export function withPrice(q: CatalogQuery, minCents: number | null, maxCents: number | null): CatalogQuery {
  return normalizeCatalogQuery({ ...q, minPriceCents: minCents, maxPriceCents: maxCents });
}

export function withSort(q: CatalogQuery, sort: SortKey): CatalogQuery {
  return { ...q, sort };
}

/** The /shop link for exactly this query. */
export function catalogHref(q: CatalogQuery): string {
  const s = serializeCatalogQuery(q);
  return s ? `/shop?${s}` : "/shop";
}

/** The href a "Shop by ..." link should use: the given criteria on top of an empty query. */
export function shopHref(partial: Partial<CatalogQuery>): string {
  return catalogHref({ ...EMPTY_QUERY, ...partial });
}
