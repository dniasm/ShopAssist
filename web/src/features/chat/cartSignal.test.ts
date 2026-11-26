import { describe, expect, it } from "vitest";
import type { ChatWsMessage } from "../../api/types";
import { showsCart } from "./cartSignal";

const TS = "2026-01-01T00:00:00Z";
const message = (cards: { kind: string; data: unknown }[]) =>
  ({ seq: 1, ts: TS, channel: "chat", type: "message", data: { turn_id: "T1", text: "Added.", cards, closing: "" } }) as unknown as ChatWsMessage;

describe("showsCart", () => {
  it("is true for an assistant message with a cart card", () => {
    expect(showsCart(message([{ kind: "products", data: [] }, { kind: "cart", data: { lines: [] } }]))).toBe(true);
  });
  it("is false without one, and for other events", () => {
    expect(showsCart(message([{ kind: "products", data: [] }]))).toBe(false);
    expect(showsCart({ seq: 2, ts: TS, channel: "chat", type: "turn_done", data: { turn_id: "T1" } } as unknown as ChatWsMessage)).toBe(false);
  });
});
