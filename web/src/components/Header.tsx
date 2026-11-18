import { Link, NavLink } from "react-router";
import { UserMenu } from "../features/auth/UserMenu";
import { useDrawer } from "../features/drawer/DrawerContext";
import { Icon, Mark } from "./Icon";
import { SearchBox } from "./SearchBox";
import styles from "./Header.module.css";

/** Wordmark, two destinations, the global search, the cart, and the person. Sticky on desktop; scrolls away on a phone
 *  where two rows would eat a fifth of the screen. */
export function Header() {
  const { open, toggle } = useDrawer();
  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <Link to="/" className={styles.brand} aria-label="ShopAssist, home">
          <Mark />
          <span className={styles.brandName}>ShopAssist</span>
        </Link>
        <nav className={styles.nav} aria-label="Pages">
          <NavLink to="/shop" className={styles.link}>Shop</NavLink>
          <NavLink to="/system" className={styles.link}>System</NavLink>
        </nav>
        <div className={styles.search}><SearchBox /></div>
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.cart}
            aria-label="Cart"
            aria-haspopup="dialog"
            aria-expanded={open === "cart"}
            onClick={() => toggle("cart")}
          >
            <Icon name="bag" size={18} />
            <span className={styles.cartText}>Cart</span>
          </button>
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
