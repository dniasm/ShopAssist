import { titleCase } from "../tile/colors";

/** "t-shirts" -> "T-shirts", "home_goods" -> "Home goods". Slugs come from the API; this only makes them readable. */
export function slugLabel(slug: string): string {
  const spaced = slug.replace(/_+/g, " ").trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : slug;
}

/** "ONE" is the API's code for a one-size product. */
export function sizeLabel(size: string): string {
  return size === "ONE" ? "One size" : size;
}

export function colorLabel(color: string): string {
  return titleCase(color);
}

const CLOTHING_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "ONE"];

function sizeRank(size: string): [number, number, string] {
  const clothing = CLOTHING_ORDER.indexOf(size.toUpperCase());
  if (clothing >= 0) return [0, clothing, ""];
  const numeric = Number(size);
  if (size.trim() !== "" && Number.isFinite(numeric)) return [1, numeric, ""];
  return [2, 0, size];
}

/** XS S M L XL XXL, then 6 7 8 ... 12, then anything else alphabetically. Does not mutate its input. */
export function sortSizes(sizes: readonly string[]): string[] {
  return [...sizes].sort((a, b) => {
    const [ga, na, sa] = sizeRank(a);
    const [gb, nb, sb] = sizeRank(b);
    return ga - gb || na - nb || sa.localeCompare(sb);
  });
}

export function pluralize(count: number, one: string, many: string = `${one}s`): string {
  return `${count.toLocaleString("en-US")} ${count === 1 ? one : many}`;
}
