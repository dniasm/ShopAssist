import { Link } from "react-router";
import { formatMoney } from "../../api/money";
import { StatusPill } from "../orders/StatusPill";
import { describeFailure, sagaRows } from "../checkout/saga";
import { SagaView } from "../checkout/SagaView";
import { useChat } from "./ChatContext";
import { useLeaveChat } from "./ChatCards";
import { confirmLabel, proposalDetails } from "./trace";
import type { ChatProposal } from "./transcript";
import styles from "./ProposalCard.module.css";

/** The assistant never spends money or cancels anything itself: it proposes, and this card is where the shopper decides. It can be
 *  decided once. After Confirm it follows the real result on the normal checkout / order events and shows it right here. */
export function ProposalCard({ proposal }: { proposal: ChatProposal }) {
  const { confirm, dismiss } = useChat();
  const details = proposalDetails(proposal.action, proposal.args);
  const decided = proposal.state !== "pending" && proposal.state !== "confirming";

  return (
    <section className={styles.card} data-state={proposal.state} aria-label="Needs your confirmation">
      <p className={styles.summary}>{proposal.summary}</p>
      {details.length > 0 && <ul className={styles.details}>{details.map((d) => <li key={d}>{d}</li>)}</ul>}

      {!decided && (
        <div className={styles.actions}>
          <button type="button" className="btn primary" aria-disabled={proposal.state === "confirming"} onClick={() => { if (proposal.state === "pending") confirm(proposal.id); }}>
            {proposal.state === "confirming" ? "Working\u2026" : confirmLabel(proposal.action)}
          </button>
          <button type="button" className="btn" aria-disabled={proposal.state === "confirming"} onClick={() => { if (proposal.state === "pending") dismiss(proposal.id); }}>Dismiss</button>
        </div>
      )}
      {proposal.note && <p className={styles.note} role="status">{proposal.note}</p>}
      {proposal.state === "dismissed" && <p className="muted small">Dismissed. Nothing happened.</p>}
      {proposal.state === "confirmed" && <Result proposal={proposal} />}
    </section>
  );
}

/** What happened after Confirm, from the checkout steps or the order's status. */
function Result({ proposal }: { proposal: ChatProposal }) {
  const leave = useLeaveChat();
  const follow = proposal.follow;
  if (!follow) return <p className="muted small" role="status">Confirmed.</p>;

  if (follow.kind === "order") {
    return (
      <p className={styles.result} role="status">
        {follow.status === null && "Confirmed. Cancelling\u2026"}
        {follow.status === "CANCELLED" && <>Order {follow.orderId} is cancelled and refunded. <Link to={`/orders/${encodeURIComponent(follow.orderId)}`} className="link" onClick={leave}>View the order</Link></>}
        {follow.status !== null && follow.status !== "CANCELLED" && <><StatusPill status={follow.status} /> {follow.message}{" "}<Link to={`/orders/${encodeURIComponent(follow.orderId)}`} className="link" onClick={leave}>View the order</Link></>}
      </p>
    );
  }

  if (!follow.orderId || !follow.saga) return <p className="muted small" role="status">Confirmed. Starting your order{"\u2026"}</p>;
  const saga = follow.saga;
  const outcome = saga.outcome;
  return (
    <div className={styles.saga}>
      <SagaView saga={saga} paused={saga.interrupted !== null && !outcome} />
      <p className={styles.result} role="status">
        {outcome?.kind === "success" && <>Order {outcome.order.order_id} is confirmed ({formatMoney(outcome.order.total_cents)}). <Link to={`/orders/${encodeURIComponent(outcome.order.order_id)}`} className="link" onClick={leave}>Track this order</Link></>}
        {outcome?.kind === "failed" && <><strong>{describeFailure(outcome.failure, (sku) => sku).title}.</strong> {describeFailure(outcome.failure, (sku) => sku).reassurance}</>}
        {!outcome && saga.interrupted && <>{saga.interrupted.message} <Link to={`/checkout/${encodeURIComponent(follow.orderId)}`} className="link" onClick={leave}>Watch it here</Link></>}
        {!outcome && !saga.interrupted && sagaRows(saga).some((r) => r.status === "active") && "Placing your order\u2026"}
      </p>
    </div>
  );
}
