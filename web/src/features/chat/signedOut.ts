/* Chatting while signed out. The assistant lives behind sign-in (its replies come over the signed-in socket), but a visitor can still ask
 * about the catalogue: such a question is answered from the public product search, and anything about the cart or orders gets the sign-in
 * prompt. This is the client's own small stand-in and it says so (the turn is marked "Search", not "AI" or "Rules"). */

const ACCOUNT_WORDS = /\b(cart|basket|order|orders|checkout|check out|pay|payment|track|tracking|cancel|refund|deliver|delivery|my account|buy it|purchase)\b/i;

export type SignedOutKind = "account" | "search";

export function classifySignedOut(message: string): SignedOutKind {
  return ACCOUNT_WORDS.test(message) ? "account" : "search";
}

const LEAD = /^(please\s+)?(can you |could you |do you (have|sell|stock) |i('m| am) (looking|searching) for |i (want|need|would like) |show me |find me |find |get me |search( for)? |any |some )+/i;

/** The catalogue search for a free-text question: filler at the front and the question mark at the end are dropped. */
export function searchQueryFrom(message: string): string {
  const q = message.trim().replace(/[?!.]+$/g, "").replace(LEAD, "").replace(/\s+/g, " ").trim();
  return (q || message.trim()).slice(0, 120);
}

export const SIGN_IN_PROMPT = "Sign in to ask about your cart and orders, or to have me add things for you. I can still look things up in the catalogue.";
