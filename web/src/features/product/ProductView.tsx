import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { formatMoney } from "../../api/money";
import type { ProductDetail } from "../../api/types";
import { colorLabel, slugLabel } from "../catalog/labels";
import { Rating } from "../catalog/ProductCard";
import { shopHref } from "../catalog/query";
import { ProductTile } from "../tile/ProductTile";
import { AddToCartButton } from "./AddToCartButton";
import { QuantityStepper } from "./QuantityStepper";
import { ColorPicker, SizePicker } from "./VariantPickers";
import {
  availabilityText, clampQuantity, colorOptions, initialSelection, resolveSelection, sizeOptions, validSize, type Selection,
} from "./variants";
import styles from "./ProductView.module.css";

/** The product page body. The selected colour and size live in the URL (?color=navy&size=M, replaced rather than pushed),
 *  so a link to a variant opens on that variant; everything else is derived from them and the stock array. */
export function ProductView({ detail }: { detail: ProductDetail }) {
  const { product, stock } = detail;
  const [params, setParams] = useSearchParams();
  const [wantedQuantity, setWantedQuantity] = useState(1);

  const selection = useMemo(
    () => initialSelection(product, stock, { color: params.get("color") ?? undefined, size: params.get("size") ?? undefined }),
    [product, stock, params],
  );
  const colors = useMemo(() => colorOptions(product, stock), [product, stock]);
  const sizes = useMemo(() => (selection.color ? sizeOptions(product, stock, selection.color) : []), [product, stock, selection.color]);
  const resolved = resolveSelection(stock, selection);
  const quantity = clampQuantity(wantedQuantity, resolved?.maxQuantity ?? 1);
  const colorSoldOut = colors.find((c) => c.color === selection.color)?.availability === "sold_out";
  const singleSize = product.sizes.length === 1;

  function select(next: Selection) {
    const p = new URLSearchParams();
    if (next.color) p.set("color", next.color);
    if (next.size) p.set("size", next.size);
    setParams(p, { replace: true });
  }
  const pickColor = (color: string) => select({ color, size: validSize(product, stock, color, selection.size) });
  const pickSize = (size: string) => select({ color: selection.color, size });

  const tileLabel = `${selection.color ? colorLabel(selection.color) + " " : ""}${product.name}`;

  return (
    <article className={styles.view}>
      <nav className={styles.crumbs} aria-label="Breadcrumb">
        <ol>
          <li><Link to="/shop" className="link">Shop</Link></li>
          <li><Link to={shopHref({ category: [product.category] })} className="link">{slugLabel(product.category)}</Link></li>
          <li aria-current="page">{product.name}</li>
        </ol>
      </nav>

      <div className={styles.grid}>
        <div className={styles.media}>
          <ProductTile productId={product.product_id} category={product.category} color={selection.color ?? product.colors[0]} label={tileLabel} />
        </div>

        <div className={styles.details}>
          <header>
            <h1 className={styles.name}>{product.name}</h1>
            <p className="muted">
              <Link to={shopHref({ brand: [product.brand] })} className="link">{product.brand}</Link>
              {" · "}{slugLabel(product.category)}
            </p>
          </header>

          <div className={styles.priceRow}>
            <span className={styles.price}>{formatMoney(product.price_cents)}</span>
            <Rating value={product.rating} count={product.rating_count} />
          </div>

          <p className={styles.description}>{product.description}</p>

          {product.colors.length > 0 && <ColorPicker options={colors} value={selection.color} onChange={pickColor} />}
          {!singleSize && <SizePicker options={sizes} value={selection.size} onChange={pickSize} />}

          <p className={styles.status} role="status" aria-live="polite">
            <Availability colorSoldOut={colorSoldOut} color={selection.color} resolved={resolved} needsSize={!resolved && !colorSoldOut} />
          </p>

          <div className={styles.buy}>
            <div className={styles.qty}>
              <span className={styles.qtyLabel}>Quantity</span>
              <QuantityStepper
                value={quantity}
                max={resolved?.maxQuantity ?? 1}
                disabled={!resolved || resolved.available === 0}
                onChange={setWantedQuantity}
              />
            </div>
            <AddToCartButton />
          </div>

          {resolved && <p className={`${styles.sku} muted small`}><code>{resolved.skuId}</code></p>}
        </div>
      </div>
    </article>
  );
}

function Availability({ resolved, colorSoldOut, color, needsSize }: {
  resolved: ReturnType<typeof resolveSelection>; colorSoldOut: boolean; color: string | null; needsSize: boolean;
}) {
  if (resolved) {
    const text = availabilityText(resolved.availability, resolved.available);
    const tone = resolved.availability === "in_stock" ? "pos" : resolved.availability === "low" ? "warn" : "neg";
    return <span className={`pill ${tone}`}>{text}</span>;
  }
  if (colorSoldOut) return <span className="pill neg">{color ? `${colorLabel(color)} is sold out` : "Sold out"}</span>;
  if (needsSize) return <span className="muted">Select a size to see availability.</span>;
  return null;
}
