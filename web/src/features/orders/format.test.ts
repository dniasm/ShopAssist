import { describe, expect, it } from "vitest";
import { formatDate, formatDateTime, formatTime } from "./format";

describe("order date formatting", () => {
  it("formats a real timestamp", () => {
    expect(formatDateTime("2026-11-18T15:04:09Z")).toMatch(/^Nov 1[89], 2026, \d{1,2}:\d{2}\s?[AP]M$/);
    expect(formatDate("2026-11-18T12:00:00Z")).toMatch(/^Nov 18, 2026$/);
    expect(formatTime("2026-11-18T15:04:09Z")).toMatch(/\d{1,2}:\d{2}:\d{2}\s?[AP]M/);
  });
  it("never shows 'Invalid Date'", () => {
    for (const bad of ["", null, undefined, "not a date"]) {
      expect(formatDateTime(bad)).toBe("—");
      expect(formatDate(bad)).toBe("—");
      expect(formatTime(bad)).toBe("");
    }
  });
});
