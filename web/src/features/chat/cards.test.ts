import { describe, expect, it } from "vitest";
import type { ChatCard } from "../../api/types";
import { parseCard, parseCards } from "./cards";

const product = (id: string, over: Record<string, unknown> = {}) => ({ product_id: id, name: `Product ${id}`, category: "hoodies", brand: "Brevlan", price_cents: 5999, rating: 4.5, rating_count: 12, colors: ["navy", "red"], sizes: ["M"], in_stock: true, ...over });
const card = (kind: ChatCard["kind"], data: unknown) => ({ kind, data });

describe("cards", () => {
  it("products: a list, or a list under `products`; each with its first colour; junk entries dropped", () => {
    const a = parseCard(card("products", [product("P-1"), { nope: 1 }, product("P-2", { in_stock: false })]));
    expect(a?.kind === "products" ? a.items.map((i) => [i.productId, i.color, i.inStock]) : null).toEqual([["P-1", "navy", true], ["P-2", "navy", false]]);
    const b = parseCard(card("products", { products: [product("P-3")] }));
    expect(b?.kind === "products" ? b.items[0]?.priceCents : null).toBe(5999);
    expect(parseCard(card("products", []))).toBeNull();
    expect(parseCard(card("products", "text"))).toBeNull();
  });
  it("product: the product itself or {product}, with its description", () => {
    const a = parseCard(card("product", { product: product("P-1", { description: "Warm." }) }));
    expect(a).toMatchObject({ kind: "product", description: "Warm.", item: { productId: "P-1", name: "Product P-1" } });
    expect(parseCard(card("product", product("P-2")))?.kind).toBe("product");
    expect(parseCard(card("product", { product: {} }))).toBeNull();
  });
  it("stock: groups SKUs by colour, keeps sizes in the order given, clamps negatives", () => {
    const s = parseCard(card("stock", { product_id: "P-1", stock: [
      { sku_id: "S1", color: "navy", size: "S", available: 3 }, { sku_id: "S2", color: "navy", size: "M", available: 0 },
      { sku_id: "S3", color: "red", size: "S", available: -2 }, { nope: 1 },
    ] }));
    expect(s).toMatchObject({ kind: "stock", stock: { productId: "P-1" } });
    expect(s?.kind === "stock" ? s.stock.colors.map((c) => [c.color, c.sizes.map((z) => `${z.size}:${z.available}`)]) : null)
      .toEqual([["navy", ["S:3", "M:0"]], ["red", ["S:0"]]]);
    expect(parseCard(card("stock", []))).toBeNull();
  });
  it("cart: lines and the total, also from {cart}", () => {
    const line = { sku_id: "S1", product_id: "P-1", name: "Hoodie", category: "hoodies", size: "M", color: "navy", quantity: 2, unit_price_cents: 3000, line_total_cents: 6000 };
    const c = parseCard(card("cart", { cart: { lines: [line], total_cents: 6499 } }));
    expect(c).toMatchObject({ kind: "cart", cart: { count: 2, totalCents: 6499, lines: [{ name: "Hoodie", lineTotalCents: 6000 }] } });
    expect(parseCard(card("cart", { lines: [], total_cents: 0 }))).toMatchObject({ kind: "cart", cart: { count: 0 } });
  });
  it("order and orders: id, status, total and item count", () => {
    const order = { order_id: "O-1", status: "SHIPPED", total_cents: 8000, created_at: "2026-01-01T00:00:00Z", lines: [{ quantity: 2 }, { quantity: 1 }] };
    expect(parseCard(card("order", { order }))).toMatchObject({ kind: "order", order: { orderId: "O-1", status: "SHIPPED", itemCount: 3, totalCents: 8000 } });
    const many = parseCard(card("orders", { orders: [order, { nope: 1 }, { ...order, order_id: "O-2" }] }));
    expect(many?.kind === "orders" ? many.orders.map((o) => o.orderId) : null).toEqual(["O-1", "O-2"]);
    expect(parseCard(card("orders", []))).toBeNull();
  });
  it("signin: needs no data", () => {
    expect(parseCard(card("signin", {}))).toEqual({ kind: "signin" });
  });
  it("parseCards keeps only what can be drawn", () => {
    expect(parseCards([card("products", []), card("order", { order: { order_id: "O-1", status: "PACKED" } })]).map((c) => c.kind)).toEqual(["order"]);
  });
});
