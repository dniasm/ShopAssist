import { ApiError } from "../../api/errors";
import { formatMoney } from "../../api/money";
import type { Order, OrderStatus } from "../../api/types";

export type PillTone = "neutral" | "pos" | "neg" | "warn";

/** Words and colour for a status pill. Colour only says state: green once delivered, red when it did not go through, amber
 *  while it is still being confirmed or after a cancellation; everything in between is plain. */
export function statusPill(status: OrderStatus): { label: string; tone: PillTone } {
  switch (status) {
    case "PENDING": return { label: "Confirming", tone: "warn" };
    case "CONFIRMED": return { label: "Confirmed", tone: "neutral" };
    case "PACKED": return { label: "Packed", tone: "neutral" };
    case "SHIPPED": return { label: "Shipped", tone: "neutral" };
    case "OUT_FOR_DELIVERY": return { label: "Out for delivery", tone: "neutral" };
    case "DELIVERED": return { label: "Delivered", tone: "pos" };
    case "CANCELLED": return { label: "Cancelled", tone: "warn" };
    case "FAILED": return { label: "Not completed", tone: "neg" };
  }
}

/** PLAN: a user can cancel only while the order is CONFIRMED or PACKED. */
export function canCancel(status: OrderStatus): boolean {
  return status === "CONFIRMED" || status === "PACKED";
}

export function refundText(order: Pick<Order, "total_cents">): string {
  return `${formatMoney(order.total_cents)} will be refunded to your card, and the items go back to stock.`;
}

export const TOO_LATE_MESSAGE = "This order has already shipped and can no longer be cancelled.";

/** What to tell the shopper when a cancel did not work. A 409 is the race: it moved on while they were deciding. */
export function describeCancelError(err: unknown): { message: string; refetch: boolean } {
  if (err instanceof ApiError && (err.code === "conflict" || err.status === 409)) return { message: TOO_LATE_MESSAGE, refetch: true };
  if (err instanceof ApiError && err.isNotFound) return { message: "We can't find this order any more.", refetch: true };
  if (err instanceof ApiError && err.isNetwork) return { message: "Can't reach the shop. Nothing was cancelled; try again.", refetch: false };
  return { message: err instanceof ApiError ? err.message : "Something went wrong. Nothing was cancelled.", refetch: false };
}

export function itemCount(order: Pick<Order, "lines">): number {
  return order.lines.reduce((sum, l) => sum + l.quantity, 0);
}
