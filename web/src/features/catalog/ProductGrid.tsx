import type { ProductCard as ProductCardData } from "../../api/types";
import { ProductCard, ProductCardSkeleton } from "./ProductCard";
import styles from "./ProductGrid.module.css";

export function ProductGrid({ products, label, busy = false }: { products: ProductCardData[]; label: string; busy?: boolean }) {
  return (
    <ul className={styles.grid} aria-label={label} aria-busy={busy} data-stale={busy}>
      {products.map((p) => <ProductCard key={p.product_id} product={p} />)}
    </ul>
  );
}

export function ProductGridSkeleton({ count = 12 }: { count?: number }) {
  return (
    <ul className={styles.grid} aria-label="Loading products" aria-busy="true">
      {Array.from({ length: count }, (_v, i) => <ProductCardSkeleton key={i} />)}
    </ul>
  );
}
