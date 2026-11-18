import type { ReactNode } from "react";
import type { DrawerKind } from "./DrawerContext";

/** What each drawer shows. Placeholders until their phase: 5.2 replaces `cart`, 5.4 replaces `chat`. */
export const DRAWER_PANELS: Record<DrawerKind, { title: string; body: () => ReactNode }> = {
  cart: {
    title: "Cart",
    body: () => (
      <div className="muted">
        <p>Your cart will live here.</p>
        <p>Adding items and checking out arrive in the next update.</p>
      </div>
    ),
  },
  chat: {
    title: "Assistant",
    body: () => (
      <div className="muted">
        <p>The shopping assistant will open here, on every page.</p>
      </div>
    ),
  },
};
