import { describe, expect, it } from "vitest";
import { authFrame, isAuthOk, socketUrl } from "./handshake";
import { parseSocketMessage } from "./events";

describe("socket handshake", () => {
  it("puts no credentials in the URL, only the resume cursor", () => {
    expect(socketUrl({ protocol: "http:", host: "localhost:5173" }, 12)).toBe("ws://localhost:5173/api/ws?after=12");
    expect(socketUrl({ protocol: "https:", host: "shop.example" }, 0)).toBe("wss://shop.example/api/ws?after=0");
  });

  it("sends the session token as the first message", () => {
    expect(JSON.parse(authFrame({ token: "t.o.k" }))).toEqual({ type: "auth", token: "t.o.k" });
  });

  it("sends a guest id (and no token) for a signed-out visitor", () => {
    expect(JSON.parse(authFrame({ guest: "guest-1234" }))).toEqual({ type: "auth", guest: "guest-1234" });
  });

  it("recognises auth_ok, and nothing else, as the signal that the socket is authenticated", () => {
    const ok = parseSocketMessage(JSON.stringify({ seq: 0, ts: "t", channel: "system", type: "auth_ok", data: { guest: false } }));
    const resync = parseSocketMessage(JSON.stringify({ seq: 0, ts: "t", channel: "system", type: "resync", data: {} }));
    expect(ok && isAuthOk(ok)).toBe(true);
    expect(resync && isAuthOk(resync)).toBe(false);
  });
});
