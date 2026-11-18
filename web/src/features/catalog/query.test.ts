import { describe, expect, it } from "vitest";
import {
  EMPTY_QUERY, catalogHref, activeFilterCount, clearFilters, normalizeCatalogQuery, parseCatalogQuery, serializeCatalogQuery,
  shopHref, toApiParams, toggleListValue, type CatalogQuery,
} from "./query";

const clearText = (q: CatalogQuery): CatalogQuery => ({ ...q, q: "" });
const FULL: CatalogQuery = normalizeCatalogQuery({
  q: "rain jacket",
  category: ["jackets", "backpacks"],
  brand: ["Sorrel & Co", "Kestrel"],
  color: ["navy", "olive"],
  size: ["M", "L", "XL"],
  minPriceCents: 4000,
  maxPriceCents: 12050,
  sort: "price_asc",
});

describe("catalog query <-> URL", () => {
  it("round-trips a fully populated state", () => {
    expect(parseCatalogQuery(serializeCatalogQuery(FULL))).toEqual(FULL);
  });

  it("round-trips the empty state to an empty string", () => {
    expect(serializeCatalogQuery(EMPTY_QUERY)).toBe("");
    expect(parseCatalogQuery("")).toEqual(EMPTY_QUERY);
  });

  it("round-trips awkward text (ampersand, plus, unicode, quotes)", () => {
    const q = normalizeCatalogQuery({ ...EMPTY_QUERY, q: `rock & roll + "café" 100%`, brand: ["Sorrel & Co"] });
    expect(parseCatalogQuery(serializeCatalogQuery(q))).toEqual(q);
  });

  it("writes one canonical URL: fixed parameter order, sorted lists, no defaults", () => {
    const scrambled = "sort=price_asc&size=XL&max=120.5&size=M&brand=Kestrel&q=rain%20jacket&min=40&color=olive&category=jackets&size=L&color=navy&brand=Sorrel+%26+Co&category=backpacks";
    const once = serializeCatalogQuery(parseCatalogQuery(scrambled));
    expect(once).toBe(serializeCatalogQuery(FULL));
    expect(serializeCatalogQuery(parseCatalogQuery(once))).toBe(once);
    expect(once.startsWith("q=rain+jacket&category=backpacks&category=jackets&brand=Kestrel")).toBe(true);
  });

  it("omits the default sort and keeps others", () => {
    expect(serializeCatalogQuery({ ...EMPTY_QUERY, sort: "relevance" })).toBe("");
    expect(serializeCatalogQuery({ ...EMPTY_QUERY, sort: "rating" })).toBe("sort=rating");
  });

  it("accepts a leading question mark and URLSearchParams", () => {
    expect(parseCatalogQuery("?color=navy")).toEqual({ ...EMPTY_QUERY, color: ["navy"] });
    expect(parseCatalogQuery(new URLSearchParams("color=navy&color=navy"))).toEqual({ ...EMPTY_QUERY, color: ["navy"] });
  });

  it("ignores junk: unknown params, bad sort, bad prices, blank values", () => {
    const q = parseCatalogQuery("utm=1&sort=cheapest&min=abc&max=-5&q=%20%20&brand=&size=%20");
    expect(q).toEqual(EMPTY_QUERY);
  });

  it("swaps an inverted price range instead of returning nothing", () => {
    const q = parseCatalogQuery("min=100&max=20");
    expect([q.minPriceCents, q.maxPriceCents]).toEqual([2000, 10000]);
  });

  it("keeps cents exact: 12.05 -> 1205 -> 12.05, 12.5 <-> 1250, 12 <-> 1200", () => {
    for (const [text, cents] of [["12.05", 1205], ["12.5", 1250], ["12", 1200], ["0.99", 99]] as const) {
      const q = parseCatalogQuery(`min=${text}`);
      expect(q.minPriceCents).toBe(cents);
      expect(serializeCatalogQuery(q)).toBe(`min=${text}`);
    }
    expect(parseCatalogQuery("min=12.50").minPriceCents).toBe(1250);
  });

  it("allows a zero minimum and keeps it distinct from 'no minimum'", () => {
    const q = parseCatalogQuery("min=0");
    expect(q.minPriceCents).toBe(0);
    expect(serializeCatalogQuery(q)).toBe("min=0");
  });
});

describe("catalog query helpers", () => {
  it("toggles list values on and off", () => {
    const on = toggleListValue(EMPTY_QUERY, "color", "navy");
    expect(on.color).toEqual(["navy"]);
    expect(toggleListValue(on, "color", "navy").color).toEqual([]);
  });

  it("counts filters (price range counts once) but not text or sort", () => {
    expect(activeFilterCount(FULL)).toBe(2 + 2 + 2 + 3 + 1);
    expect(activeFilterCount({ ...EMPTY_QUERY, q: "x", sort: "rating" })).toBe(0);
  });

  it("clearFilters keeps the text search and the sort", () => {
    const cleared = clearFilters(FULL);
    expect(cleared).toEqual({ ...EMPTY_QUERY, q: "rain jacket", sort: "price_asc" });
  });

  it("maps to API params in cents with repeatable lists", () => {
    const p = toApiParams(FULL, { pageToken: "24", pageSize: 24 });
    expect(p).toMatchObject({
      q: "rain jacket", category: ["backpacks", "jackets"], min_price_cents: 4000, max_price_cents: 12050,
      sort: "price_asc", page_size: 24, page_token: "24",
    });
    expect(toApiParams(EMPTY_QUERY).category).toBeUndefined();
    expect(toApiParams(EMPTY_QUERY).min_price_cents).toBeUndefined();
  });

  it("builds shop links", () => {
    expect(shopHref({})).toBe("/shop");
    expect(shopHref({ category: ["jackets"] })).toBe("/shop?category=jackets");
    expect(catalogHref(clearText(FULL))).toBe(`/shop?${serializeCatalogQuery({ ...FULL, q: "" })}`);
    expect(catalogHref(clearText(FULL))).not.toContain("q=");
  });
});
