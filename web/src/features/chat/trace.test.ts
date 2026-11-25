import { describe, expect, it } from "vitest";
import { argsSummary, confirmLabel, dismissLabel, proposalDetails, toolLabel } from "./trace";

describe("route trace wording", () => {
  it("names each tool in the past and in the present tense", () => {
    expect(toolLabel("search_products", false)).toBe("Searched products");
    expect(toolLabel("search_products", true)).toBe("Searching products");
    expect(toolLabel("propose_cancel_order", false)).toBe("Prepared a cancellation");
  });
  it("makes a readable line out of a tool name it has never heard of", () => {
    expect(toolLabel("compare_prices", false)).toBe("Compare prices");
  });
  it("summarises arguments as a short phrase, in cents formatted as money", () => {
    expect(argsSummary({ query: "hoodie", color: "navy", max_price_cents: 5000, sort: "price_asc" })).toBe("\u201Choodie\u201D \u00B7 navy \u00B7 under $50.00 \u00B7 cheapest first");
    expect(argsSummary({ category: "shoes", size: "9", min_price_cents: 3000, max_price_cents: 9000 })).toBe("shoes \u00B7 size 9 \u00B7 $30.00 to $90.00");
    expect(argsSummary({ product_id: "P-1", quantity: 2 })).toBe("P-1 \u00B7 \u00D72");
    expect(argsSummary({ payment_method: "card_declined" })).toBe("Card that is declined");
  });
  it("leaves out what it does not know and handles no arguments", () => {
    expect(argsSummary({ mystery: { deep: 1 }, query: "" })).toBe("");
    expect(argsSummary(null)).toBe("");
  });
});

describe("proposals", () => {
  it("says what will be paid with or cancelled, and labels the confirm button for the action", () => {
    expect(proposalDetails("checkout_redirect", {})).toEqual([]);
    expect(proposalDetails("cancel_order", { order_id: "O-5" })).toEqual(["Order O-5"]);
    expect(proposalDetails("something_else", {})).toEqual([]);
    expect([confirmLabel("checkout_redirect"), confirmLabel("cancel_order"), confirmLabel("other")]).toEqual(["Yes, check out", "Cancel order", "Confirm"]);
    expect([dismissLabel("checkout_redirect"), dismissLabel("cancel_order")]).toEqual(["Not now", "Dismiss"]);
  });
});
