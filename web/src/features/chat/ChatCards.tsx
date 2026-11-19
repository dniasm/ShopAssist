import { useCallback } from "react";
import { Link } from "react-router";
import { formatMoney } from "../../api/money";
import { StarIcon } from "../../components/Icon";
import { useDrawer } from "../drawer/DrawerContext";
import { colorLabel, sizeLabel, slugLabel } from "../catalog/labels";
import { formatDate } from "../orders/format";
import { StatusPill } from "../orders/StatusPill";
import { asOrderStatus } from "../orders/timeline";
import { colorHex } from "../tile/colors";
import { ProductTile } from "../tile/ProductTile";
import { LOW_STOCK_BELOW } from "../product/variants";
import type { CardView, CartCardView, OrderCardView, ProductCardView, StockView } from "./cards";
import { SignInCard } from "./SignInCard";
import styles from "./ChatCards.module.css";

/** On a phone the chat covers the page, so following a link out of it closes it; on a wide screen it stays beside the page. */
export function useLeaveChat(): () => void {
  const { close } = useDrawer();
  return useCallback(() => { if (window.matchMedia("(max-width: 720px)").matches) close(); }, [close]);
}

export function ChatCardView({ card }: { card: CardView }) {
  switch (card.kind) {
    case "products": return <ProductsCard items={card.items} />;
    case "product": return <SingleProduct item={card.item} description={card.description} />;
    case "stock": return <StockCard stock={card.stock} />;
    case "cart": return <CartCard cart={card.cart} />;
    case "order": return <OrdersCard orders={[card.order]} />;
    case "orders": return <OrdersCard orders={card.orders} />;
    case "signin": return <SignInCard />;
  }
}

function ProductsCard({ items }: { items: ProductCardView[] }) {
  const leave = useLeaveChat();
  return (
    <ul className={styles.products} aria-label="Products">
      {items.map((p) => (
        <li key={p.productId}>
          <Link to={`/p/${encodeURIComponent(p.productId)}`} className={styles.product} onClick={leave}>
            <span className={styles.tile} aria-hidden="true"><ProductTile productId={p.productId} category={p.category} color={p.color ?? undefined} /></span>
            <span className={styles.productText}>
              <span className={styles.productName}>{p.name}</span>
              <span className={styles.productMeta}>
                <span className={styles.price}>{formatMoney(p.priceCents)}</span>
                {p.rating !== null && <span className="muted small"><StarIcon /> {p.rating.toFixed(1)}</span>}
                {p.inStock === false && <span className="pill">Sold out</span>}
              </span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function SingleProduct({ item, description }: { item: ProductCardView; description: string }) {
  const leave = useLeaveChat();
  return (
    <div className={styles.single}>
      <span className={styles.tileLarge} aria-hidden="true"><ProductTile productId={item.productId} category={item.category} color={item.color ?? undefined} /></span>
      <div className={styles.singleText}>
        <Link to={`/p/${encodeURIComponent(item.productId)}`} className={styles.productName} onClick={leave}>{item.name}</Link>
        <span className="muted small">{item.brand}{item.category ? ` \u00B7 ${slugLabel(item.category)}` : ""}</span>
        <span className={styles.price}>{formatMoney(item.priceCents)}</span>
        {description && <p className={`${styles.desc} muted`}>{description}</p>}
        <Link to={`/p/${encodeURIComponent(item.productId)}`} className="link small" onClick={leave}>Open the product page</Link>
      </div>
    </div>
  );
}

/** Colours down the side, sizes across: what is left of each. Sold out is struck through; under 5 says how many. */
function StockCard({ stock }: { stock: StockView }) {
  return (
    <div className={styles.stock} role="group" aria-label="Stock by colour and size">
      {stock.colors.map((c) => (
        <div key={c.color} className={styles.stockRow}>
          <span className={styles.stockColor}>
            <span className={styles.swatch} style={{ background: colorHex(c.color) }} aria-hidden="true" />
            {colorLabel(c.color)}
          </span>
          <ul className={styles.sizes}>
            {c.sizes.map((s) => {
              const low = s.available > 0 && s.available < LOW_STOCK_BELOW;
              const label = s.available === 0 ? "sold out" : low ? `only ${s.available} left` : `${s.available} in stock`;
              return (
                <li key={`${s.skuId ?? s.size}`} className={styles.size} data-state={s.available === 0 ? "out" : low ? "low" : "ok"} aria-label={`${sizeLabel(s.size)}, ${label}`}>
                  <span>{sizeLabel(s.size)}</span>
                  <span className={styles.sizeCount}>{s.available === 0 ? "\u2014" : s.available}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}

function CartCard({ cart }: { cart: CartCardView }) {
  const { show } = useDrawer();
  if (cart.lines.length === 0) return <p className="muted">Your cart is empty.</p>;
  return (
    <div className={styles.cart}>
      <ul className={styles.cartLines}>
        {cart.lines.map((l) => (
          <li key={l.skuId} className={styles.cartLine}>
            <span className={styles.tileSmall} aria-hidden="true"><ProductTile productId={l.productId} category={l.category} color={l.color} /></span>
            <span className={styles.productText}>
              <span className={styles.productName}>{l.name}</span>
              <span className="muted small">{sizeLabel(l.size)} {"\u00B7"} {colorLabel(l.color)} {"\u00B7"} {"\u00D7"}{l.quantity}</span>
            </span>
            <span className={styles.price}>{formatMoney(l.lineTotalCents)}</span>
          </li>
        ))}
      </ul>
      <div className={styles.cartFoot}>
        <span>Total <strong>{formatMoney(cart.totalCents)}</strong></span>
        <button type="button" className="btn" onClick={() => show("cart")}>Open cart</button>
      </div>
    </div>
  );
}

function OrdersCard({ orders }: { orders: OrderCardView[] }) {
  const leave = useLeaveChat();
  return (
    <ul className={styles.orders} aria-label="Orders">
      {orders.slice(0, 5).map((o) => {
        const status = asOrderStatus(o.status);
        return (
          <li key={o.orderId}>
            <Link to={`/orders/${encodeURIComponent(o.orderId)}`} className={styles.order} onClick={leave}>
              <span className={styles.orderId}>{o.orderId}</span>
              <span className="muted small">{formatDate(o.createdAt)}{o.itemCount > 0 ? ` \u00B7 ${o.itemCount} ${o.itemCount === 1 ? "item" : "items"}` : ""}</span>
              {status ? <StatusPill status={status} /> : <span className="pill">{o.status || "Unknown"}</span>}
              <span className={styles.price}>{formatMoney(o.totalCents)}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
