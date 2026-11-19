import type { ServicesHealth } from "../../api/types";
import { SERVICE_ORDER, SERVICE_ROLES, SERVICE_TITLES, isServiceName, latencyText, statusMap } from "./health";
import styles from "./ServiceHealthTable.module.css";

/** One row per service: its status, how long the health call took, and where it listens. Down rows say why. */
export function ServiceHealthTable({ data }: { data: ServicesHealth | null }) {
  const status = statusMap(data);
  const extra = (data?.services ?? []).filter((s) => !isServiceName(s.name));
  const rows = [
    ...SERVICE_ORDER.map((name) => ({ key: name, title: SERVICE_TITLES[name], role: SERVICE_ROLES[name], ...status[name], address: data?.services.find((s) => s.name === name)?.address ?? null })),
    ...extra.map((s) => ({ key: s.name, title: s.name, role: s.service, status: s.status === "up" ? "up" as const : "down" as const, latencyMs: s.latency_ms, error: s.error, address: s.address })),
  ];
  return (
    <div className={styles.wrap}>
      <table className={styles.table}>
        <thead>
          <tr><th>Service</th><th>Status</th><th className={styles.n}>Latency</th><th>Address</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} data-state={r.status}>
              <td data-label="Service">
                <span className={styles.name}>{r.title}</span>
                <span className="muted small">{r.role}</span>
              </td>
              <td data-label="Status">
                <span className={`pill ${r.status === "up" ? "pos" : r.status === "down" ? "neg" : ""}`}>
                  <span className={`${styles.dot} ${styles[r.status]}`} aria-hidden="true" />
                  {r.status === "up" ? "Up" : r.status === "down" ? "Down" : "Checking…"}
                </span>
                {r.status === "down" && r.error && <span className={styles.error}>{r.error}</span>}
              </td>
              <td data-label="Latency" className={styles.n}>{latencyText(r.latencyMs)}</td>
              <td data-label="Address">{r.address ? <code>{r.address}</code> : <span className="muted">{"—"}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
