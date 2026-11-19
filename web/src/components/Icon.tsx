/* One outline icon set: 16px, 1.5px stroke, currentColor. Icons never carry meaning alone: callers add text or aria-label. */

const PATHS = {
  search: "M7 12.5a5.5 5.5 0 1 0 0-11 5.5 5.5 0 0 0 0 11zM11 11l3.5 3.5",
  bag: "M3 5.5h10l-.7 8a1.2 1.2 0 0 1-1.2 1.1H4.9a1.2 1.2 0 0 1-1.2-1.1zM5.5 5.5v-.8a2.5 2.5 0 0 1 5 0v.8",
  chevron: "M4 6l4 4 4-4",
  close: "M3.5 3.5l9 9M12.5 3.5l-9 9",
  chat: "M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z",
  filter: "M2 4h12M4.5 8h7M7 12h2",
  check: "M3 8.5l3.2 3.2L13 4.8",
  user: "M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2.5 14c.6-2.6 2.8-4 5.5-4s4.9 1.4 5.5 4",
  minus: "M3.5 8h9",
  plus: "M3.5 8h9M8 3.5v9",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={PATHS[name]} />
    </svg>
  );
}

export function StarIcon({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path fill="currentColor" d="M8 1.3l2 4.3 4.7.6-3.4 3.2.9 4.6L8 11.7l-4.2 2.3.9-4.6L1.3 6.2 6 5.6z" />
    </svg>
  );
}

/** The ShopAssist mark: a bag with two linked hops. The one place the data hue sits in the chrome. */
export function Mark({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <g fill="none" stroke="var(--text)" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round">
        <path d="M5.5 8h13l-1 11.5a1.5 1.5 0 0 1-1.5 1.4H8a1.5 1.5 0 0 1-1.5-1.4z" />
        <path d="M9 8V7a3 3 0 0 1 6 0v1" />
      </g>
      <circle cx="9" cy="15" r="1.4" fill="var(--data)" />
      <circle cx="15" cy="15" r="1.4" fill="var(--data)" />
      <path d="M10.4 15h3.2" stroke="var(--data)" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
