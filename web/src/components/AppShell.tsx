import { useEffect } from "react";
import { Outlet, useLocation } from "react-router";
import { Drawer } from "../features/drawer/Drawer";
import { Header } from "./Header";
import styles from "./AppShell.module.css";

/** Header, the routed page, and the drawer slot. Every route renders inside <main>. */
export function AppShell() {
  const { pathname } = useLocation();

  // New page, new scroll position. (Filter changes on /shop keep the same pathname and so keep the scroll.)
  useEffect(() => { window.scrollTo(0, 0); }, [pathname]);

  return (
    <>
      <a className={styles.skip} href="#main">Skip to content</a>
      <Header />
      <main id="main" className="canvas" tabIndex={-1}>
        <Outlet />
      </main>
      <Drawer />
    </>
  );
}
