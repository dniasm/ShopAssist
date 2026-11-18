/* Product colour names -> display colours. The API only knows lower-case names ("navy"); the swatches and the SVG tiles
 * need a colour. Unknown names get a deterministic colour derived from the name, so they still look intentional. */

const KNOWN: Record<string, string> = {
  black: "#1c1c1b", white: "#f4f3ef", grey: "#8b8b88", gray: "#8b8b88", silver: "#b9bcc0", charcoal: "#3d3f42",
  navy: "#1f3358", blue: "#2f6fc4", "light blue": "#8db8e8", teal: "#1f7a7a", green: "#3f8a55", olive: "#6b6f33",
  khaki: "#b5a56b", tan: "#b98b5a", brown: "#6b4630", beige: "#d8c9a8", cream: "#efe6cf",
  red: "#c4343a", burgundy: "#6e1f33", maroon: "#6e1f33", orange: "#d9772b", yellow: "#e0b82e", gold: "#c9a24a",
  pink: "#e29aae", purple: "#6b4a8f",
};

export function hashString(input: string): number {
  let h = 2166136261;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function hslToHex(h: number, s: number, l: number): string {
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    const c = l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)));
    return Math.round(255 * c).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

export function colorHex(name: string): string {
  const key = name.trim().toLowerCase();
  return KNOWN[key] ?? hslToHex(hashString(key) % 360, 0.38, 0.5);
}

function parse(hex: string): [number, number, number] {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toHex(r: number, g: number, b: number): string {
  return `#${[r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("")}`;
}

/** Relative luminance, 0 (black) .. 1 (white). */
export function luminance(hex: string): number {
  const [r, g, b] = parse(hex).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Mixes `hex` toward white (amount > 0) or black (amount < 0). */
export function shade(hex: string, amount: number): string {
  const target = amount >= 0 ? 255 : 0;
  const t = Math.abs(amount);
  const [r, g, b] = parse(hex);
  return toHex(r + (target - r) * t, g + (target - g) * t, b + (target - b) * t);
}

/** The outline colour for a shape filled with `hex`: darker for light fills, lighter for very dark fills. */
export function outlineFor(hex: string): string {
  return luminance(hex) < 0.05 ? shade(hex, 0.45) : shade(hex, -0.5);
}

export function titleCase(text: string): string {
  return text.replace(/(^|[\s-])([a-z])/g, (_m, sep: string, ch: string) => sep + ch.toUpperCase());
}
