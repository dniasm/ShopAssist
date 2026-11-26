import type { ChatWsMessage } from "../../api/types";

/** True when this chat event shows the cart (the assistant's cart tool answers with a cart card whether it viewed or changed
 *  it), so the web cart should be read again from the server. */
export function showsCart(m: ChatWsMessage): boolean {
  return m.type === "message" && m.data.cards.some((c) => c.kind === "cart");
}
