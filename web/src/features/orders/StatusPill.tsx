import type { OrderStatus } from "../../api/types";
import { statusPill } from "./orderStatus";

const CLASS = { neutral: "pill", pos: "pill pos", neg: "pill neg", warn: "pill warn" } as const;

/** The order's status in words, with colour only where it means something (see statusPill). */
export function StatusPill({ status }: { status: OrderStatus }) {
  const { label, tone } = statusPill(status);
  return <span className={CLASS[tone]}>{label}</span>;
}
