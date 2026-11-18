import { Link } from "react-router";
import { formatMoney } from "../../api/money";
import type { ProductCard as ProductCardData } from "../../api/types";
import { StarIcon } from "../../components/Icon";
import { ProductTile } from "../tile/ProductTile";
import { colorLabel } from "./labels";
import styles from "./ProductCard.module.css";

/** One product in a grid. The whole card is one link; the drawn tile is decorative because the name sits beside it. */
export function ProductCard({ product }: { product: ProductCardData }) {
  const firstColor = product.colors[0];
  return (
    <li className={styles.item}>
      <Link to={`/p/${encodeURIComponent(product.product_id)}`} className={styles.card}>
        <ProductTile productId={product.product_id} category={product.category} color={firstColor} />
        <div className={styles.info}>
          <h3 className={styles.name} title={product.name}>{product.name}</h3>
          <p className={`${styles.meta} muted`}>
            {product.brand}
            {product.colors.length > 1 && ` · ${product.colors.length} colours`}
            {product.colors.length === 1 && firstColor && ` · ${colorLabel(firstColor)}`}
          </p>
          <div className={styles.foot}>
            <span className={styles.price}>{formatMoney(product.price_cents)}</span>
            <Rating value={product.rating} count={product.rating_count} />
          </div>
          {!product.in_stock && <span className="pill">Sold out</span>}
        </div>
      </Link>
    </li>
  );
}

export function Rating({ value, count }: { value: number; count: number }) {
  if (count === 0) return <span className="muted small">No ratings</span>;
  return (
    <span className={styles.rating} aria-label={`Rated ${value.toFixed(1)} out of 5 from ${count} ratings`}>
      <StarIcon /> <span>{value.toFixed(1)}</span> <span className="muted">({count.toLocaleString("en-US")})</span>
    </span>
  );
}

export function ProductCardSkeleton() {
  return (
    <li className={styles.item} aria-hidden="true">
      <div className={styles.card}>
        <div className={styles.skelTile} />
        <div className={styles.info}>
          <span className="skel" style={{ width: "85%", height: 16 }} />
          <span className="skel" style={{ width: "55%" }} />
          <span className="skel" style={{ width: "40%", height: 18, marginTop: 6 }} />
        </div>
      </div>
    </li>
  );
}
