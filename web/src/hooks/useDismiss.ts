import { useEffect, type RefObject } from "react";

/** Calls `onDismiss` on Escape, or on a pointer press / focus landing outside `ref`. Only listens while `active`. */
export function useDismiss(ref: RefObject<HTMLElement | null>, active: boolean, onDismiss: () => void): void {
  useEffect(() => {
    if (!active) return;
    const outside = (e: Event) => {
      const el = ref.current;
      if (el && e.target instanceof Node && !el.contains(e.target)) onDismiss();
    };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onDismiss(); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", outside);
      document.removeEventListener("keydown", key);
    };
  }, [ref, active, onDismiss]);
}
