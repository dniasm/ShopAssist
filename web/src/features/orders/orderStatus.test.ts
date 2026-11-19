import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/errors";
import type { OrderStatus } from "../../api/types";
import { TOO_LATE_MESSAGE, canCancel, describeCancelError, itemCount, refundText, statusPill } from "./orderStatus";

const ALL: OrderStatus[] = ["PENDING", "CONFIRMED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED", "CANCELLED", "FAILED"];

describe("statusPill", () => {
  it("has words for every status, and colour only where it means something", () => {
    for (const s of ALL) expect(statusPill(s).label.length).toBeGreaterThan(0);
    expect(statusPill("DELIVERED")).toEqual({ label: "Delivered", tone: "pos" });
    expect(statusPill("FAILED").tone).toBe("neg");
    expect(statusPill("PENDING").tone).toBe("warn");
    expect(statusPill("CANCELLED").tone).toBe("warn");
    for (const s of ["CONFIRMED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY"] as const) expect(statusPill(s).tone).toBe("neutral");
  });
});

describe("cancelling", () => {
  it("is offered only while CONFIRMED or PACKED", () => {
    expect(ALL.filter(canCancel)).toEqual(["CONFIRMED", "PACKED"]);
  });
  it("states the refund amount and that the items go back to stock", () => {
    expect(refundText({ total_cents: 14398 })).toBe("$143.98 will be refunded to your card, and the items go back to stock.");
  });
  it("turns a 409 into the 'already shipped' message and asks for a refetch", () => {
    expect(describeCancelError(new ApiError(409, "conflict", "order is SHIPPED"))).toEqual({ message: TOO_LATE_MESSAGE, refetch: true });
    expect(TOO_LATE_MESSAGE).toBe("This order has already shipped and can no longer be cancelled.");
  });
  it("other failures say nothing was cancelled", () => {
    expect(describeCancelError(new ApiError(0, "network", "x")).message).toMatch(/Nothing was cancelled/);
    expect(describeCancelError(new ApiError(0, "network", "x")).refetch).toBe(false);
    expect(describeCancelError(new ApiError(404, "not_found", "x")).refetch).toBe(true);
    expect(describeCancelError("weird").message).toMatch(/Nothing was cancelled/);
  });
});

describe("itemCount", () => {
  it("counts items, not lines", () => {
    const line = { sku_id: "S", product_id: "P", name: "n", category: "jackets", size: "M", color: "c", unit_price_cents: 1, line_total_cents: 1 };
    expect(itemCount({ lines: [{ ...line, quantity: 2 }, { ...line, quantity: 3 }] })).toBe(5);
  });
});
