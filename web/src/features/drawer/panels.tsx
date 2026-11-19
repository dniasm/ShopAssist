import type { ReactNode } from "react";
import { CartPanel } from "../cart/CartPanel";
import { ChatPanel } from "../chat/ChatPanel";
import type { DrawerKind } from "./DrawerContext";

/** What each drawer shows. `modal`: a scrim covers the page and focus is trapped by the dialog; otherwise it docks beside the page. */
export const DRAWER_PANELS: Record<DrawerKind, { title: string; modal: boolean; body: () => ReactNode }> = {
  cart: { title: "Cart", modal: true, body: () => <CartPanel /> },
  chat: { title: "Assistant", modal: false, body: () => <ChatPanel /> },
};
