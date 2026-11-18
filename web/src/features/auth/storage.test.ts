import { describe, expect, it } from "vitest";
import { parseStoredSession } from "./storage";

const user = { user_id: "U-01", name: "Maya Okafor", email: "m@example.com", city: "Austin" };

describe("parseStoredSession", () => {
  it("accepts a well-formed session", () => {
    expect(parseStoredSession(JSON.stringify({ token: "t", user }))).toEqual({ token: "t", user });
  });
  it("treats anything else as signed out", () => {
    for (const raw of [null, "", "not json", "[]", "null", JSON.stringify({ token: "", user }),
      JSON.stringify({ token: "t" }), JSON.stringify({ token: "t", user: { user_id: "U-01" } }),
      JSON.stringify({ token: 5, user })]) {
      expect(parseStoredSession(raw)).toBeNull();
    }
  });
});
