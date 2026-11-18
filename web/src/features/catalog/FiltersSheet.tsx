import { useEffect, useRef, type ReactNode } from "react";
import { Icon } from "../../components/Icon";
import { pluralize } from "./labels";
import styles from "./FiltersSheet.module.css";

export const NARROW = "(max-width: 1000px)";

interface Props {
  open: boolean;
  onClose: () => void;
  total: number | null;
  children: ReactNode;
}

/** The filter column. Wide screens: a sticky column beside the results. Narrow screens: closed until "Filters" is pressed,
 *  then a full-screen sheet with a "Show N products" button at the bottom. Same content, same state, one component. */
export function FiltersSheet({ open, onClose, total, children }: Props) {
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (!open || !window.matchMedia(NARROW).matches) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    heading.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, [open, onClose]);

  return (
    <aside className={styles.sheet} data-open={open} aria-label="Filters">
      <header className={styles.head}>
        <h2 className="section-title" tabIndex={-1} ref={heading}>Filters</h2>
        <button type="button" className="btn icon" onClick={onClose} aria-label="Close filters"><Icon name="close" /></button>
      </header>
      <div className={styles.body}>{children}</div>
      <footer className={styles.foot}>
        <button type="button" className="btn primary large" onClick={onClose}>
          {total === null ? "Show results" : `Show ${pluralize(total, "product")}`}
        </button>
      </footer>
    </aside>
  );
}
