/* The assistant's cards arrive as `{kind, data}` with data of unknown shape (the contract only names the kinds). Each kind is checked
 * here and turned into a small view model the UI draws; anything that does not fit is dropped rather than half-rendered.
 * Tolerant on purpose about wrappers: `data` may be the thing itself, or `{products: [...]}`, `{order: {...}}` and so on. */

import type { ChatCard } from "../../api/types";

type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const num = (v: unknown, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

export interface ProductCardView {
  productId: string;
  name: string;
  category: string;
  brand: string;
  priceCents: number;
  rating: number | null;
  ratingCount: number;
  color: string | null;
  inStock: boolean | null;
}
export interface StockSize { size: string; available: number; skuId: string | null }
export interface StockView { productId: string | null; colors: { color: string; sizes: StockSize[] }[] }
export interface CartLineView { skuId: string; productId: string; name: string; category: string; size: string; color: string; quantity: number; lineTotalCents: number }
export interface CartCardView { lines: CartLineView[]; count: number; totalCents: number }
export interface OrderCardView { orderId: string; status: string; totalCents: number; itemCount: number; createdAt: string }

export type CardView =
  | { kind: "products"; items: ProductCardView[] }
  | { kind: "product"; item: ProductCardView; description: string }
  | { kind: "stock"; stock: StockView }
  | { kind: "cart"; cart: CartCardView }
  | { kind: "order"; order: OrderCardView }
  | { kind: "orders"; orders: OrderCardView[] }
  | { kind: "signin" };

/** `data` itself if it is a list, else the list under one of `keys`. */
function listOf(data: unknown, ...keys: string[]): unknown[] {
  if (Array.isArray(data)) return data;
  if (isRec(data)) for (const k of keys) if (Array.isArray(data[k])) return data[k];
  return [];
}
/** `data` itself if it is an object, else the object under `key`. */
function objectOf(data: unknown, key: string): Rec | null {
  if (isRec(data) && isRec(data[key])) return data[key];
  return isRec(data) ? data : null;
}

function productView(v: unknown): ProductCardView | null {
  if (!isRec(v) || typeof v.product_id !== "string" || typeof v.name !== "string") return null;
  const colors = Array.isArray(v.colors) ? v.colors.filter((c): c is string => typeof c === "string") : [];
  return {
    productId: v.product_id, name: v.name, category: str(v.category), brand: str(v.brand), priceCents: num(v.price_cents),
    rating: typeof v.rating === "number" ? v.rating : null, ratingCount: num(v.rating_count),
    color: colors[0] ?? (typeof v.color === "string" ? v.color : null),
    inStock: typeof v.in_stock === "boolean" ? v.in_stock : null,
  };
}

function stockView(data: unknown): StockView | null {
  const entries = listOf(data, "stock", "levels", "skus");
  const byColor = new Map<string, StockSize[]>();
  for (const e of entries) {
    if (!isRec(e) || typeof e.size !== "string") continue;
    const color = str(e.color, "default");
    const sizes = byColor.get(color) ?? [];
    sizes.push({ size: e.size, available: Math.max(0, num(e.available)), skuId: typeof e.sku_id === "string" ? e.sku_id : null });
    byColor.set(color, sizes);
  }
  if (byColor.size === 0) return null;
  const productId = isRec(data) && typeof data.product_id === "string" ? data.product_id : null;
  return { productId, colors: [...byColor].map(([color, sizes]) => ({ color, sizes })) };
}

function cartView(data: unknown): CartCardView | null {
  const cart = objectOf(data, "cart");
  if (!cart) return null;
  const lines = listOf(cart, "lines").flatMap((l): CartLineView[] => {
    if (!isRec(l) || typeof l.sku_id !== "string") return [];
    const quantity = num(l.quantity);
    return [{ skuId: l.sku_id, productId: str(l.product_id), name: str(l.name), category: str(l.category), size: str(l.size), color: str(l.color), quantity, lineTotalCents: num(l.line_total_cents, num(l.unit_price_cents) * quantity) }];
  });
  return { lines, count: lines.reduce((n, l) => n + l.quantity, 0), totalCents: num(cart.total_cents, num(cart.subtotal_cents)) };
}

function orderView(v: unknown): OrderCardView | null {
  if (!isRec(v) || typeof v.order_id !== "string") return null;
  const lines = Array.isArray(v.lines) ? v.lines : [];
  return {
    orderId: v.order_id, status: str(v.status), totalCents: num(v.total_cents), createdAt: str(v.created_at),
    itemCount: lines.reduce<number>((n, l) => n + (isRec(l) ? num(l.quantity) : 0), 0),
  };
}

export function parseCard(card: { kind: ChatCard["kind"]; data: unknown }): CardView | null {
  switch (card.kind) {
    case "signin": return { kind: "signin" };
    case "products": {
      const items = listOf(card.data, "products", "items").flatMap((p) => productView(p) ?? []);
      return items.length ? { kind: "products", items } : null;
    }
    case "product": {
      const obj = objectOf(card.data, "product");
      const item = productView(obj);
      return item ? { kind: "product", item, description: str(obj?.description) } : null;
    }
    case "stock": {
      const stock = stockView(card.data);
      return stock ? { kind: "stock", stock } : null;
    }
    case "cart": {
      const cart = cartView(card.data);
      return cart ? { kind: "cart", cart } : null;
    }
    case "order": {
      const order = orderView(objectOf(card.data, "order"));
      return order ? { kind: "order", order } : null;
    }
    case "orders": {
      const orders = listOf(card.data, "orders").flatMap((o) => orderView(o) ?? []);
      return orders.length ? { kind: "orders", orders } : null;
    }
  }
}

/** All the cards of a message that can be drawn. */
export function parseCards(cards: readonly { kind: ChatCard["kind"]; data: unknown }[]): CardView[] {
  return cards.flatMap((c) => parseCard(c) ?? []);
}
