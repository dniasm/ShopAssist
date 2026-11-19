import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

/** The right-hand drawer is one slot. The cart is its only occupant today. */
export type DrawerKind = "cart";

interface DrawerValue {
  open: DrawerKind | null;
  show: (kind: DrawerKind) => void;
  close: () => void;
  toggle: (kind: DrawerKind) => void;
}

const DrawerContext = createContext<DrawerValue | null>(null);

export function useDrawer(): DrawerValue {
  const value = useContext(DrawerContext);
  if (!value) throw new Error("useDrawer must be used inside <DrawerProvider>");
  return value;
}

export function DrawerProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<DrawerKind | null>(null);
  const show = useCallback((kind: DrawerKind) => setOpen(kind), []);
  const close = useCallback(() => setOpen(null), []);
  const toggle = useCallback((kind: DrawerKind) => setOpen((cur) => (cur === kind ? null : kind)), []);
  const value = useMemo(() => ({ open, show, close, toggle }), [open, show, close, toggle]);
  return <DrawerContext.Provider value={value}>{children}</DrawerContext.Provider>;
}
