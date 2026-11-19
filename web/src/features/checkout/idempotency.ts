type RandomSource = Pick<Crypto, "getRandomValues"> & Partial<Pick<Crypto, "randomUUID">>;

/** A fresh UUID v4 for one checkout attempt. The checkout page makes ONE when it opens and reuses it for every click and
 *  retry, so the server sees the same key and a double click can never create two orders. `randomUUID` needs a secure
 *  context (https or localhost); the fallback builds a v4 from getRandomValues. */
export function newIdempotencyKey(source: RandomSource = globalThis.crypto): string {
  if (typeof source.randomUUID === "function") return source.randomUUID();
  const bytes = source.getRandomValues(new Uint8Array(16));
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;   // version 4
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;   // variant 10xx
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
