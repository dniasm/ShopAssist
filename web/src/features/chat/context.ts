import type { ChatContext } from "../../api/types";

/** The page context sent with every chat message: the route the shopper is on, and the product on it. It is read when the message is
 *  sent (never when the drawer was opened), straight from the route, so it cannot lag behind a navigation. */
export function chatContextFor(pathname: string): ChatContext {
  const match = /^\/p\/([^/?#]+)/.exec(pathname);
  if (!match?.[1]) return { path: pathname };
  try {
    return { path: pathname, product_id: decodeURIComponent(match[1]) };
  } catch {
    return { path: pathname, product_id: match[1] };
  }
}
