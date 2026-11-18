import { describe, expect, it } from "vitest";
import type { StockEntry } from "../../api/types";
import {
  availabilityOf, availabilityText, clampQuantity, colorOptions, initialSelection, resolveSelection, sizeOptions, validSize,
} from "./variants";

const product = { colors: ["navy", "red"], sizes: ["S", "M", "L"] };
const stock: StockEntry[] = [
  { sku_id: "SKU-1", color: "navy", size: "S", available: 0 },
  { sku_id: "SKU-2", color: "navy", size: "M", available: 3 },
  { sku_id: "SKU-3", color: "navy", size: "L", available: 40 },
  { sku_id: "SKU-4", color: "red", size: "S", available: 0 },
  { sku_id: "SKU-5", color: "red", size: "M", available: 0 },
  // red / L does not exist at all
];

describe("availability", () => {
  it("classifies stock levels", () => {
    expect(availabilityOf(0)).toBe("sold_out");
    expect(availabilityOf(1)).toBe("low");
    expect(availabilityOf(4)).toBe("low");
    expect(availabilityOf(5)).toBe("in_stock");
  });
  it("words them", () => {
    expect(availabilityText("low", 3)).toBe("Only 3 left");
    expect(availabilityText("sold_out", 0)).toBe("Sold out");
    expect(availabilityText("in_stock", 40)).toBe("In stock");
  });
});

describe("variant options", () => {
  it("marks a colour sold out when every size is", () => {
    const options = colorOptions(product, stock);
    expect(options.map((o) => [o.color, o.availability])).toEqual([["navy", "in_stock"], ["red", "sold_out"]]);
  });
  it("lists sizes in product order with the SKU each resolves to", () => {
    const sizes = sizeOptions(product, stock, "navy");
    expect(sizes.map((s) => [s.size, s.skuId, s.availability])).toEqual([
      ["S", "SKU-1", "sold_out"], ["M", "SKU-2", "low"], ["L", "SKU-3", "in_stock"],
    ]);
  });
  it("flags a size that the colour does not come in", () => {
    expect(sizeOptions(product, stock, "red").find((s) => s.size === "L")).toMatchObject({ skuId: null, availability: "not_offered" });
  });
});

describe("selection", () => {
  it("starts on the first colour that has stock, with no size picked", () => {
    expect(initialSelection(product, stock)).toEqual({ color: "navy", size: null });
    const redFirst = { colors: ["red", "navy"], sizes: product.sizes };
    expect(initialSelection(redFirst, stock)).toEqual({ color: "navy", size: null });
  });
  it("honours a wanted colour and size only when they can be bought", () => {
    expect(initialSelection(product, stock, { color: "navy", size: "L" })).toEqual({ color: "navy", size: "L" });
    expect(initialSelection(product, stock, { color: "navy", size: "S" })).toEqual({ color: "navy", size: null });
    expect(initialSelection(product, stock, { color: "nope", size: "M" })).toEqual({ color: "navy", size: "M" });
  });
  it("drops a size that is not sold in the newly chosen colour", () => {
    expect(validSize(product, stock, "red", "M")).toBeNull();
    expect(validSize(product, stock, "navy", "M")).toBe("M");
  });
  it("selects the only size of a one-size product", () => {
    const one = [{ sku_id: "SKU-9", color: "black", size: "ONE", available: 12 }];
    expect(initialSelection({ colors: ["black"], sizes: ["ONE"] }, one)).toEqual({ color: "black", size: "ONE" });
  });
  it("resolves colour + size to a sku_id and the most you can add", () => {
    expect(resolveSelection(stock, { color: "navy", size: "M" })).toEqual({ skuId: "SKU-2", availability: "low", available: 3, maxQuantity: 3 });
    expect(resolveSelection(stock, { color: "navy", size: "L" })?.maxQuantity).toBe(10);
    expect(resolveSelection(stock, { color: "navy", size: null })).toBeNull();
    expect(resolveSelection(stock, { color: "red", size: "L" })).toBeNull();
  });
  it("clamps quantity between 1 and the maximum", () => {
    expect(clampQuantity(0, 5)).toBe(1);
    expect(clampQuantity(9, 5)).toBe(5);
    expect(clampQuantity(2.7, 5)).toBe(2);
    expect(clampQuantity(Number.NaN, 5)).toBe(1);
  });
});
