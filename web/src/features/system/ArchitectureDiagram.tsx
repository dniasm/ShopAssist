import { useId } from "react";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { diagramLayout, wireTone, type DiagramEdge, type DiagramNode } from "./diagram";
import { latencyText, type ServiceName, type ServiceStatus } from "./health";
import styles from "./ArchitectureDiagram.module.css";

interface Props {
  status: Record<ServiceName, ServiceStatus>;
  /** Did the last request to the gateway itself work? null: not known yet. */
  gatewayUp: boolean | null;
}

const SUMMARY =
  "The browser talks to the orchestrator over REST, and the orchestrator answers over a WebSocket. The orchestrator has no database: it calls five gRPC services, " +
  "each of which owns its own PostgreSQL database. The order service also calls the catalog, inventory and payment services directly.";

/** The architecture as one SVG. Colour means status and nothing else: a service that is down turns its box, its database and the wires
 *  into it red. Wide screens draw it left to right; phones draw the same model top to bottom. */
export function ArchitectureDiagram({ status, gatewayUp }: Props) {
  const wide = useMediaQuery("(min-width: 900px)");
  const diagram = diagramLayout(wide ? "wide" : "narrow");
  const titleId = useId();
  const isDown = (s: ServiceName) => status[s].status === "down";
  // quiet wires first, so the solid ones (and their halos) sit on top where they cross
  const edges = [...diagram.edges].sort((a, b) => Number(b.kind === "s2s") - Number(a.kind === "s2s"));

  return (
    <figure className={styles.figure} data-mode={diagram.mode}>
      <svg className={styles.svg} viewBox={`0 0 ${diagram.width} ${diagram.height}`} role="img" aria-labelledby={titleId}>
        <title id={titleId}>{SUMMARY}</title>
        <defs>
          {(["normal", "bad", "quiet"] as const).map((tone) => (
            <marker key={tone} id={`${titleId}-arrow-${tone}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
              <path d="M0 1 L9 5 L0 9 z" className={styles[`head_${tone}`]} />
            </marker>
          ))}
        </defs>

        {edges.map((edge) => <Wire key={edge.id} edge={edge} tone={wireTone(edge, isDown)} markerBase={`${titleId}-arrow-`} />)}
        {diagram.nodes.map((node) => <Box key={node.id} node={node} narrow={diagram.mode === "narrow"} status={node.service ? status[node.service] : null} gatewayUp={gatewayUp} />)}
      </svg>

      <figcaption className={styles.legend}>
        <span className={styles.key}><svg width="28" height="8" aria-hidden="true"><path d="M0 4 H28" className={styles.wire_normal} /></svg> request or call</span>
        <span className={styles.key}><svg width="28" height="8" aria-hidden="true"><path d="M0 4 H28" className={styles.wire_quiet} /></svg> service to service</span>
        <span className={styles.key}><span className={`${styles.dot} ${styles.up}`} aria-hidden="true" /> up</span>
        <span className={styles.key}><span className={`${styles.dot} ${styles.down}`} aria-hidden="true" /> down</span>
      </figcaption>
    </figure>
  );
}

function Wire({ edge, tone, markerBase }: { edge: DiagramEdge; tone: "normal" | "bad" | "quiet"; markerBase: string }) {
  const marker = `url(#${markerBase}${tone})`;
  return (
    <g>
      {edge.kind === "sql" && <path d={edge.d} className={styles.halo} />}
      <path d={edge.d} className={styles[`wire_${tone}`]} markerEnd={marker} markerStart={edge.arrow === "both" ? marker : undefined} />
      {edge.label && <text x={edge.label.x} y={edge.label.y} textAnchor={edge.label.anchor} className={styles.wireLabel}>{edge.label.text}</text>}
    </g>
  );
}

function Box({ node, narrow, status, gatewayUp }: { node: DiagramNode; narrow: boolean; status: ServiceStatus | null; gatewayUp: boolean | null }) {
  const state = node.kind === "orchestrator"
    ? (gatewayUp === false ? "down" : gatewayUp === true ? "up" : "checking")
    : status?.status ?? "none";
  const down = state === "down";

  if (node.kind === "database") {
    const inner = narrow;      // on a phone the database is drawn inside its service's box
    return (
      <g data-state={down ? "down" : "ok"}>
        <rect x={node.x} y={node.y} width={node.w} height={node.h} rx={inner ? 5 : 8} className={`${styles.db} ${down ? styles.dbDown : ""}`} />
        {inner
          ? <text x={node.x + 10} y={node.y + 15} className={styles.small}>{node.title} · {node.lines.join(" ")}</text>
          : (<>
              <text x={node.x + 14} y={node.y + 24} className={styles.title}>{node.title}</text>
              <text x={node.x + 14} y={node.y + 44} className={styles.sub}>{node.lines.join(" ")}</text>
            </>)}
      </g>
    );
  }

  const statusLine = node.kind === "service"
    ? (status?.status === "up" ? `Up · ${latencyText(status.latencyMs)}` : status?.status === "down" ? "Down" : "Checking…")
    : node.kind === "orchestrator" && gatewayUp !== null ? (gatewayUp ? "Up" : "Not responding") : null;

  return (
    <g data-state={state}>
      <rect x={node.x} y={node.y} width={node.w} height={node.h} rx={8} className={`${styles.box} ${down ? styles.boxDown : ""}`} />
      <text x={node.x + 14} y={node.y + 24} className={styles.title}>{node.title}</text>
      {statusLine && node.kind === "service" && (
        <>
          <circle cx={node.x + 18} cy={node.y + 40} r={4.5} className={`${styles.dot} ${styles[state === "up" ? "up" : state === "down" ? "down" : "wait"]}`} />
          <text x={node.x + 30} y={node.y + 44} className={`${styles.sub} ${down ? styles.subDown : ""}`}>{statusLine}</text>
        </>
      )}
      {node.lines.map((line, i) => (
        <text key={line} x={node.x + 14} y={node.y + 44 + i * 18} className={styles.sub}>{line}</text>
      ))}
      {node.kind === "orchestrator" && statusLine && (
        <>
          <circle cx={node.x + node.w - 18} cy={node.y + 18} r={4.5} className={`${styles.dot} ${styles[gatewayUp ? "up" : "down"]}`} />
          <title>{`Orchestrator: ${statusLine}`}</title>
        </>
      )}
    </g>
  );
}

