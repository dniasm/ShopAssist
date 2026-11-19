import { describe, expect, it } from "vitest";
import type { LlmStatus, ServicesHealth } from "../../api/types";
import { diagramLayout, wireTone, type Diagram } from "./diagram";
import { CHECKOUT_STEPS } from "./explainer";
import { SERVICE_ORDER, ageText, finding, latencyText, statusMap, type ServiceName } from "./health";
import { coolingText, isRulesOnly, keyState, meter, poolSummary, secondsUntil } from "./llm";

const svc = (name: string, status: "up" | "down", latency = 50, error: string | null = null) => ({ name, service: `shopassist.v2.${name}`, address: "localhost:1", status, latency_ms: latency, error });
const health = (down: string[] = []): ServicesHealth => ({
  all_up: down.length === 0,
  checked_at: "2026-01-01T00:00:00Z",
  services: SERVICE_ORDER.map((n) => svc(n, down.includes(n) ? "down" : "up", 40, down.includes(n) ? "unavailable" : null)),
});

describe("diagram model", () => {
  for (const mode of ["wide", "narrow"] as const) {
    const d: Diagram = diagramLayout(mode);
    describe(mode, () => {
      it("has the browser, the orchestrator, five services and five databases, with unique ids", () => {
        expect(d.nodes.filter((n) => n.kind === "browser")).toHaveLength(1);
        expect(d.nodes.filter((n) => n.kind === "orchestrator")).toHaveLength(1);
        expect(d.nodes.filter((n) => n.kind === "service").map((n) => n.service)).toEqual([...SERVICE_ORDER]);
        expect(d.nodes.filter((n) => n.kind === "database")).toHaveLength(5);
        expect(new Set(d.nodes.map((n) => n.id)).size).toBe(d.nodes.length);
        expect(new Set(d.edges.map((e) => e.id)).size).toBe(d.edges.length);
      });
      it("says the orchestrator owns no database, and every service has its own", () => {
        expect(d.nodes.find((n) => n.kind === "orchestrator")?.lines.join(" ")).toMatch(/No database/);
        for (const s of SERVICE_ORDER) expect(d.nodes.filter((n) => n.kind === "database" && n.service === s)).toHaveLength(1);
      });
      it("keeps every box inside the drawing and no two services overlap", () => {
        for (const n of d.nodes) {
          expect(n.x).toBeGreaterThanOrEqual(0);
          expect(n.y).toBeGreaterThanOrEqual(0);
          expect(n.x + n.w).toBeLessThanOrEqual(d.width);
          expect(n.y + n.h).toBeLessThanOrEqual(d.height);
        }
        const svcs = d.nodes.filter((n) => n.kind === "service").sort((a, b) => a.y - b.y);
        for (let i = 1; i < svcs.length; i++) expect((svcs[i - 1]?.y ?? 0) + (svcs[i - 1]?.h ?? 0)).toBeLessThan(svcs[i]?.y ?? 0);
      });
      it("draws Order calling Catalog, Inventory and Payment, and nothing else service to service", () => {
        const s2s = d.edges.filter((e) => e.kind === "s2s");
        expect(s2s.map((e) => e.id).sort()).toEqual(["s2s-order-catalog", "s2s-order-inventory", "s2s-order-payment"]);
        for (const e of s2s) expect(e.involves[0]).toBe("order");
      });
      it("wires the orchestrator to each service, and the browser both ways (REST in, WebSocket out)", () => {
        expect(d.edges.filter((e) => e.kind === "grpc")).toHaveLength(5);
        expect(d.edges.filter((e) => e.kind === "rest" || e.kind === "ws").map((e) => e.kind).sort()).toEqual(["rest", "ws"]);
      });
    });
  }
  it("wide runs left to right and narrow top to bottom", () => {
    const w = diagramLayout("wide"), n = diagramLayout("narrow");
    const x = (d: Diagram, id: string) => d.nodes.find((q) => q.id === id)?.x ?? -1;
    const y = (d: Diagram, id: string) => d.nodes.find((q) => q.id === id)?.y ?? -1;
    expect(x(w, "browser")).toBeLessThan(x(w, "orchestrator"));
    expect(x(w, "orchestrator")).toBeLessThan(x(w, "svc-catalog"));
    expect(x(w, "svc-catalog")).toBeLessThan(x(w, "db-catalog"));
    expect(y(n, "browser")).toBeLessThan(y(n, "orchestrator"));
    expect(y(n, "orchestrator")).toBeLessThan(y(n, "svc-catalog"));
    expect(n.width).toBeLessThan(w.width);
  });
  it("a wire turns red when a service at either end is down", () => {
    const down = (s: ServiceName) => s === "payment";
    expect(wireTone({ kind: "grpc", involves: ["payment"] }, down)).toBe("bad");
    expect(wireTone({ kind: "s2s", involves: ["order", "payment"] }, down)).toBe("bad");
    expect(wireTone({ kind: "s2s", involves: ["order", "catalog"] }, down)).toBe("quiet");
    expect(wireTone({ kind: "rest", involves: [] }, down)).toBe("normal");
  });
});

describe("health helpers", () => {
  it("maps statuses by service name and is 'checking' until something arrives", () => {
    expect(Object.values(statusMap(null)).every((s) => s.status === "checking")).toBe(true);
    const m = statusMap(health(["payment"]));
    expect(m.payment).toMatchObject({ status: "down", error: "unavailable" });
    expect(m.catalog).toMatchObject({ status: "up", latencyMs: 40 });
  });
  it("ignores a service it does not draw", () => {
    const extra: ServicesHealth = { ...health(), services: [...health().services, svc("mystery", "down")] };
    expect(Object.values(statusMap(extra)).filter((s) => s.status === "down")).toHaveLength(0);
  });
  it("words the one-line finding", () => {
    expect(finding(null, false)).toEqual({ text: "Checking the services…", tone: "wait" });
    expect(finding(null, true).tone).toBe("bad");
    expect(finding(health(), false)).toEqual({ text: "All 5 services are up.", tone: "ok" });
    expect(finding(health(["order"]), false)).toEqual({ text: "The order service is down.", tone: "bad" });
    expect(finding(health(["catalog", "payment"]), false).text).toBe("2 services are down: Catalog, Payment.");
  });
  it("keeps showing the last good answer when a refresh fails", () => {
    expect(finding(health(), true).tone).toBe("ok");
  });
  it("formats ages and latency", () => {
    expect([0, 1, 4, 59, 89, 120].map(ageText)).toEqual(["just now", "just now", "4 s ago", "59 s ago", "89 s ago", "2 min ago"]);
    expect(latencyText(61)).toBe("61 ms");
    expect(latencyText(null)).toBe("—");
  });
});

describe("key pool helpers", () => {
  const key = (state: string, over: Partial<LlmStatus["keys"][number]> = {}): LlmStatus["keys"][number] => ({ key: "key#1 …WDDu", state, rpm_left: 30, rpd_left: 1000, tpm_left: 8000, tpd_left: 200000, cooling_until: null, ...over });
  const status = (keys: LlmStatus["keys"], enabled = true): LlmStatus => ({ enabled, model: "openai/gpt-oss-120b", limits: { rpm: 30, rpd: 1000, tpm: 8000, tpd: 200000 }, keys });

  it("meters what is left against the limit", () => {
    expect(meter(30, 30)).toEqual({ fraction: 1, tone: "ok", text: "30 of 30 left" });
    expect(meter(5, 30)).toMatchObject({ tone: "low", text: "5 of 30 left" });
    expect(meter(0, 8000)).toMatchObject({ fraction: 0, tone: "empty" });
    expect(meter(200000, 200000).text).toBe("200,000 of 200,000 left");
  });
  it("clamps nonsense and shows a dash for what is not tracked", () => {
    expect(meter(40, 30).fraction).toBe(1);
    expect(meter(-3, 30).fraction).toBe(0);
    for (const missing of [null, undefined, Number.NaN]) expect(meter(missing, 30)).toEqual({ fraction: null, tone: "unknown", text: "—" });
    expect(meter(5, 0).tone).toBe("unknown");
  });
  it("words key states", () => {
    expect(keyState({ state: "ok" })).toEqual({ label: "OK", tone: "pos" });
    expect(keyState({ state: "cooling" })).toEqual({ label: "Cooling", tone: "warn" });
    expect(keyState({ state: "disabled" })).toEqual({ label: "Disabled", tone: "neg" });
    expect(keyState({ state: "mystery" })).toEqual({ label: "Mystery", tone: "neutral" });
  });
  it("counts down a cooling key", () => {
    const now = Date.parse("2026-01-01T00:00:00Z");
    expect(secondsUntil("2026-01-01T00:00:42Z", now)).toBe(42);
    expect(secondsUntil("2025-12-31T23:59:00Z", now)).toBe(0);
    expect(secondsUntil(null, now)).toBeNull();
    expect(secondsUntil("nope", now)).toBeNull();
    expect([coolingText(0), coolingText(42), coolingText(200), coolingText(null)]).toEqual(["Ready again", "Ready again in 42 s", "Ready again in 4 min", null]);
  });
  it("is rules-only with no keys or when disabled, and says so", () => {
    expect(isRulesOnly(status([]))).toBe(true);
    expect(isRulesOnly(status([key("ok")], false))).toBe(true);
    expect(isRulesOnly(status([key("ok")]))).toBe(false);
    expect(poolSummary(status([]))).toBe("LLM disabled — rules only");
    expect(poolSummary(status([key("ok"), key("cooling"), key("ok")]))).toBe("2 of 3 keys ready, 1 cooling");
  });
});

describe("checkout explainer", () => {
  it("lists the saga's four steps with the same names the live checkout page uses, each with what is undone", () => {
    expect(CHECKOUT_STEPS.map((s) => s.label)).toEqual(["Checking your order", "Reserving your items", "Charging your card", "Confirming your order"]);
    for (const s of CHECKOUT_STEPS) {
      expect(s.does.length).toBeGreaterThan(20);
      expect(s.ifFails.length).toBeGreaterThan(20);
    }
  });
});
