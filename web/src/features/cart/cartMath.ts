/* What the cart shows, derived from the server's cart. Money comes from the server (subtotal / shipping / total are its
 * numbers, not recomputed here); these functions decide the wording, the free-shipping hint and what blocks checkout. */

import { formatMoney } from "../../api/money";
import type { Cart, CartLine } from "../../api/types";
import { MAX_PER_LINE } from "../product/variants";

/** PLAN: shipping is free when the subtotal reaches $50.00, otherwise $4.99. */
export const FREE_SHIPPING_THRESHOLD_CENTS = 5000;

export type LineIssue = { kind: "sold_out" } | { kind: "short"; available: number };

/** A line that cannot be bought as it stands: nothing left, or fewer left than the quantity in the cart. */
export function lineIssue(line: Pick<CartLine, "quantity" | "available">): LineIssue | null {
  if (line.available <= 0) return { kind: "sold_out" };
  if (line.available < line.quantity) return { kind: "short", available: line.available };
  return null;
}

export function issueText(issue: LineIssue, quantity: number): string {
  return issue.kind === "sold_out"
    ? "Sold out. Remove it to check out."
    : `Only ${issue.available} left, but you have ${quantity} in your cart. Lower the quantity to check out.`;
}

/** The most the stepper may go to: what is available, and never above the per-line cap. */
export function lineMax(line: Pick<CartLine, "available">): number {
  return Math.min(MAX_PER_LINE, Math.max(0, line.available));
}

export function itemCount(cart: Pick<Cart, "lines"> | null): number {
  return cart ? cart.lines.reduce((sum, l) => sum + l.quantity, 0) : 0;
}

export function freeShippingGap(subtotalCents: number): number {
  return Math.max(0, FREE_SHIPPING_THRESHOLD_CENTS - subtotalCents);
}

export interface CartView {
  count: number;
  lineCount: number;
  subtotal: string;
  shipping: string;
  total: string;
  /** "Add $12.01 for free shipping", or null when it already qualifies (or the cart is empty). */
  freeShippingHint: string | null;
  issues: { skuId: string; issue: LineIssue }[];
  canCheckout: boolean;
}

export function cartView(cart: Cart | null): CartView {
  const lines = cart?.lines ?? [];
  const count = itemCount(cart);
  const issues = lines.flatMap((l) => {
    const issue = lineIssue(l);
    return issue ? [{ skuId: l.sku_id, issue }] : [];
  });
  const subtotalCents = cart?.subtotal_cents ?? 0;
  const gap = freeShippingGap(subtotalCents);
  return {
    count,
    lineCount: lines.length,
    subtotal: formatMoney(subtotalCents),
    shipping: cart && cart.shipping_cents === 0 ? "Free" : formatMoney(cart?.shipping_cents ?? 0),
    total: formatMoney(cart?.total_cents ?? 0),
    freeShippingHint: count > 0 && gap > 0 ? `Add ${formatMoney(gap)} for free shipping` : null,
    issues,
    canCheckout: count > 0 && issues.length === 0,
  };
}
