/** "just now", "5 min ago", "3 h ago", "2 d ago", else the date: when a saved chat was last used. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return "";
  const minutes = Math.floor((now - then) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)} h ago`;
  if (minutes < 60 * 24 * 7) return `${Math.floor(minutes / (60 * 24))} d ago`;
  return new Date(then).toLocaleDateString();
}

/** A title the server will take: trimmed, 1 to 80 characters; null when there is nothing to save. */
export function cleanTitle(raw: string): string | null {
  const t = raw.trim().slice(0, 80);
  return t ? t : null;
}
