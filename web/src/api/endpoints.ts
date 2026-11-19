import { request } from "./client";
import type {
  Cart, CancelOrderBody, ChatAccepted, ChatRequest, CheckoutAccepted, ListProductsParams, LlmStatus, Order, OrderList, PaymentMethod, ProductDetail, ProductList, ProposalAck, ServicesHealth,
  Session, User,
} from "./types";

/** One function per REST route. Components never build URLs; hooks call these. */

export async function listUsers(signal?: AbortSignal): Promise<User[]> {
  const res = await request<{ users: User[] }>("GET", "/users", { signal });
  return res.users;
}

export function createSession(userId: string, signal?: AbortSignal): Promise<Session> {
  return request<Session>("POST", "/session", { body: { user_id: userId }, signal });
}

export async function getMe(signal?: AbortSignal): Promise<User> {
  const res = await request<{ user: User }>("GET", "/me", { signal });
  return res.user;
}

export function listProducts(params: ListProductsParams, signal?: AbortSignal): Promise<ProductList> {
  return request<ProductList>("GET", "/products", { query: { ...params }, signal });
}

export function getProduct(productId: string, signal?: AbortSignal): Promise<ProductDetail> {
  return request<ProductDetail>("GET", `/products/${encodeURIComponent(productId)}`, { signal });
}

export function getCart(signal?: AbortSignal): Promise<Cart> {
  return request<Cart>("GET", "/cart", { signal });
}

/** Sets the line to an absolute quantity (0 removes it; the API caps a line at 10). */
export function setCartLine(skuId: string, quantity: number, signal?: AbortSignal): Promise<Cart> {
  return request<Cart>("PUT", `/cart/lines/${encodeURIComponent(skuId)}`, { body: { quantity }, signal });
}

export function removeCartLine(skuId: string, signal?: AbortSignal): Promise<Cart> {
  return request<Cart>("DELETE", `/cart/lines/${encodeURIComponent(skuId)}`, { signal });
}

/** Starts the checkout saga for the cart's lines. Progress arrives on the WebSocket's `checkout` channel. */
export function placeOrder(idempotencyKey: string, paymentMethod: PaymentMethod, signal?: AbortSignal): Promise<CheckoutAccepted> {
  return request<CheckoutAccepted>("POST", "/checkout", { body: { idempotency_key: idempotencyKey, payment_method: paymentMethod }, signal });
}

export async function getOrder(orderId: string, signal?: AbortSignal): Promise<Order> {
  const res = await request<{ order: Order }>("GET", `/orders/${encodeURIComponent(orderId)}`, { signal });
  return res.order;
}

/** The caller's orders, newest first. List rows carry their lines but not their `history`. */
export function listOrders(params: { page_size?: number; page_token?: string } = {}, signal?: AbortSignal): Promise<OrderList> {
  return request<OrderList>("GET", "/orders", { query: { ...params }, signal });
}

/** Cancels a CONFIRMED or PACKED order (anything else is a 409 `conflict`). The same key always returns the same result. */
export async function cancelOrder(orderId: string, idempotencyKey: string, signal?: AbortSignal): Promise<Order> {
  const body: CancelOrderBody = { idempotency_key: idempotencyKey };
  const res = await request<{ order: Order }>("POST", `/orders/${encodeURIComponent(orderId)}/cancel`, { body, signal });
  return res.order;
}

/** One short gRPC health call per service, made by the gateway. No sign-in needed. */
export function getServicesHealth(signal?: AbortSignal): Promise<ServicesHealth> {
  return request<ServicesHealth>("GET", "/health/services", { signal });
}

/** The Groq key pool: model, limits, and per key the state and what is left. Keys come masked; nothing more is ever sent. */
export function getLlmStatus(signal?: AbortSignal): Promise<LlmStatus> {
  return request<LlmStatus>("GET", "/llm/status", { signal });
}

// ---- chat ----

/** Starts an assistant turn; its progress arrives on the WebSocket's `chat` channel. */
export function sendChat(body: ChatRequest, signal?: AbortSignal): Promise<ChatAccepted> {
  return request<ChatAccepted>("POST", "/chat", { body, signal });
}

/** Runs the proposed write as the signed-in user. A proposal can be confirmed once: a second try is a 409. */
export function confirmProposal(proposalId: string, signal?: AbortSignal): Promise<ProposalAck> {
  return request<ProposalAck>("POST", `/chat/proposals/${encodeURIComponent(proposalId)}/confirm`, { signal });
}

export function dismissProposal(proposalId: string, signal?: AbortSignal): Promise<ProposalAck> {
  return request<ProposalAck>("POST", `/chat/proposals/${encodeURIComponent(proposalId)}/dismiss`, { signal });
}
