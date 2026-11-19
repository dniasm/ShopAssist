import { describe, expect, it } from "vitest";
import { parseStored, trimStored } from "./chatStorage";

describe("stored chat", () => {
  it("reads what it wrote", () => {
    const raw = JSON.stringify({ texts: { T1: "hello" }, decided: { "PR-1": { state: "confirmed", afterSeq: 4, orderId: "O-7" }, "PR-2": { state: "dismissed" } } });
    expect(parseStored(raw)).toEqual({ texts: { T1: "hello" }, decided: { "PR-1": { state: "confirmed", afterSeq: 4, orderId: "O-7" }, "PR-2": { state: "dismissed", afterSeq: null, orderId: null } } });
  });
  it("treats anything unexpected as empty and skips bad entries", () => {
    for (const raw of [null, "", "nope", "[]", "5", JSON.stringify({ texts: 3 })]) expect(parseStored(raw)).toEqual({ texts: {}, decided: {} });
    const mixed = parseStored(JSON.stringify({ texts: { a: "x", b: 5 }, decided: { c: { state: "weird" }, d: "no" } }));
    expect(mixed).toEqual({ texts: { a: "x" }, decided: {} });
  });
  it("keeps only the newest texts", () => {
    const many = Object.fromEntries(Array.from({ length: 100 }, (_v, i) => [`T${i}`, `q${i}`]));
    const trimmed = trimStored({ texts: many, decided: {} });
    expect(Object.keys(trimmed.texts)).toHaveLength(60);
    expect(trimmed.texts.T99).toBe("q99");
    expect(trimmed.texts.T0).toBeUndefined();
  });
});
