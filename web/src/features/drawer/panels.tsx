import type { ReactNode } from "react";
import { CartPanel } from "../cart/CartPanel";
import type { DrawerKind } from "./DrawerContext";

/** What each drawer shows. The cart is the only occupant for now. */
export const DRAWER_PANELS: Record<DrawerKind, { title: string; body: () => ReactNode }> = {
  cart: { title: "Cart", body: () => <CartPanel /> },
};
