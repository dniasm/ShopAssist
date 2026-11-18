import { describe, expect, it } from "vitest";
import { centsToDollarsText, formatMoney, parseDollarsToCents } from "./money";

describe("formatMoney", () => {
  it("formats integer cents as dollars", () => {
    expect(formatMoney(1299)).toBe("$12.99");
    expect(formatMoney(5)).toBe("$0.05");
    expect(formatMoney(0)).toBe("$0.00");
    expect(formatMoney(100)).toBe("$1.00");
  });
  it("separates thousands and handles negatives", () => {
    expect(formatMoney(123456789)).toBe("$1,234,567.89");
    expect(formatMoney(-381)).toBe("-$3.81");
    expect(formatMoney(-100000)).toBe("-$1,000.00");
  });
  it("never shows NaN", () => {
    expect(formatMoney(Number.NaN)).toBe("—");
  });
});

describe("dollar text <-> cents", () => {
  it("parses plain amounts exactly", () => {
    expect(parseDollarsToCents("12.5")).toBe(1250);
    expect(parseDollarsToCents("12.05")).toBe(1205);
    expect(parseDollarsToCents(" 7 ")).toBe(700);
    expect(parseDollarsToCents("0.1")).toBe(10);
  });
  it("rejects anything else", () => {
    for (const bad of ["", "abc", "-1", "1,000", "1.234", "$5", "1e3", "."]) expect(parseDollarsToCents(bad)).toBeNull();
  });
  it("writes the shortest exact text", () => {
    expect(centsToDollarsText(1250)).toBe("12.5");
    expect(centsToDollarsText(1200)).toBe("12");
    expect(centsToDollarsText(1205)).toBe("12.05");
    expect(centsToDollarsText(99)).toBe("0.99");
  });
});
