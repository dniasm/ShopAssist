import { Link, useParams } from "react-router";
import { ApiError } from "../api/errors";
import { EmptyState, ErrorBlock } from "../components/StateBlocks";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useProduct } from "../hooks/useProduct";
import { ProductView } from "../features/product/ProductView";
import styles from "./ProductPage.module.css";

export function ProductPage() {
  const { productId = "" } = useParams();
  const state = useProduct(productId);
  useDocumentTitle(state.status === "ready" ? state.data.product.name : state.status === "error" ? "Product" : "Loading…");

  if (state.status === "loading") return <ProductSkeleton />;

  if (state.status === "error") {
    if (state.error instanceof ApiError && state.error.isNotFound) {
      return (
        <EmptyState title="That product doesn't exist" action={<Link to="/shop" className="btn primary">Browse the shop</Link>}>
          It may have been removed, or the link is mistyped.
        </EmptyState>
      );
    }
    return <ErrorBlock error={state.error} what="this product" onRetry={state.reload} />;
  }

  // key: a different product must start with fresh quantity state
  return <ProductView key={state.data.product.product_id} detail={state.data} />;
}

function ProductSkeleton() {
  return (
    <div className={styles.skeleton} aria-busy="true" aria-label="Loading product">
      <span className="skel" style={{ width: 220 }} />
      <div className={styles.skelGrid}>
        <div className={styles.skelTile} />
        <div className={styles.skelDetails}>
          <span className="skel" style={{ width: "70%", height: 28 }} />
          <span className="skel" style={{ width: "35%" }} />
          <span className="skel" style={{ width: "25%", height: 32 }} />
          <span className="skel" style={{ width: "90%" }} />
          <span className="skel" style={{ width: "80%" }} />
          <span className="skel" style={{ width: 160, height: 32, marginTop: 12 }} />
          <span className="skel" style={{ width: 280, height: 40 }} />
        </div>
      </div>
    </div>
  );
}
