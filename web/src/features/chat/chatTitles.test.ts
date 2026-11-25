import { describe, expect, it } from "vitest";
import { cleanTitle, relativeTime } from "./chatTitles";

describe("saved chat list", () => {
  const now = Date.parse("2026-01-10T12:00:00Z");
  it("says how long ago a chat was used", () => {
    expect(relativeTime("2026-01-10T11:59:40Z", now)).toBe("just now");
    expect(relativeTime("2026-01-10T11:55:00Z", now)).toBe("5 min ago");
    expect(relativeTime("2026-01-10T09:00:00Z", now)).toBe("3 h ago");
    expect(relativeTime("2026-01-08T12:00:00Z", now)).toBe("2 d ago");
    expect(relativeTime("nonsense", now)).toBe("");
  });
  it("trims a new title and refuses an empty one", () => {
    expect(cleanTitle("  Rain jackets ")).toBe("Rain jackets");
    expect(cleanTitle("   ")).toBeNull();
    expect(cleanTitle("x".repeat(100))).toHaveLength(80);
  });
});
