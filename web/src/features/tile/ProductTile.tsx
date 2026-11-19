import { useId } from "react";
import { colorHex, hashString, luminance, outlineFor, shade } from "./colors";
import { PATTERNS, glyphFor, type PatternKind } from "./glyphs";
import styles from "./ProductTile.module.css";

interface Props {
  productId: string;
  category: string;
  /** Colour name as the API spells it ("navy"). Defaults to a neutral. */
  color?: string;
  /** Accessible name. Omit when the tile sits next to the product's name (it is then hidden from assistive tech). */
  label?: string;
}

/** A deterministic drawn picture of a product: its category's glyph in its colour. There are no photographs, so this is
 *  the product image everywhere (cards, the product page, the cart, orders). Same inputs, same drawing. */
export function ProductTile({ productId, category, color = "grey", label }: Props) {
  const clipId = useId();
  const hex = colorHex(color);
  const outline = outlineFor(hex);
  const glyph = glyphFor(category);
  const pattern = PATTERNS[hashString(productId) % PATTERNS.length] ?? "plain";
  const mark = luminance(hex) > 0.4 ? shade(hex, -0.16) : shade(hex, 0.2);
  const a11y = label ? { role: "img", "aria-label": label } : { "aria-hidden": true as const };

  return (
    <svg className={styles.tile} viewBox="0 0 120 120" focusable="false" {...a11y}>
      <rect width="120" height="120" style={{ fill: `color-mix(in srgb, ${hex} 14%, var(--raised))` }} />
      <g transform="translate(10 10) scale(0.8333)" strokeLinejoin="round" strokeLinecap="round">
        <defs>
          <clipPath id={clipId}>{glyph.body.map((d) => <path key={d} d={d} />)}</clipPath>
        </defs>
        {glyph.body.map((d) => <path key={d} d={d} fill={hex} stroke={outline} strokeWidth={2.5} />)}
        <g clipPath={`url(#${clipId})`}><Pattern kind={pattern} color={mark} /></g>
        {glyph.thick?.map((d) => <path key={d} d={d} fill="none" stroke={outline} strokeWidth={5} />)}
        {glyph.detail?.map((d) => <path key={d} d={d} fill="none" stroke={outline} strokeWidth={1.8} />)}
      </g>
    </svg>
  );
}

function Pattern({ kind, color }: { kind: PatternKind; color: string }) {
  switch (kind) {
    case "band":
      return <rect x="0" y="62" width="120" height="12" fill={color} opacity="0.55" />;
    case "stripes":
      return (
        <g stroke={color} strokeWidth="5" opacity="0.5">
          {[-40, -16, 8, 32, 56, 80, 104].map((x) => <line key={x} x1={x} y1="0" x2={x + 120} y2="120" />)}
        </g>
      );
    case "dots":
      return (
        <g fill={color} opacity="0.55">
          {Array.from({ length: 36 }, (_v, i) => (
            <circle key={i} cx={10 + (i % 6) * 20 + (Math.floor(i / 6) % 2) * 10} cy={10 + Math.floor(i / 6) * 20} r="3" />
          ))}
        </g>
      );
    case "plain":
      return null;
  }
}
