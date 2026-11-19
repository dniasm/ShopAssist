/* The architecture diagram as data: where every box and wire goes, for a wide (desktop, left to right) and a narrow (phone, top to
 * bottom) layout. Pure geometry, no React: the SVG component only draws what this returns, and the tests check the model.
 *
 *   Browser --REST--> Orchestrator --gRPC--> five services, each with its own database
 *            <--WebSocket--   (no database)        Order --gRPC--> Catalog, Inventory, Payment (service to service)
 */

import { SERVICE_DATABASES, SERVICE_ORDER, SERVICE_TITLES, type ServiceName } from "./health";

export type DiagramMode = "wide" | "narrow";
export type NodeKind = "browser" | "orchestrator" | "service" | "database";
export type EdgeKind = "rest" | "ws" | "grpc" | "sql" | "s2s";

export interface DiagramNode {
  id: string;
  kind: NodeKind;
  x: number; y: number; w: number; h: number;
  title: string;
  /** Plain lines under the title (a service's status line is added by the component). */
  lines: string[];
  service?: ServiceName;
}

export interface DiagramEdge {
  id: string;
  kind: EdgeKind;
  /** SVG path, drawn from the sender to the receiver. */
  d: string;
  /** Where the arrowhead goes. */
  arrow: "end" | "both";
  label?: { text: string; x: number; y: number; anchor: "start" | "middle" | "end" };
  /** Services at either end: if one of them is down, the wire turns red. */
  involves: ServiceName[];
}

export interface Diagram { mode: DiagramMode; width: number; height: number; nodes: DiagramNode[]; edges: DiagramEdge[] }

/** Order calls these three directly (it prices from Catalog, reserves in Inventory, charges through Payment). */
export const SERVICE_TO_SERVICE: readonly ServiceName[] = ["catalog", "inventory", "payment"];

const nodeId = (s: ServiceName) => `svc-${s}`;
const dbId = (s: ServiceName) => `db-${s}`;

interface Row { y: number; cy: number }
const rowAt = (i: number, top: number, pitch: number, h: number): Row => ({ y: top + i * pitch, cy: top + i * pitch + h / 2 });

/** One row per service, stacked in SERVICE_ORDER (catalog, inventory, order, payment, customer). */
function rowsFor(top: number, pitch: number, h: number): Record<ServiceName, Row> {
  const at = (s: ServiceName) => rowAt(SERVICE_ORDER.indexOf(s), top, pitch, h);
  return { catalog: at("catalog"), inventory: at("inventory"), order: at("order"), payment: at("payment"), customer: at("customer") };
}

/** The three Order -> other service arrows as elbow paths in lanes beside the service column, entering the target's right edge. */
function serviceToService(rightEdge: number, laneStart: number, laneGap: number, rows: Record<ServiceName, Row>, h: number): DiagramEdge[] {
  const orderCy = rows.order.cy;
  return SERVICE_TO_SERVICE.map((target, i): DiagramEdge => {
    const lane = laneStart + i * laneGap;
    const above = rows[target].cy < orderCy;
    const exitY = orderCy + (i - 1) * 14;               // three distinct exit points on the Order box
    const entryY = rows[target].cy + (above ? h / 4 : -h / 4);   // enter on the side facing Order, away from the database wire at the centre
    return { id: `s2s-order-${target}`, kind: "s2s", d: `M${rightEdge} ${exitY} H${lane} V${entryY} H${rightEdge}`, arrow: "end", involves: ["order", target] };
  });
}

function wide(): Diagram {
  const W = 1000, top = 24, pitch = 76, h = 56;
  const rows = rowsFor(top, pitch, h);
  const mid = rows.order.cy;
  const svcX = 480, svcW = 250, dbX = 830, dbW = 170, trunk = 445;
  const nodes: DiagramNode[] = [
    { id: "browser", kind: "browser", x: 0, y: mid - 32, w: 120, h: 64, title: "Browser", lines: ["React app"] },
    { id: "orchestrator", kind: "orchestrator", x: 240, y: mid - 60, w: 170, h: 120, title: "Orchestrator", lines: ["REST + WebSocket", "No database.", "Every read is a gRPC call."] },
  ];
  const edges: DiagramEdge[] = [
    { id: "rest", kind: "rest", d: `M120 ${mid - 12} H240`, arrow: "end", involves: [], label: { text: "REST", x: 180, y: mid - 20, anchor: "middle" } },
    { id: "ws", kind: "ws", d: `M240 ${mid + 14} H120`, arrow: "end", involves: [], label: { text: "WebSocket", x: 180, y: mid + 34, anchor: "middle" } },
  ];
  for (const s of SERVICE_ORDER) {
    const r = rows[s];
    nodes.push({ id: nodeId(s), kind: "service", x: svcX, y: r.y, w: svcW, h, title: SERVICE_TITLES[s], lines: [], service: s });
    nodes.push({ id: dbId(s), kind: "database", x: dbX, y: r.y, w: dbW, h, title: `${SERVICE_DATABASES[s]} db`, lines: ["PostgreSQL"], service: s });
    edges.push({ id: `grpc-${s}`, kind: "grpc", d: `M410 ${mid} H${trunk} V${r.cy} H${svcX}`, arrow: "end", involves: [s], ...(s === "catalog" ? { label: { text: "gRPC", x: trunk - 6, y: r.cy - 14, anchor: "middle" as const } } : {}) });
    edges.push({ id: `sql-${s}`, kind: "sql", d: `M${svcX + svcW} ${r.cy} H${dbX}`, arrow: "both", involves: [s], ...(s === "catalog" ? { label: { text: "SQL", x: (svcX + svcW + dbX) / 2 + 22, y: r.cy - 8, anchor: "middle" as const } } : {}) });
  }
  edges.push(...serviceToService(svcX + svcW, svcX + svcW + 14, 14, rows, h));
  return { mode: "wide", width: W, height: top + 4 * pitch + h + top, nodes, edges };
}

function narrow(): Diagram {
  const W = 343, h = 84, pitch = 96, top = 204;
  const rows = rowsFor(top, pitch, h);
  const x = 40, w = 263, trunk = 14;
  const nodes: DiagramNode[] = [
    { id: "browser", kind: "browser", x, y: 0, w, h: 48, title: "Browser", lines: ["React app"] },
    { id: "orchestrator", kind: "orchestrator", x, y: 96, w, h: 72, title: "Orchestrator", lines: ["REST + WebSocket. No database:", "every read is a gRPC call."] },
  ];
  const edges: DiagramEdge[] = [
    { id: "rest", kind: "rest", d: "M130 48 V96", arrow: "end", involves: [], label: { text: "REST", x: 122, y: 76, anchor: "end" } },
    { id: "ws", kind: "ws", d: "M213 96 V48", arrow: "end", involves: [], label: { text: "WebSocket", x: 221, y: 76, anchor: "start" } },
  ];
  for (const s of SERVICE_ORDER) {
    const r = rows[s];
    nodes.push({ id: nodeId(s), kind: "service", x, y: r.y, w, h, title: SERVICE_TITLES[s], lines: [], service: s });
    // the database sits inside the service box: owned, not shared
    nodes.push({ id: dbId(s), kind: "database", x: x + 12, y: r.y + h - 32, w: w - 24, h: 22, title: `${SERVICE_DATABASES[s]} db`, lines: ["PostgreSQL"], service: s });
    edges.push({ id: `grpc-${s}`, kind: "grpc", d: `M${x} 132 H${trunk} V${r.cy} H${x}`, arrow: "end", involves: [s], ...(s === "catalog" ? { label: { text: "gRPC", x: trunk + 4, y: 190, anchor: "start" as const } } : {}) });
  }
  edges.push(...serviceToService(x + w, x + w + 12, 10, rows, h));
  return { mode: "narrow", width: W, height: top + 4 * pitch + h + 8, nodes, edges };
}

export function diagramLayout(mode: DiagramMode): Diagram {
  return mode === "wide" ? wide() : narrow();
}

export type WireTone = "normal" | "bad" | "quiet";

/** A wire is red when a service at either end is down; service-to-service calls are drawn quieter (dashed) when healthy. */
export function wireTone(edge: Pick<DiagramEdge, "kind" | "involves">, isDown: (s: ServiceName) => boolean): WireTone {
  if (edge.involves.some(isDown)) return "bad";
  return edge.kind === "s2s" ? "quiet" : "normal";
}
