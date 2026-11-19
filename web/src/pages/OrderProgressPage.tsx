import { useEffect } from "react";
import { Link, useParams } from "react-router";
import { EmptyState } from "../components/StateBlocks";
import { useAuth } from "../features/auth/AuthContext";
import { colorLabel, sizeLabel } from "../features/catalog/labels";
import { useCart } from "../features/cart/CartContext";
import { InterruptedNotice } from "../features/checkout/InterruptedNotice";
import { FailurePanel, SuccessPanel } from "../features/checkout/OrderOutcome";
import { SagaView } from "../features/checkout/SagaView";
import { useCheckoutProgress } from "../hooks/useCheckoutProgress";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import styles from "./OrderProgressPage.module.css";

/** /checkout/:orderId. Everything on it is rebuilt from the order id in the URL and the socket's replay, so a refresh in the
 *  middle of a checkout (or after it) shows the same steps and the same result. */
export function OrderProgressPage() {
  const { orderId = "" } = useParams();
  const { user, openSignIn } = useAuth();
  useDocumentTitle(`Order ${orderId}`);

  if (!user) {
    return (
      <>
        <div className="title-row"><h1>Order {orderId}</h1></div>
        <EmptyState title="Sign in to see this order" action={<button type="button" className="btn primary" onClick={() => openSignIn()}>Sign in</button>}>
          Orders belong to the customer who placed them.
        </EmptyState>
      </>
    );
  }
  // key: another order id starts from an empty saga
  return <OrderProgress key={orderId} orderId={orderId} />;
}

function OrderProgress({ orderId }: { orderId: string }) {
  const { saga, socketStatus, order, checkStatus, checking, lastCheck } = useCheckoutProgress(orderId);
  const { cart, refresh } = useCart();
  const outcome = saga.outcome;

  // The saga's end changes the cart (cleared on success, intact on failure): show the truth.
  const kind = outcome?.kind;
  useEffect(() => { if (kind) void refresh(); }, [kind, refresh]);

  const nameOf = (skuId: string): string => {
    const line = order?.lines.find((l) => l.sku_id === skuId) ?? cart?.lines.find((l) => l.sku_id === skuId);
    return line ? `${line.name} (${sizeLabel(line.size)}, ${colorLabel(line.color)})` : skuId;
  };

  const inDoubt = !outcome && saga.interrupted !== null;
  const heading = inDoubt ? "Waiting for confirmation" : !outcome ? "Placing your order" : outcome.kind === "success" ? "Order confirmed" : "Order not completed";
  const reconnecting = !outcome && socketStatus !== "open";

  return (
    <>
      <div className="title-row">
        <div>
          <h1>{heading}</h1>
          <p className="sub">Order <code className={styles.id}>{orderId}</code></p>
        </div>
      </div>

      <div className={styles.layout}>
        <section className={styles.progress} aria-labelledby="progress-title">
          <h2 id="progress-title" className="section-title">Progress</h2>
          <SagaView saga={saga} paused={inDoubt} />
          <p className={`${styles.waiting} muted`} role="status" aria-live="polite">
            {outcome || inDoubt ? "" : saga.applied === 0 ? (reconnecting ? "Reconnecting to get the latest updates…" : "Waiting for the first update…") : reconnecting ? "Connection lost. Reconnecting; you won’t miss any steps." : ""}
          </p>
        </section>

        <div className={styles.result}>
          {outcome?.kind === "success" && <SuccessPanel order={outcome.order} />}
          {outcome?.kind === "failed" && <FailurePanel failure={outcome.failure} nameOf={nameOf} />}
          {inDoubt && saga.interrupted && (
            <InterruptedNotice message={saga.interrupted.message} checking={checking} lastCheck={lastCheck} onCheck={() => void checkStatus()} />
          )}
          {!outcome && !inDoubt && (
            <p className="muted">
              This page updates by itself. You can refresh it or come back to this address: the result will be here.{" "}
              <Link to="/shop" className="link">Keep browsing</Link>
            </p>
          )}
        </div>
      </div>
    </>
  );
}
