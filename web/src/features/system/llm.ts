import type { LlmKeyStatus, LlmStatus } from "../../api/types";

export type MeterTone = "ok" | "low" | "empty" | "unknown";
export interface MeterView {
  /** Remaining share of the limit, 0..1, or null when the API does not track it for this key. */
  fraction: number | null;
  tone: MeterTone;
  text: string;
}

export const LOW_BELOW = 0.2;

/** One limit as a meter: how much is LEFT against the limit. Below 20 % it is "low", at 0 "empty"; a missing value is a dash. */
export function meter(left: number | null | undefined, limit: number): MeterView {
  if (left === null || left === undefined || !Number.isFinite(left) || limit <= 0) return { fraction: null, tone: "unknown", text: "—" };
  const clamped = Math.max(0, Math.min(limit, left));
  const fraction = clamped / limit;
  return {
    fraction,
    tone: clamped === 0 ? "empty" : fraction < LOW_BELOW ? "low" : "ok",
    text: `${clamped.toLocaleString("en-US")} of ${limit.toLocaleString("en-US")} left`,
  };
}

export type KeyTone = "pos" | "warn" | "neg" | "neutral";

/** A key's state in words. The API says "ok", "cooling" or "disabled"; anything else is shown as it came, in plain tone. */
export function keyState(key: Pick<LlmKeyStatus, "state">): { label: string; tone: KeyTone } {
  switch (key.state) {
    case "ok": return { label: "OK", tone: "pos" };
    case "cooling": return { label: "Cooling", tone: "warn" };
    case "disabled": return { label: "Disabled", tone: "neg" };
    default: return { label: key.state ? key.state.charAt(0).toUpperCase() + key.state.slice(1) : "Unknown", tone: "neutral" };
  }
}

/** Whole seconds until an ISO time, 0 once passed, null for no/invalid time. */
export function secondsUntil(iso: string | null | undefined, nowMs: number): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.max(0, Math.ceil((t - nowMs) / 1000));
}

export function coolingText(seconds: number | null): string | null {
  if (seconds === null) return null;
  if (seconds === 0) return "Ready again";
  return seconds < 90 ? `Ready again in ${seconds} s` : `Ready again in ${Math.ceil(seconds / 60)} min`;
}

/** No keys configured (or the pool switched off): the assistant runs on its rules parser alone. */
export function isRulesOnly(status: Pick<LlmStatus, "enabled" | "keys">): boolean {
  return !status.enabled || status.keys.length === 0;
}

export function poolSummary(status: LlmStatus): string {
  if (isRulesOnly(status)) return "LLM disabled — rules only";
  const ready = status.keys.filter((k) => k.state === "ok").length;
  const cooling = status.keys.filter((k) => k.state === "cooling").length;
  const parts = [`${ready} of ${status.keys.length} keys ready`];
  if (cooling > 0) parts.push(`${cooling} cooling`);
  return parts.join(", ");
}
