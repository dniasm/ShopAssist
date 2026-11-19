import { describe, expect, it } from "vitest";
import { chatContextFor } from "./context";

describe("chatContextFor", () => {
  it("sends the product of a product page", () => {
    expect(chatContextFor("/p/P-1042")).toEqual({ path: "/p/P-1042", product_id: "P-1042" });
    expect(chatContextFor("/p/P%2D7")).toEqual({ path: "/p/P%2D7", product_id: "P-7" });
  });
  it("sends only the path anywhere else", () => {
    for (const path of ["/", "/shop", "/orders/O-5", "/checkout", "/pricing/p/x"]) expect(chatContextFor(path)).toEqual({ path });
  });
  it("is a pure function of the path: navigating changes the answer", () => {
    expect(chatContextFor("/p/A").product_id).toBe("A");
    expect(chatContextFor("/p/B").product_id).toBe("B");
    expect(chatContextFor("/shop").product_id).toBeUndefined();
  });
  it("does not throw on a malformed escape", () => {
    expect(chatContextFor("/p/%E0%A4%A")).toEqual({ path: "/p/%E0%A4%A", product_id: "%E0%A4%A" });
  });
});
