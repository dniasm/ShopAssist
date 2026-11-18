const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

/** Integer cents -> "$12.99" (thousands separated, "-$3.81" when negative). Only integer maths on the cents. */
export function formatMoney(cents: number): string {
  if (!Number.isFinite(cents)) return "—";
  const abs = Math.abs(Math.round(cents));
  const dollars = usd.format(Math.floor(abs / 100)).replace(/\.00$/, "");
  return `${cents < 0 ? "-" : ""}${dollars}.${String(abs % 100).padStart(2, "0")}`;
}

/** "12.5" / "12.50" / "12" -> 1250 cents. Returns null for anything that is not a plain amount. */
export function parseDollarsToCents(text: string): number | null {
  const m = /^(\d{1,7})(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (!m) return null;
  const cents = Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0"));
  return cents;
}

/** 1250 -> "12.5", 1200 -> "12", 1205 -> "12.05". The shortest exact dollar string, for URLs and inputs. */
export function centsToDollarsText(cents: number): string {
  const whole = Math.trunc(cents / 100);
  const frac = cents % 100;
  if (frac === 0) return String(whole);
  const f = String(frac).padStart(2, "0").replace(/0$/, "");
  return `${whole}.${f}`;
}
