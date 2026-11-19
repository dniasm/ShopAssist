const dateTime = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat("en-US", { dateStyle: "medium" });
const timeOnly = new Intl.DateTimeFormat("en-US", { timeStyle: "medium" });

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "Nov 18, 2026, 3:04 PM", or an em dash when the server sent nothing usable. */
export function formatDateTime(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? dateTime.format(d) : "—";
}
export function formatDate(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? dateOnly.format(d) : "—";
}
/** "3:04:09 PM" for the timeline, where the seconds show the 8 s steps. */
export function formatTime(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? timeOnly.format(d) : "";
}
