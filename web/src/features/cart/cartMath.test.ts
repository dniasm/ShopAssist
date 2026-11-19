import { describe, expect, it } from "vitest";
import type { Cart, CartLine } from "../../api/types";
import { cartView, freeShippingGap, issueText, itemCount, lineIssue, lineMax } from "./cartMath";

const line = (over: Partial<CartLine> = {}): CartLine => ({
  sku_id: "SKU-1", product_id: "P-1", name: "Rain Jacket", category: "jackets", size: "M", color: "navy", quantity: 1,
  unit_price_cents: 2000, line_total_cents: 2000, available: 20, ...over,
});
const cart = (lines: CartLine[], shipping = 499): Cart => {
  const subtotal = lines.reduce((s, l) => s + l.line_total_cents, 0);
  return { lines, subtotal_cents: subtotal, shipping_cents: shipping, total_cents: subtotal + shipping, version: 3 };
};

describe("line issues", () => {
  it("flags sold out and short lines, nothing else", () => {
    expect(lineIssue({ quantity: 2, available: 0 })).toEqual({ kind: "sold_out" });
    expect(lineIssue({ quantity: 3, available: 2 })).toEqual({ kind: "short", available: 2 });
    expect(lineIssue({ quantity: 2, available: 2 })).toBeNull();
    expect(lineIssue({ quantity: 1, available: 40 })).toBeNull();
  });
  it("words them so the shopper knows what to do", () => {
    expect(issueText({ kind: "sold_out" }, 1)).toBe("Sold out. Remove it to check out.");
    expect(issueText({ kind: "short", available: 2 }, 3)).toBe("Only 2 left, but you have 3 in your cart. Lower the quantity to check out.");
  });
  it("caps the stepper by availability and by 10", () => {
    expect(lineMax({ available: 3 })).toBe(3);
    expect(lineMax({ available: 99 })).toBe(10);
    expect(lineMax({ available: 0 })).toBe(0);
    expect(lineMax({ available: -4 })).toBe(0);
  });
});

describe("free shipping", () => {
  it("counts down to $50.00 and stops at zero", () => {
    expect(freeShippingGap(0)).toBe(5000);
    expect(freeShippingGap(3799)).toBe(1201);
    expect(freeShippingGap(5000)).toBe(0);
    expect(freeShippingGap(12000)).toBe(0);
  });
});

describe("cartView", () => {
  it("an empty or missing cart shows zeros, no hint and cannot check out", () => {
    for (const c of [null, cart([], 0)]) {
      const v = cartView(c);
      expect([v.count, v.freeShippingHint, v.canCheckout, v.subtotal, v.total]).toEqual([0, null, false, "$0.00", "$0.00"]);
    }
  });
  it("under the threshold: shipping is charged and the hint says how much more", () => {
    const v = cartView(cart([line({ quantity: 2, line_total_cents: 3799 })]));
    expect(v).toMatchObject({ count: 2, subtotal: "$37.99", shipping: "$4.99", total: "$42.98", freeShippingHint: "Add $12.01 for free shipping", canCheckout: true });
  });
  it("at or over the threshold: shipping is Free and there is no hint", () => {
    const v = cartView(cart([line({ line_total_cents: 5000 })], 0));
    expect(v).toMatchObject({ shipping: "Free", freeShippingHint: null, total: "$50.00" });
  });
  it("blocks checkout while any line is sold out or short, and says which", () => {
    const v = cartView(cart([line(), line({ sku_id: "SKU-2", available: 0 }), line({ sku_id: "SKU-3", quantity: 4, available: 2 })]));
    expect(v.canCheckout).toBe(false);
    expect(v.issues).toEqual([{ skuId: "SKU-2", issue: { kind: "sold_out" } }, { skuId: "SKU-3", issue: { kind: "short", available: 2 } }]);
  });
  it("counts items, not lines", () => {
    expect(itemCount(cart([line({ quantity: 2 }), line({ sku_id: "S2", quantity: 3 })]))).toBe(5);
    expect(itemCount(null)).toBe(0);
  });
});
