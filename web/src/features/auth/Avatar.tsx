import { hashString } from "../tile/colors";
import styles from "./Avatar.module.css";

// Hues for people's tints. Violet and indigo (about 240-290) are deliberately absent.
const HUES = [8, 28, 48, 85, 140, 168, 195, 215, 330, 350] as const;

export function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "?";
  const last = parts.length > 1 ? parts[parts.length - 1]?.[0] ?? "" : "";
  return (first + last).toUpperCase();
}

/** A person's stable initials on a pale tint of their own hue. Identity, not data: it stays pale. */
export function Avatar({ userId, name, size = 28 }: { userId: string; name: string; size?: number }) {
  const hue = HUES[hashString(userId) % HUES.length] ?? 8;
  return (
    <span className={styles.avatar} style={{ "--h": hue, width: size, height: size, fontSize: Math.round(size * 0.4) } as React.CSSProperties} aria-hidden="true">
      {initialsOf(name)}
    </span>
  );
}
