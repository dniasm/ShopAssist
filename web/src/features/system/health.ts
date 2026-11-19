import type { ServicesHealth } from "../../api/types";

/** The five gRPC services in the order the diagram stacks them (Order sits in the middle: it calls the three around it). */
export const SERVICE_ORDER = ["catalog", "inventory", "order", "payment", "customer"] as const;
export type ServiceName = (typeof SERVICE_ORDER)[number];

export const SERVICE_TITLES: Record<ServiceName, string> = {
  catalog: "Catalog service",
  inventory: "Inventory service",
  order: "Order service",
  payment: "Payment service",
  customer: "Customer service",
};

/** One line on what each service owns, for the health table. */
export const SERVICE_ROLES: Record<ServiceName, string> = {
  catalog: "Products, SKUs and search",
  inventory: "Stock and reservations",
  order: "Orders, checkout saga, fulfilment",
  payment: "Simulated charges and refunds",
  customer: "Customers and carts",
};

/** Each service's own database (the order service's is called "orders"). */
export const SERVICE_DATABASES: Record<ServiceName, string> = {
  catalog: "catalog",
  inventory: "inventory",
  order: "orders",
  payment: "payment",
  customer: "customer",
};

export function isServiceName(value: string): value is ServiceName {
  return SERVICE_ORDER.some((s) => s === value);
}

export type NodeStatus = "up" | "down" | "checking";
export interface ServiceStatus { status: NodeStatus; latencyMs: number | null; error: string | null }

/** Every known service's status from one health response. `null` (nothing received yet) means "checking" for all of them. */
export function statusMap(data: ServicesHealth | null): Record<ServiceName, ServiceStatus> {
  const blank = (): ServiceStatus => ({ status: "checking", latencyMs: null, error: null });
  const map: Record<ServiceName, ServiceStatus> = { catalog: blank(), inventory: blank(), order: blank(), payment: blank(), customer: blank() };
  if (!data) return map;
  for (const s of data.services) {
    if (!isServiceName(s.name)) continue;
    map[s.name] = { status: s.status === "up" ? "up" : "down", latencyMs: s.latency_ms, error: s.error };
  }
  return map;
}

export type FindingTone = "ok" | "bad" | "wait";

/** The one sentence under the title. `apiFailed`: the last request to the gateway itself failed. */
export function finding(data: ServicesHealth | null, apiFailed: boolean): { text: string; tone: FindingTone } {
  if (apiFailed && !data) return { text: "The API is not responding, so the services cannot be checked.", tone: "bad" };
  if (!data) return { text: "Checking the services…", tone: "wait" };
  const down = SERVICE_ORDER.filter((s) => statusMap(data)[s].status === "down");
  const total = data.services.length;
  if (down.length === 0 && data.all_up) return { text: total === 0 ? "No services reported." : `All ${total} services are up.`, tone: total === 0 ? "wait" : "ok" };
  if (down.length === 0) return { text: "A service is not answering.", tone: "bad" };
  const names = down.map((s) => SERVICE_TITLES[s].replace(" service", ""));
  return { text: down.length === 1 ? `The ${SERVICE_TITLES[down[0] as ServiceName].toLowerCase()} is down.` : `${down.length} services are down: ${names.join(", ")}.`, tone: "bad" };
}

/** "just now", "4 s ago", "2 min ago". */
export function ageText(seconds: number): string {
  if (seconds < 2) return "just now";
  if (seconds < 90) return `${Math.floor(seconds)} s ago`;
  return `${Math.round(seconds / 60)} min ago`;
}

export function latencyText(ms: number | null): string {
  return ms === null ? "—" : `${ms.toLocaleString("en-US")} ms`;
}
