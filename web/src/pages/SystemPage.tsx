import { useEffect, useState } from "react";
import { getLlmStatus, getServicesHealth } from "../api/endpoints";
import { ErrorBlock } from "../components/StateBlocks";
import { ArchitectureDiagram } from "../features/system/ArchitectureDiagram";
import { CheckoutExplainer } from "../features/system/CheckoutExplainer";
import { ageText, finding, statusMap } from "../features/system/health";
import { KeyPool } from "../features/system/KeyPool";
import { ServiceHealthTable } from "../features/system/ServiceHealthTable";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { usePolled } from "../hooks/usePolled";
import styles from "./SystemPage.module.css";

const POLL_MS = 5000;

/** /system: how the shop is wired and whether it is working right now. Everything on it is live (polled every 5 s) and needs no sign-in. */
export function SystemPage() {
  useDocumentTitle("System");
  const health = usePolled(getServicesHealth, POLL_MS);
  const llm = usePolled(getLlmStatus, POLL_MS);
  const now = useClock();

  const failed = health.error !== null;
  const found = finding(health.data, failed);
  const status = statusMap(health.data);
  const gatewayUp = health.data || failed ? !failed : null;
  const age = health.receivedAt === null ? null : Math.max(0, (now - health.receivedAt) / 1000);

  return (
    <>
      <div className="title-row">
        <div>
          <h1>System</h1>
          <p className={`sub ${styles.finding}`} data-tone={found.tone} role="status" aria-live="polite">{found.text}</p>
        </div>
        <div className={styles.actions}>
          <span className={styles.stamp} aria-live="off">
            {failed && health.data ? `Couldn’t refresh. Last good check ${age === null ? "" : ageText(age)}` : age === null ? "" : `Checked ${ageText(age)}`}
          </span>
          <button type="button" className="btn" onClick={() => { health.refresh(); llm.refresh(); }} disabled={health.refreshing}>
            {health.refreshing ? "Checking…" : "Check now"}
          </button>
        </div>
      </div>

      <div className={styles.blocks}>
        <section className={styles.panel} aria-labelledby="arch-title">
          <header className={styles.head}>
            <h2 id="arch-title" className="section-title">Where a request goes</h2>
            <p className="muted small">Each service owns its own database. The orchestrator owns none.</p>
          </header>
          <ArchitectureDiagram status={status} gatewayUp={gatewayUp} />
        </section>

        <section className={styles.panel} aria-labelledby="svc-title">
          <header className={styles.head}>
            <h2 id="svc-title" className="section-title">Service health</h2>
            <p className="muted small">One short gRPC health call per service, every 5 seconds.</p>
          </header>
          {failed && !health.data
            ? <ErrorBlock error={health.error} what="the service health" onRetry={health.refresh} />
            : <ServiceHealthTable data={health.data} />}
        </section>

        <section className={styles.panel} aria-labelledby="keys-title">
          <header className={styles.head}>
            <h2 id="keys-title" className="section-title">Groq key pool</h2>
            <p className="muted small">The language model that reads questions. Keys are shown masked, as the API sends them.</p>
          </header>
          {llm.data
            ? <KeyPool status={llm.data} />
            : llm.error !== null
              ? <ErrorBlock error={llm.error} what="the key pool" onRetry={llm.refresh} />
              : <p className="muted" aria-busy="true">Loading{"…"}</p>}
        </section>

        <section className={styles.panel} aria-labelledby="flow-title">
          <header className={styles.head}>
            <h2 id="flow-title" className="section-title">What happens when you check out</h2>
            <p className="muted small">Four steps, each one able to undo the ones before it.</p>
          </header>
          <CheckoutExplainer />
        </section>
      </div>
    </>
  );
}

/** Re-renders every second so "checked 4 s ago" stays true between polls. */
function useClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  return now;
}
