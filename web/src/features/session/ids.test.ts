import { describe, expect, it } from "vitest";
import { guestIdFrom, newId } from "./ids";

function fakeStore(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return { data, getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => { data[k] = v; } };
}

describe("ids", () => {
  it("makes distinct ids that the backend accepts as guest ids", () => {
    const a = newId();
    expect(a).not.toBe(newId());
    expect(a).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
  });
  it("keeps one guest id per browser", () => {
    const store = fakeStore();
    const first = guestIdFrom(store, () => "guest-aaaa-1111");
    expect(first).toBe("guest-aaaa-1111");
    expect(guestIdFrom(store, () => "guest-bbbb-2222")).toBe("guest-aaaa-1111");
  });
  it("replaces a stored value that is not a valid id", () => {
    expect(guestIdFrom(fakeStore({ "shopassist.guest.v1": "no" }), () => "guest-cccc-3333")).toBe("guest-cccc-3333");
  });
  it("still returns an id when storage throws or is missing", () => {
    const blocked = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(guestIdFrom(blocked, () => "guest-dddd-4444")).toBe("guest-dddd-4444");
    expect(guestIdFrom(null, () => "guest-eeee-5555")).toBe("guest-eeee-5555");
  });
});
