import { describe, expect, it } from "vitest";
import type { Facet } from "../../api/types";
import { facetOptions } from "./facets";

const facets: Facet[] = [
  { field: "category", values: [{ value: "t-shirts", count: 5 }, { value: "jackets", count: 9 }] },
  { field: "color", values: [{ value: "navy", count: 4 }, { value: "red", count: 12 }, { value: "olive", count: 4 }] },
  { field: "size", values: [{ value: "XL", count: 2 }, { value: "S", count: 3 }, { value: "M", count: 8 }] },
];

describe("facetOptions", () => {
  it("passes the API's counts through and labels the values", () => {
    expect(facetOptions(facets, "category", []).map((o) => [o.label, o.count])).toEqual([["Jackets", 9], ["T-shirts", 5]]);
  });
  it("orders every group by count (then name), the same rule for brands, colours and categories", () => {
    const brands: Facet[] = [{ field: "brand", values: [{ value: "Zed", count: 3 }, { value: "Alpha", count: 3 }, { value: "Mid", count: 9 }, { value: "Low", count: 1 }] }];
    expect(facetOptions(brands, "brand", []).map((o) => o.value)).toEqual(["Mid", "Alpha", "Zed", "Low"]);
  });
  it("orders colours by count, then name; sizes in shopper order", () => {
    expect(facetOptions(facets, "color", []).map((o) => o.value)).toEqual(["red", "navy", "olive"]);
    expect(facetOptions(facets, "size", []).map((o) => o.value)).toEqual(["S", "M", "XL"]);
  });
  it("keeps a selected value that the API stopped listing, with a zero count", () => {
    const options = facetOptions(facets, "color", ["teal"]);
    expect(options.find((o) => o.value === "teal")).toEqual({ value: "teal", label: "Teal", count: 0, selected: true });
  });
  it("drops unselected zero-count values and tolerates a missing facet", () => {
    const withZero: Facet[] = [{ field: "brand", values: [{ value: "Kestrel", count: 0 }, { value: "Lowtide", count: 2 }] }];
    expect(facetOptions(withZero, "brand", []).map((o) => o.value)).toEqual(["Lowtide"]);
    expect(facetOptions([], "brand", [])).toEqual([]);
  });
  it("marks selected options", () => {
    expect(facetOptions(facets, "size", ["M"]).filter((o) => o.selected).map((o) => o.value)).toEqual(["M"]);
  });
});
