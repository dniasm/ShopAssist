import { useEffect, useRef } from "react";
import { Icon } from "../../components/Icon";
import { useDrawer } from "./DrawerContext";
import { DRAWER_PANELS } from "./panels";
import styles from "./Drawer.module.css";

/** The right-side drawer. Floats over the page (so it may cast the one shadow), closes on Escape or a click on the
 *  scrim, and gives focus back to whatever opened it. */
export function Drawer() {
  const { open, close } = useDrawer();
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
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
  }, [open, close]);

  if (!open) return null;
  const panel = DRAWER_PANELS[open];

  return (
    <div className={styles.layer}>
      <div className={styles.scrim} onClick={close} aria-hidden="true" />
      <aside className={styles.drawer} role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <header className={styles.head}>
          <h2 id="drawer-title" className="section-title">{panel.title}</h2>
          <button type="button" ref={closeButton} className="btn icon" onClick={close} aria-label={`Close ${panel.title.toLowerCase()}`}>
            <Icon name="close" />
          </button>
        </header>
        <div className={styles.body}>{panel.body()}</div>
      </aside>
    </div>
  );
}
