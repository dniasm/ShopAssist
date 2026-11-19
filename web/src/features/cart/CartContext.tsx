import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { getCart, removeCartLine, setCartLine } from "../../api/endpoints";
import { ApiError, describeError } from "../../api/errors";
import type { Cart } from "../../api/types";
import { useAuth } from "../auth/AuthContext";
import { useSocketChannel } from "../session/SessionSocket";
import { MAX_PER_LINE } from "../product/variants";
import { itemCount } from "./cartMath";

export type CartStatus = "signed_out" | "loading" | "ready" | "error";

export type AddResult =
  | { ok: true; quantity: number; capped: boolean }
  | { ok: false; reason: "max" | "error"; message: string; outOfStock: boolean };

interface CartApi {
  cart: Cart | null;
  status: CartStatus;
  /** Items in the cart (sum of quantities), 0 when signed out. */
  count: number;
  /** A line change is in flight for this sku (or "*" for a whole-cart one): its controls are disabled. */
  busySku: string | null;
  /** The last failed change, in words, for the drawer to show. */
  actionError: string | null;
  clearActionError: () => void;
  refresh: () => Promise<Cart | null>;
  /** Adds `quantity` to what is already in the cart (the API sets absolute quantities, so this reads the cart first). */
  addToCart: (skuId: string, quantity: number) => Promise<AddResult>;
  setQuantity: (skuId: string, quantity: number) => Promise<void>;
  removeLine: (skuId: string) => Promise<void>;
}

const CartContext = createContext<CartApi | null>(null);

export function useCart(): CartApi {
  const value = useContext(CartContext);
  if (!value) throw new Error("useCart must be used inside <CartProvider>");
  return value;
}

/** The signed-in shopper's cart: loaded on sign-in, refetched on a socket resync, changed only through the API (every
 *  change returns the whole cart, which replaces what is shown). Changes run one at a time so quick clicks cannot race. */
export function CartProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const [cart, setCart] = useState<Cart | null>(null);
  const [status, setStatus] = useState<CartStatus>(token ? "loading" : "signed_out");
  const [busySku, setBusySku] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const cartRef = useRef<Cart | null>(null);
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const queue = useRef<Promise<unknown>>(Promise.resolve());

  const apply = useCallback((next: Cart | null) => { cartRef.current = next; setCart(next); }, []);

  const refresh = useCallback(async (): Promise<Cart | null> => {
    if (!tokenRef.current) return null;
    try {
      const fresh = await getCart();
      apply(fresh);
      setStatus("ready");
      return fresh;
    } catch (err) {
      // A failed refresh keeps what is on screen; only the very first load shows an error state.
      if (cartRef.current === null) setStatus("error");
      setActionError(describeError(err));
      return null;
    }
  }, [apply]);

  useEffect(() => {
    apply(null);
    setActionError(null);
    if (!token) { setStatus("signed_out"); return; }
    setStatus("loading");
    void refresh();
  }, [token, apply, refresh]);

  // The server says our view may be stale (we were offline longer than its replay buffer).
  useSocketChannel("system", (env) => { if (env.type === "resync") void refresh(); });

  /** Runs `change` after every earlier change has finished. */
  const enqueue = useCallback(<T,>(sku: string, change: () => Promise<T>): Promise<T> => {
    const run = queue.current.then(async () => {
      setBusySku(sku);
      setActionError(null);
      try { return await change(); } finally { setBusySku(null); }
    });
    queue.current = run.catch(() => undefined);
    return run;
  }, []);

  const addToCart = useCallback((skuId: string, quantity: number): Promise<AddResult> => enqueue(skuId, async () => {
    try {
      const current = cartRef.current ?? (await getCart());
      const existing = current.lines.find((l) => l.sku_id === skuId)?.quantity ?? 0;
      if (existing >= MAX_PER_LINE) {
        apply(current);
        return { ok: false, reason: "max", message: `You already have ${MAX_PER_LINE} of this in your cart, the most one order can hold.`, outOfStock: false } as const;
      }
      const target = Math.min(MAX_PER_LINE, existing + quantity);
      const next = await setCartLine(skuId, target);
      apply(next);
      setStatus("ready");
      return { ok: true, quantity: target, capped: existing + quantity > MAX_PER_LINE } as const;
    } catch (err) {
      await refresh();   // stock may have changed under us: show the truth
      return { ok: false, reason: "error", message: describeError(err), outOfStock: err instanceof ApiError && err.code === "out_of_stock" } as const;
    }
  }), [enqueue, apply, refresh]);

  const setQuantity = useCallback((skuId: string, quantity: number): Promise<void> => enqueue(skuId, async () => {
    try {
      apply(await setCartLine(skuId, Math.max(0, Math.min(MAX_PER_LINE, quantity))));
    } catch (err) {
      setActionError(describeError(err));
      await refresh();
    }
  }), [enqueue, apply, refresh]);

  const removeLine = useCallback((skuId: string): Promise<void> => enqueue(skuId, async () => {
    try {
      apply(await removeCartLine(skuId));
    } catch (err) {
      setActionError(describeError(err));
      await refresh();
    }
  }), [enqueue, apply, refresh]);

  const value = useMemo<CartApi>(() => ({
    cart, status, count: itemCount(cart), busySku, actionError,
    clearActionError: () => setActionError(null),
    refresh, addToCart, setQuantity, removeLine,
  }), [cart, status, busySku, actionError, refresh, addToCart, setQuantity, removeLine]);

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}
