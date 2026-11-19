import { Link, NavLink } from "react-router";
import { useAuth } from "../features/auth/AuthContext";
import { UserMenu } from "../features/auth/UserMenu";
import { useCart } from "../features/cart/CartContext";
import { useDrawer } from "../features/drawer/DrawerContext";
import { Icon, Mark } from "./Icon";
import { SearchBox } from "./SearchBox";
import styles from "./Header.module.css";

/** Wordmark, two destinations, the global search, the cart, and the person. Sticky on desktop; scrolls away on a phone
 *  where two rows would eat a fifth of the screen. */
export function Header() {
  const { open, toggle } = useDrawer();
  const { count } = useCart();
  const { user } = useAuth();
  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <Link to="/" className={styles.brand} aria-label="ShopAssist, home">
          <Mark />
          <span className={styles.brandName}>ShopAssist</span>
        </Link>
        <nav className={styles.nav} aria-label="Pages">
          <NavLink to="/shop" className={styles.link}>Shop</NavLink>
          {user && <NavLink to="/orders" className={`${styles.link} ${styles.ordersLink}`}>Orders</NavLink>}
          <NavLink to="/system" className={styles.link}>System</NavLink>
        </nav>
        <div className={styles.search}><SearchBox /></div>
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.cart}
            aria-label={count > 0 ? `Cart, ${count} ${count === 1 ? "item" : "items"}` : "Cart"}
            aria-haspopup="dialog"
            aria-expanded={open === "cart"}
            onClick={() => toggle("cart")}
          >
            <Icon name="bag" size={18} />
            <span className={styles.cartText}>Cart</span>
            {count > 0 && <span className={styles.count} aria-hidden="true">{count}</span>}
          </button>
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
