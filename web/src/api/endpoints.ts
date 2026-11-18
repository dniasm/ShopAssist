import { request } from "./client";
import type { ListProductsParams, ProductDetail, ProductList, Session, User } from "./types";

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
