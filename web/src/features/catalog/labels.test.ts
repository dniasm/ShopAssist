import { describe, expect, it } from "vitest";
import { pluralize, slugLabel, sortSizes } from "./labels";

describe("labels", () => {
  it("orders sizes the way a shopper expects", () => {
    expect(sortSizes(["XL", "10", "S", "6", "XXL", "M", "ONE", "L", "XS", "12", "7"]))
      .toEqual(["XS", "S", "M", "L", "XL", "XXL", "ONE", "6", "7", "10", "12"]);
  });
  it("does not mutate the input", () => {
    const input = ["M", "S"];
    sortSizes(input);
    expect(input).toEqual(["M", "S"]);
  });
  it("makes slugs readable", () => {
    expect(slugLabel("home_goods")).toBe("Home goods");
    expect(slugLabel("t-shirts")).toBe("T-shirts");
    expect(slugLabel("jackets")).toBe("Jackets");
  });
  it("pluralizes with separators", () => {
    expect(pluralize(1, "product")).toBe("1 product");
    expect(pluralize(1234, "product")).toBe("1,234 products");
    expect(pluralize(0, "product")).toBe("0 products");
  });
});
