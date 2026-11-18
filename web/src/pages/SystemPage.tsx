import { EmptyState } from "../components/StateBlocks";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

/** Placeholder. The v1 System page (service map, health, request route) is ported in a later 5.x step. */
export function SystemPage() {
  useDocumentTitle("System");
  return (
    <>
      <div className="title-row">
        <div>
          <h1>System</h1>
          <p className="sub">How the shop is wired: the gateway, the five services and their databases.</p>
        </div>
      </div>
      <EmptyState title="The system view is not ported yet">
        The live service map and health checks from the first version move here in a later step.
      </EmptyState>
    </>
  );
}
