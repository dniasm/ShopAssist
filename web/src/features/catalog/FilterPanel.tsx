import { useState, type ReactNode } from "react";
import type { Facet } from "../../api/types";
import { Icon } from "../../components/Icon";
import { colorHex } from "../tile/colors";
import { facetOptions, type FacetOption } from "./facets";
import { PriceFilter } from "./PriceFilter";
import { toggleListValue, withPrice, type CatalogQuery, type ListField } from "./query";
import styles from "./FilterPanel.module.css";

interface Props {
  query: CatalogQuery;
  facets: Facet[];
  priceMinCents: number;
  priceMaxCents: number;
  onChange: (next: CatalogQuery) => void;
}

/** Facet filters with live counts. Pure view: every change goes up through `onChange`, and the URL decides the rest. */
export function FilterPanel({ query, facets, priceMinCents, priceMaxCents, onChange }: Props) {
  const toggle = (field: ListField) => (value: string) => onChange(toggleListValue(query, field, value));
  return (
    <div className={styles.panel}>
      <Group title="Category" count={query.category.length}>
        <CheckList options={facetOptions(facets, "category", query.category)} onToggle={toggle("category")} name="category" />
      </Group>
      <Group title="Brand" count={query.brand.length}>
        <CheckList options={facetOptions(facets, "brand", query.brand)} onToggle={toggle("brand")} name="brand" limit={7} />
      </Group>
      <Group title="Colour" count={query.color.length}>
        <CheckList options={facetOptions(facets, "color", query.color)} onToggle={toggle("color")} name="color" swatches limit={8} />
      </Group>
      <Group title="Sizes offered" count={query.size.length}>
        <SizeChips options={facetOptions(facets, "size", query.size)} onToggle={toggle("size")} />
      </Group>
      <Group title="Price" count={query.minPriceCents !== null || query.maxPriceCents !== null ? 1 : 0}>
        <PriceFilter
          minCents={query.minPriceCents}
          maxCents={query.maxPriceCents}
          rangeMinCents={priceMinCents}
          rangeMaxCents={priceMaxCents}
          onCommit={(min, max) => onChange(withPrice(query, min, max))}
        />
      </Group>
    </div>
  );
}

function Group({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <section className={styles.group}>
      <h3 className={styles.groupTitle}>
        <button type="button" className={styles.groupButton} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <span>{title}{count > 0 && <span className={styles.badge}>{count}</span>}</span>
          <span className={styles.chev} data-open={open}><Icon name="chevron" size={14} /></span>
        </button>
      </h3>
      {open && <div className={styles.groupBody}>{children}</div>}
    </section>
  );
}

function CheckList({ options, onToggle, name, swatches = false, limit }: {
  options: FacetOption[]; onToggle: (value: string) => void; name: string; swatches?: boolean; limit?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  if (options.length === 0) return <p className="muted small">None for these results.</p>;
  const shown = limit && !expanded ? pickVisible(options, limit) : options;
  return (
    <>
      <ul className={styles.list}>
        {shown.map((o) => (
          <li key={o.value}>
            <label className={styles.option}>
              <input type="checkbox" className={styles.box} name={name} value={o.value} checked={o.selected} onChange={() => onToggle(o.value)} />
              {swatches && <span className={styles.swatch} style={{ background: colorHex(o.value) }} aria-hidden="true" />}
              <span className={styles.optionLabel}>{o.label}</span>
              <span className={styles.count} aria-label={`${o.count} products`}>{o.count}</span>
            </label>
          </li>
        ))}
      </ul>
      {limit !== undefined && options.length > limit && (
        <button type="button" className={`btn text ${styles.more}`} aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show fewer" : `Show all ${options.length}`}
        </button>
      )}
    </>
  );
}

/** The first `limit` options, but never hiding a selected one. */
function pickVisible(options: FacetOption[], limit: number): FacetOption[] {
  const head = options.slice(0, limit);
  const hiddenSelected = options.slice(limit).filter((o) => o.selected);
  return [...head, ...hiddenSelected];
}

function SizeChips({ options, onToggle }: { options: FacetOption[]; onToggle: (value: string) => void }) {
  if (options.length === 0) return <p className="muted small">None for these results.</p>;
  return (
    <ul className={styles.chips}>
      {options.map((o) => (
        <li key={o.value}>
          <label className={styles.chip}>
            <input type="checkbox" className={styles.chipInput} name="size" value={o.value} checked={o.selected} onChange={() => onToggle(o.value)} />
            <span className={styles.chipFace} aria-label={`${o.label}, ${o.count} products`}>
              <span className={styles.chipLabel}>{o.label}</span>
              <span className={styles.chipCount}>{o.count}</span>
            </span>
          </label>
        </li>
      ))}
    </ul>
  );
}
