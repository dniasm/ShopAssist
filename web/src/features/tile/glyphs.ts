/* Category glyphs for the product tile, drawn on a 120 x 120 grid.
 *   body   - filled with the product's colour
 *   thick  - heavy strokes in the outline colour (a headphone band, a strap)
 *   detail - thin strokes in the outline colour (laces, zips, seams)
 * Unknown categories fall back to the shop bag, so a new category never renders as a broken image. */

export interface Glyph { body: string[]; thick?: string[]; detail?: string[] }

const BAG: Glyph = {
  body: ["M26 44h68l-6 58a7 7 0 0 1-7 6H39a7 7 0 0 1-7-6z"],
  thick: ["M46 44v-6a14 14 0 0 1 28 0v6"],
  detail: ["M48 70h24"],
};

export const GLYPHS: Record<string, Glyph> = {
  headphones: {
    body: ["M18 66h22a4 4 0 0 1 4 4v22a8 8 0 0 1-8 8H26a8 8 0 0 1-8-8z", "M80 66h22v26a8 8 0 0 1-8 8h-10a8 8 0 0 1-8-8V70a4 4 0 0 1 4-4z"],
    thick: ["M24 66C24 22 96 22 96 66"],
    detail: ["M31 74v18", "M89 74v18"],
  },
  shoes: {
    body: ["M14 74V58c14-2 26-10 32-22l14 6c8 8 22 14 40 18 8 2 12 8 12 14v6H14z"],
    thick: ["M12 82h98"],
    detail: ["M48 46l6 8", "M58 50l6 8", "M68 54l6 8", "M14 74h98"],
  },
  jackets: {
    body: ["M44 22L26 30 10 82l16 6 8-22v36h52V66l8 22 16-6L94 30 76 22c-4 10-28 10-32 0z"],
    detail: ["M60 32v70", "M34 66v36", "M86 66v36", "M40 84h12", "M68 84h12"],
  },
  "t-shirts": {
    body: ["M44 20L16 36l12 22 10-6v48h44V52l10 6 12-22-28-16c-4 10-28 10-32 0z"],
    detail: ["M46 24c4 8 24 8 28 0"],
  },
  hoodies: {
    body: [
      "M44 28c0-16 7-24 16-24s16 8 16 24c0 9-7 15-16 15s-16-6-16-15z",
      "M46 36L26 44 12 90l16 5 8-24v33h48V71l8 24 16-5-14-46-20-8c-3 6-9 10-14 10s-11-4-14-10z",
    ],
    detail: ["M48 30c2 8 8 13 12 13s10-5 12-13", "M54 48v14", "M66 48v14", "M42 82h36l-4 18H46z", "M36 71v32", "M84 71v32"],
  },
  jeans: {
    body: ["M34 16h52l8 92H68L60 54l-8 54H26z"],
    detail: ["M34 28h52", "M60 28v26", "M42 34h8", "M70 34h8"],
  },
  backpacks: {
    body: ["M34 34h52a8 8 0 0 1 8 8v54a10 10 0 0 1-10 10H36a10 10 0 0 1-10-10V42a8 8 0 0 1 8-8z"],
    thick: ["M48 34v-6a12 12 0 0 1 24 0v6"],
    detail: ["M36 74h48v20a6 6 0 0 1-6 6H42a6 6 0 0 1-6-6z", "M36 58h48", "M60 74v8"],
  },
  watches: {
    body: ["M44 8h32l4 28H40z", "M40 84h40l-4 28H44z", "M60 32a28 28 0 1 1 0 56 28 28 0 0 1 0-56z"],
    thick: ["M60 32a28 28 0 1 1 0 56 28 28 0 0 1 0-56z"],
    detail: ["M60 60V46", "M60 60l10 6", "M60 36v4", "M60 80v4", "M36 60h4", "M80 60h4"],
  },
  speakers: {
    body: ["M38 12h44a10 10 0 0 1 10 10v76a10 10 0 0 1-10 10H38a10 10 0 0 1-10-10V22a10 10 0 0 1 10-10z"],
    thick: ["M60 56a22 22 0 1 1 0 44 22 22 0 0 1 0-44z"],
    detail: ["M60 62a16 16 0 1 1 0 32 16 16 0 0 1 0-32z", "M60 24a6 6 0 1 1 0 12 6 6 0 0 1 0-12z"],
  },
  sunglasses: {
    body: ["M12 46h44v10a18 18 0 0 1-18 18h-8a18 18 0 0 1-18-18z", "M64 46h44v10a18 18 0 0 1-18 18h-8a18 18 0 0 1-18-18z"],
    thick: ["M56 50c3-4 5-4 8 0", "M12 46L4 40", "M108 46l8-6"],
    detail: [],
  },
  accessories: {
    body: ["M24 78c0-26 16-42 38-42s38 16 38 42z", "M18 78h72c10 0 14 4 14 10H18z"],
    thick: ["M62 36v-6"],
    detail: ["M62 40v38", "M44 46c-6 8-8 18-8 32"],
  },
};

export function hasGlyph(category: string): boolean {
  return Object.hasOwn(GLYPHS, category);
}

export function glyphFor(category: string): Glyph {
  return hasGlyph(category) ? GLYPHS[category] ?? BAG : BAG;
}

/** Overlay patterns, picked per product from a hash so two products of one category and colour still differ. */
export type PatternKind = "plain" | "band" | "stripes" | "dots";
export const PATTERNS: readonly PatternKind[] = ["plain", "band", "stripes", "dots", "plain", "band"];
