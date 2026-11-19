import { describe, expect, it } from "vitest";
import { newIdempotencyKey } from "./idempotency";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("newIdempotencyKey", () => {
  it("makes a v4 UUID, different every time", () => {
    const keys = new Set(Array.from({ length: 50 }, () => newIdempotencyKey()));
    expect(keys.size).toBe(50);
    for (const k of keys) expect(k).toMatch(UUID_V4);
  });
  it("falls back to getRandomValues where randomUUID is unavailable (insecure context)", () => {
    const source = { getRandomValues: <T extends ArrayBufferView | null>(array: T): T => { if (array) new Uint8Array(array.buffer).fill(0xff); return array; } };
    const key = newIdempotencyKey(source);
    expect(key).toMatch(UUID_V4);
    expect(key).toBe("ffffffff-ffff-4fff-bfff-ffffffffffff");
  });
});
