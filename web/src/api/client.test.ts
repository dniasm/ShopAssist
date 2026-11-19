import { afterEach, describe, expect, it, vi } from "vitest";
import { request, setAuthToken, setUnauthenticatedHandler } from "./client";

function answer401() {
  const body = JSON.stringify({ error: { code: "unauthenticated", message: "Wrong email or password." } });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 401, headers: { "content-type": "application/json" } })));
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAuthToken(null);
  setUnauthenticatedHandler(null);
});

describe("a 401 while signed in", () => {
  it("signs out when an ordinary call is refused (the session expired)", async () => {
    answer401();
    const signOut = vi.fn();
    setAuthToken("token");
    setUnauthenticatedHandler(signOut);
    await expect(request("GET", "/cart")).rejects.toMatchObject({ code: "unauthenticated" });
    expect(signOut).toHaveBeenCalledOnce();
  });

  it("keeps the session when it is a wrong password while switching accounts", async () => {
    answer401();
    const signOut = vi.fn();
    setAuthToken("token");
    setUnauthenticatedHandler(signOut);
    await expect(request("POST", "/session", { body: { email: "a@b.c", password: "x" } })).rejects.toMatchObject({ code: "unauthenticated" });
    await expect(request("POST", "/signup", { body: {} })).rejects.toMatchObject({ code: "unauthenticated" });
    expect(signOut).not.toHaveBeenCalled();
  });
});
