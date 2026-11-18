import { describe, expect, it } from "vitest";
import { MAX_RESTORE_PAGES, pagesFor, readSnapshot, restorableSnapshot, withSnapshot } from "./listingState";

const routerState = { usr: { from: "x" }, key: "k1", idx: 4 };

describe("listing snapshot in history state", () => {
  it("round-trips through withSnapshot / readSnapshot and keeps the router's own fields", () => {
    const next = withSnapshot(routerState, { key: "category=jackets", pages: 3, scrollY: 2400.4 });
    expect(next).toMatchObject(routerState);
    expect(readSnapshot(next)).toEqual({ key: "category=jackets", pages: 3, scrollY: 2400 });
  });

  it("does not mutate the state it was given, and copes with no state at all", () => {
    const before = JSON.stringify(routerState);
    withSnapshot(routerState, { key: "", pages: 1, scrollY: 0 });
    expect(JSON.stringify(routerState)).toBe(before);
    expect(readSnapshot(withSnapshot(null, { key: "a", pages: 2, scrollY: 5 }))).toEqual({ key: "a", pages: 2, scrollY: 5 });
  });

  it("restores pages and scroll only for the same query", () => {
    const state = withSnapshot(routerState, { key: "q=hoodie", pages: 3, scrollY: 1800 });
    expect(restorableSnapshot(state, "q=hoodie")).toEqual({ pages: 3, scrollY: 1800 });
    expect(restorableSnapshot(state, "q=jeans")).toEqual({ pages: 1, scrollY: 0 });
    expect(restorableSnapshot(state, "")).toEqual({ pages: 1, scrollY: 0 });
  });

  it("restores nothing from missing or malformed state", () => {
    for (const bad of [undefined, null, 5, "x", [], {}, { listing: null }, { listing: { key: "a" } },
      { listing: { key: "a", pages: "3", scrollY: 0 } }, { listing: { key: "a", pages: Number.NaN, scrollY: 0 } },
      { listing: { key: "a", pages: 2, scrollY: Number.POSITIVE_INFINITY } }]) {
      expect(readSnapshot(bad)).toBeNull();
      expect(restorableSnapshot(bad, "a")).toEqual({ pages: 1, scrollY: 0 });
    }
  });

  it("clamps absurd values", () => {
    expect(restorableSnapshot(withSnapshot({}, { key: "a", pages: 999, scrollY: -50 }), "a")).toEqual({ pages: MAX_RESTORE_PAGES, scrollY: 0 });
    expect(restorableSnapshot({ listing: { key: "a", pages: 0.2, scrollY: 10 } }, "a").pages).toBe(1);
  });
});

describe("pagesFor", () => {
  it("counts the pages the loaded rows span", () => {
    expect(pagesFor(0, 24)).toBe(1);
    expect(pagesFor(24, 24)).toBe(1);
    expect(pagesFor(25, 24)).toBe(2);
    expect(pagesFor(72, 24)).toBe(3);
    expect(pagesFor(400, 24)).toBe(17);
  });
});
