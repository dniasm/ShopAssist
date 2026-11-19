import { useEffect, useRef } from "react";
import { Icon } from "../../components/Icon";
import { useDrawer } from "./DrawerContext";
import { DRAWER_PANELS } from "./panels";
import styles from "./Drawer.module.css";

/** The right-side drawer: one slot, two occupants. It floats over the page (so it may cast the one shadow).
 *  - cart: modal. A scrim covers the page, Escape or a click on the scrim closes it, and focus goes back to whatever opened it.
 *  - chat: not modal. The page stays usable beside it, it keeps its place across navigation, and Escape closes it only while focus is inside. */
export function Drawer() {
  const { open, close } = useDrawer();
  const closeButton = useRef<HTMLButtonElement>(null);
  const panel = open ? DRAWER_PANELS[open] : null;
  const modal = panel?.modal ?? false;

  useEffect(() => {
    if (!open || !modal) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButton.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      opener?.focus();
    };
  }, [open, modal, close]);

  if (!open || !panel) return null;

  const head = (
    <header className={styles.head}>
      <h2 id="drawer-title" className="section-title">{panel.title}</h2>
      <button type="button" ref={closeButton} className="btn icon" onClick={close} aria-label={`Close ${panel.title.toLowerCase()}`}>
        <Icon name="close" />
      </button>
    </header>
  );

  if (modal) {
    return (
      <div className={styles.layer}>
        <div className={styles.scrim} onClick={close} aria-hidden="true" />
        <aside className={styles.drawer} role="dialog" aria-modal="true" aria-labelledby="drawer-title">
          {head}
          <div className={styles.body}>{panel.body()}</div>
        </aside>
      </div>
    );
  }

  return (
    <aside
      className={`${styles.drawer} ${styles.docked}`}
      role="complementary"
      aria-labelledby="drawer-title"
      onKeyDown={(e) => { if (e.key === "Escape") close(); }}
    >
      {head}
      <div className={styles.body}>{panel.body()}</div>
    </aside>
  );
}
