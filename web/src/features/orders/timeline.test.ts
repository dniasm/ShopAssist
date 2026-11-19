import { describe, expect, it } from "vitest";
import type { OrderEvent, OrderStatusData } from "../../api/types";
import { advanceStatus, asOrderStatus, effectiveStatus, mergeEvents, timeline } from "./timeline";

const hist = (id: number, status: string, message = "", timestamp = `2026-01-01T10:0${id}:00Z`): OrderEvent => ({ event_id: id, order_id: "O-1", status, message, timestamp });
const live = (id: number, status: string, message = ""): OrderStatusData => ({ order_id: "O-1", status, message, event_id: id });
const states = (t: ReturnType<typeof timeline>) => t.steps.map((s) => `${s.status}:${s.state}${s.implied ? "~" : ""}`);

describe("timeline: the normal run", () => {
  it("an order that was just confirmed has one current step and four pending", () => {
    const t = timeline([hist(1, "CONFIRMED", "Paid")]);
    expect(states(t)).toEqual(["CONFIRMED:current", "PACKED:pending", "SHIPPED:pending", "OUT_FOR_DELIVERY:pending", "DELIVERED:pending"]);
    expect(t.steps[0]).toMatchObject({ at: "2026-01-01T10:01:00Z", message: "Paid" });
    expect(t.terminal).toBeNull();
    expect(t.status).toBe("CONFIRMED");
  });
  it("each live event moves the current step along; earlier ones become done", () => {
    const t = timeline([hist(1, "CONFIRMED")], [live(2, "PACKED"), live(3, "SHIPPED")]);
    expect(states(t)).toEqual(["CONFIRMED:done", "PACKED:done", "SHIPPED:current", "OUT_FOR_DELIVERY:pending", "DELIVERED:pending"]);
  });
  it("DELIVERED completes every step (nothing stays 'current')", () => {
    const t = timeline([1, 2, 3, 4, 5].map((i) => hist(i, ["CONFIRMED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"][i - 1] ?? "")));
    expect(states(t).every((s) => s.endsWith(":done"))).toBe(true);
    expect(t.status).toBe("DELIVERED");
  });
  it("no events at all says nothing", () => {
    const t = timeline([]);
    expect(t.status).toBeNull();
    expect(states(t).every((s) => s.endsWith(":pending"))).toBe(true);
  });
});

describe("timeline: dedupe", () => {
  it("the same event from history and from the socket counts once", () => {
    const t = timeline([hist(1, "CONFIRMED"), hist(2, "PACKED")], [live(1, "CONFIRMED"), live(2, "PACKED"), live(2, "PACKED")]);
    expect(states(t)).toEqual(["CONFIRMED:done", "PACKED:current", "SHIPPED:pending", "OUT_FOR_DELIVERY:pending", "DELIVERED:pending"]);
    expect(mergeEvents([hist(1, "CONFIRMED"), hist(2, "PACKED")], [live(1, "CONFIRMED"), live(2, "PACKED"), live(2, "PACKED")])).toHaveLength(2);
  });
  it("history's timestamp wins over a live copy of the same event", () => {
    const t = timeline([hist(1, "CONFIRMED")], [live(1, "CONFIRMED")]);
    expect(t.steps[0]?.at).toBe("2026-01-01T10:01:00Z");
  });
  it("a step is never shown twice, even if the server sends the same status under a new event_id", () => {
    const t = timeline([hist(1, "CONFIRMED"), hist(2, "PACKED")], [live(3, "PACKED", "again"), live(4, "CONFIRMED")]);
    expect(t.steps.filter((s) => s.state !== "pending")).toHaveLength(2);
    expect(t.steps[1]).toMatchObject({ status: "PACKED", state: "current", message: "" });
  });
});

describe("timeline: live timestamps", () => {
  it("a live event shows the time the socket stamped it with; history's own time wins when both exist", () => {
    const t = timeline([hist(1, "CONFIRMED")], [{ ...live(1, "CONFIRMED"), at: "2026-01-01T11:00:00Z" }, { ...live(2, "PACKED"), at: "2026-01-01T10:30:00Z" }]);
    expect(t.steps[0]?.at).toBe("2026-01-01T10:01:00Z");
    expect(t.steps[1]?.at).toBe("2026-01-01T10:30:00Z");
  });
});

describe("timeline: out of order", () => {
  it("events are applied by event_id whatever order they arrived in", () => {
    const shuffled = timeline([], [live(3, "SHIPPED"), live(1, "CONFIRMED"), live(2, "PACKED")]);
    const ordered = timeline([], [live(1, "CONFIRMED"), live(2, "PACKED"), live(3, "SHIPPED")]);
    expect(states(shuffled)).toEqual(states(ordered));
    expect(shuffled.status).toBe("SHIPPED");
    expect(shuffled.steps.map((s) => s.implied)).toEqual([false, false, false, false, false]);
  });
  it("a late older status (higher event_id, earlier step) is ignored: nothing moves backwards", () => {
    const t = timeline([], [live(1, "CONFIRMED"), live(2, "SHIPPED"), live(3, "PACKED")]);
    expect(t.status).toBe("SHIPPED");
    expect(t.steps[1]).toMatchObject({ status: "PACKED", state: "done", implied: true });   // proven by SHIPPED, not by the late event
  });
  it("a step whose event never arrived is shown as reached but implied (no time)", () => {
    const t = timeline([hist(1, "CONFIRMED")], [live(4, "OUT_FOR_DELIVERY")]);
    expect(states(t)).toEqual(["CONFIRMED:done", "PACKED:done~", "SHIPPED:done~", "OUT_FOR_DELIVERY:current", "DELIVERED:pending"]);
    expect(t.steps[1]?.at).toBeNull();
  });
  it("ignores statuses that are not steps (PENDING from checkout, unknown future ones)", () => {
    const t = timeline([hist(1, "PENDING"), hist(2, "CONFIRMED"), hist(3, "SOMETHING_NEW")]);
    expect(t.status).toBe("CONFIRMED");
  });
});

describe("timeline: terminal states", () => {
  it("CANCELLED mid-way freezes the steps reached and leaves the rest pending", () => {
    const t = timeline([hist(1, "CONFIRMED"), hist(2, "PACKED")], [live(3, "CANCELLED", "Cancelled by you; refunded")]);
    expect(states(t)).toEqual(["CONFIRMED:done", "PACKED:done", "SHIPPED:pending", "OUT_FOR_DELIVERY:pending", "DELIVERED:pending"]);
    expect(t.terminal).toEqual({ status: "CANCELLED", message: "Cancelled by you; refunded", at: null });
    expect(t.status).toBe("CANCELLED");
  });
  it("events after the terminal one (by event_id) are ignored, even if they arrive first", () => {
    const t = timeline([hist(1, "CONFIRMED")], [live(4, "SHIPPED"), live(2, "CANCELLED")]);
    expect(states(t)).toEqual(["CONFIRMED:done", "PACKED:pending", "SHIPPED:pending", "OUT_FOR_DELIVERY:pending", "DELIVERED:pending"]);
    expect(t.terminal?.status).toBe("CANCELLED");
  });
  it("the first terminal event wins", () => {
    expect(timeline([hist(1, "CONFIRMED"), hist(2, "FAILED"), hist(3, "CANCELLED")]).terminal?.status).toBe("FAILED");
  });
  it("FAILED with no steps (checkout never completed) shows nothing reached", () => {
    const t = timeline([hist(1, "PENDING"), hist(2, "FAILED", "payment declined")]);
    expect(t.terminal).toMatchObject({ status: "FAILED", message: "payment declined" });
    expect(states(t).every((s) => s.endsWith(":pending"))).toBe(true);
  });
});

describe("timeline: refresh mid-delivery", () => {
  it("history that already holds some events plus a socket replay of all of them gives the same timeline as the live run", () => {
    const all = [live(1, "CONFIRMED"), live(2, "PACKED"), live(3, "SHIPPED")];
    const liveRun = timeline([], all);
    const afterRefresh = timeline([hist(1, "CONFIRMED"), hist(2, "PACKED")], all);   // REST knew two, the replay brings three
    expect(states(afterRefresh)).toEqual(states(liveRun));
    expect(afterRefresh.status).toBe("SHIPPED");
    // and the replayed event that is still in flight when history catches up changes nothing
    const caughtUp = timeline([hist(1, "CONFIRMED"), hist(2, "PACKED"), hist(3, "SHIPPED")], all);
    expect(states(caughtUp)).toEqual(states(liveRun));
    expect(caughtUp.steps.map((s) => s.at !== null)).toEqual([true, true, true, false, false]);
  });
});

describe("effective status", () => {
  it("moves the recorded status forward when events prove more, never backward", () => {
    expect(effectiveStatus("CONFIRMED", timeline([], [live(1, "CONFIRMED"), live(2, "PACKED")]))).toBe("PACKED");
    expect(effectiveStatus("SHIPPED", timeline([], [live(1, "CONFIRMED")]))).toBe("SHIPPED");
    expect(effectiveStatus("CONFIRMED", timeline([]))).toBe("CONFIRMED");
  });
  it("terminal wins from either side", () => {
    expect(effectiveStatus("CANCELLED", timeline([], [live(1, "CONFIRMED"), live(2, "SHIPPED")]))).toBe("CANCELLED");
    expect(effectiveStatus("PACKED", timeline([], [live(1, "CONFIRMED"), live(2, "CANCELLED")]))).toBe("CANCELLED");
  });
  it("a PENDING order moves on the first step event", () => {
    expect(effectiveStatus("PENDING", timeline([], [live(1, "CONFIRMED")]))).toBe("CONFIRMED");
  });
});

describe("advanceStatus (the list's live rows)", () => {
  it("goes forward only, ends at terminal, ignores unknown or PENDING", () => {
    expect(advanceStatus("CONFIRMED", live(2, "PACKED"))).toBe("PACKED");
    expect(advanceStatus("SHIPPED", live(3, "PACKED"))).toBe("SHIPPED");
    expect(advanceStatus("PACKED", live(4, "CANCELLED"))).toBe("CANCELLED");
    expect(advanceStatus("CANCELLED", live(5, "SHIPPED"))).toBe("CANCELLED");
    expect(advanceStatus("CONFIRMED", live(6, "PENDING"))).toBe("CONFIRMED");
    expect(advanceStatus("CONFIRMED", live(7, "SOMETHING_NEW"))).toBe("CONFIRMED");
  });
  it("asOrderStatus narrows strings safely", () => {
    expect(asOrderStatus("OUT_FOR_DELIVERY")).toBe("OUT_FOR_DELIVERY");
    expect(asOrderStatus("nope")).toBeNull();
  });
});
