import { describe, expect, it } from "vitest";
import type { ProductList } from "../../api/types";
import { GLYPHS, glyphFor, hasGlyph } from "./glyphs";

/** The categories the catalog has today (docs/PLAN, round 2). Fixed list: runs everywhere, even with the API down. */
const KNOWN_SLUGS = ["headphones", "hoodies", "t-shirts", "jackets", "jeans", "shoes", "backpacks", "watches", "sunglasses", "accessories"];

/** The live catalog's own category facet, or null when the API is not running (the live test is then skipped). */
async function liveCategorySlugs(): Promise<string[] | null> {
  try {
    const res = await fetch("http://127.0.0.1:8011/api/products?page_size=1", { signal: AbortSignal.timeout(2000) });
    if (!res.ok) return null;
    const body = (await res.json()) as ProductList;
    return body.facets.find((f) => f.field === "category")?.values.map((v) => v.value) ?? null;
  } catch {
    return null;
  }
}

const live = await liveCategorySlugs();

describe("category glyphs", () => {
  it("has its own glyph for each of the ten known category slugs", () => {
    for (const slug of KNOWN_SLUGS) expect(hasGlyph(slug), slug).toBe(true);
    expect(KNOWN_SLUGS).toHaveLength(10);
  });

  it("draws every glyph from real shapes, and every glyph is different", () => {
    const seen = new Set<string>();
    for (const slug of KNOWN_SLUGS) {
      const g = glyphFor(slug);
      expect(g.body.length, slug).toBeGreaterThan(0);
      seen.add(JSON.stringify(g));
    }
    expect(seen.size).toBe(KNOWN_SLUGS.length);
  });

  it("falls back to the bag only for a category it has never heard of", () => {
    expect(glyphFor("garden-gnomes")).not.toBe(GLYPHS.shoes);
    expect(hasGlyph("garden-gnomes")).toBe(false);
  });

  it.skipIf(live === null)("maps every category slug served by GET /api/products to a non-fallback glyph", () => {
    expect(live?.length).toBeGreaterThan(0);
    for (const slug of live ?? []) expect(hasGlyph(slug), `no glyph for live category "${slug}"`).toBe(true);
  });
});
