import { formatMoney } from "../../api/money";
import type { PaymentMethod } from "../../api/types";
import { PAYMENT_OPTIONS } from "../checkout/PaymentMethods";

/** Tool names from the agent contract, as the line the shopper reads in the route trace. `running` is while it is happening. */
const TOOL_LABELS: Record<string, { done: string; running: string }> = {
  search_products: { done: "Searched products", running: "Searching products" },
  get_product: { done: "Looked up a product", running: "Looking up a product" },
  get_stock: { done: "Checked stock", running: "Checking stock" },
  get_cart: { done: "Read your cart", running: "Reading your cart" },
  add_to_cart: { done: "Added to your cart", running: "Adding to your cart" },
  remove_from_cart: { done: "Removed from your cart", running: "Removing from your cart" },
  list_orders: { done: "Listed your orders", running: "Listing your orders" },
  get_order: { done: "Looked up an order", running: "Looking up an order" },
  propose_checkout: { done: "Prepared a checkout", running: "Preparing a checkout" },
  propose_cancel_order: { done: "Prepared a cancellation", running: "Preparing a cancellation" },
};

export function toolLabel(tool: string, running: boolean): string {
  const entry = TOOL_LABELS[tool];
  if (entry) return running ? entry.running : entry.done;
  const words = tool.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const SORT_WORDS: Record<string, string> = { price_asc: "cheapest first", price_desc: "dearest first", rating: "best rated", newest: "newest", relevance: "best match" };

function paymentLabel(value: string): string {
  return PAYMENT_OPTIONS.find((o) => o.value === (value as PaymentMethod))?.label ?? value.replace(/_/g, " ");
}

/** The arguments of a tool call as a short phrase: `“hoodie” · navy · under $50`. Unknown keys are left out, never dumped as JSON. */
export function argsSummary(args: Record<string, unknown> | null): string {
  if (!args) return "";
  const parts: string[] = [];
  const text = (k: string) => (typeof args[k] === "string" && args[k] !== "" ? String(args[k]) : null);
  const cents = (k: string) => (typeof args[k] === "number" ? Number(args[k]) : null);
  if (text("query")) parts.push(`\u201C${text("query")}\u201D`);
  for (const k of ["category", "brand", "color"]) if (text(k)) parts.push(String(text(k)));
  if (text("size")) parts.push(`size ${text("size")}`);
  const lo = cents("min_price_cents"), hi = cents("max_price_cents");
  if (lo !== null && hi !== null) parts.push(`${formatMoney(lo)} to ${formatMoney(hi)}`);
  else if (hi !== null) parts.push(`under ${formatMoney(hi)}`);
  else if (lo !== null) parts.push(`over ${formatMoney(lo)}`);
  if (text("sort")) parts.push(SORT_WORDS[String(text("sort"))] ?? String(text("sort")));
  if (text("product_id")) parts.push(String(text("product_id")));
  if (text("order_id")) parts.push(String(text("order_id")));
  if (typeof args.quantity === "number" && args.quantity !== 1) parts.push(`\u00D7${args.quantity}`);
  if (text("payment_method")) parts.push(paymentLabel(String(text("payment_method"))));
  return parts.join(" \u00B7 ");
}

/** What a proposal is about, one line each, for the confirmation card. */
export function proposalDetails(action: string, args: Record<string, unknown>): string[] {
  if (action === "propose_checkout") {
    return [typeof args.payment_method === "string" ? `Pay with: ${paymentLabel(args.payment_method)}` : "Pay with: the default demo card"];
  }
  if (action === "propose_cancel_order" && typeof args.order_id === "string") return [`Order ${args.order_id}`];
  return [];
}

/** The button label for confirming a proposal, in the action's own words. */
export function confirmLabel(action: string): string {
  if (action === "propose_checkout") return "Place order";
  if (action === "propose_cancel_order") return "Cancel order";
  return "Confirm";
}
