import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { describeError } from "../api/errors";
import { Icon } from "../components/Icon";
import { EmptyState, ErrorBlock } from "../components/StateBlocks";
import { ActiveFilters } from "../features/catalog/ActiveFilters";
import { FilterPanel } from "../features/catalog/FilterPanel";
import { FiltersSheet } from "../features/catalog/FiltersSheet";
import { pluralize } from "../features/catalog/labels";
import { ProductGrid, ProductGridSkeleton } from "../features/catalog/ProductGrid";
import {
  activeFilterCount, clearFilters, hasAnyCriteria, parseCatalogQuery, serializeCatalogQuery, withSort, withText, catalogHref,
  type CatalogQuery,
} from "../features/catalog/query";
import { SortSelect } from "../features/catalog/SortSelect";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { PAGE_SIZE, useProducts } from "../hooks/useProducts";
import { useListingSnapshot, useRestoredListing } from "../hooks/useRestoredListing";
import styles from "./ShopPage.module.css";

/** /shop. The URL's query string is the whole state: this page parses it, hands it to the data hook, and writes it back. */
export function ShopPage() {
  const [params, setParams] = useSearchParams();
  const query = useMemo(() => parseCatalogQuery(params), [params]);
  const key = serializeCatalogQuery(query);
  const snapshot = useListingSnapshot(key);
  const products = useProducts(query, { restorePages: snapshot.pages });
  useRestoredListing({
    key, snapshot, pageSize: PAGE_SIZE, loadedCount: products.products.length,
    ready: products.status === "ready" && products.forKey === key,
  });
  const [filtersOpen, setFiltersOpen] = useState(false);
  const closeFilters = useCallback(() => setFiltersOpen(false), []);

  // Filter changes push a history entry, so Back undoes them one at a time and every state has a shareable link.
  const setQuery = useCallback((next: CatalogQuery) => setParams(new URLSearchParams(serializeCatalogQuery(next))), [setParams]);

  useDocumentTitle(query.q ? `“${query.q}”` : "Shop");

  const filterCount = activeFilterCount(query);
  const ready = products.status !== "error" && (products.status === "ready" || products.stale);
  const countText = ready ? pluralize(products.total, "product") : " ";

  return (
    <>
      <div className="title-row">
        <div>
          <h1>{query.q ? `Results for “${query.q}”` : "Shop"}</h1>
          <p className="sub" role="status" aria-live="polite">{countText}</p>
        </div>
      </div>

      <div className={styles.layout}>
        <FiltersSheet open={filtersOpen} onClose={closeFilters} total={ready ? products.total : null}>
          <FilterPanel
            query={query}
            facets={products.facets}
            priceMinCents={products.priceMinCents}
            priceMaxCents={products.priceMaxCents}
            onChange={setQuery}
          />
        </FiltersSheet>

        <section className={styles.results} aria-label="Results">
          <div className={styles.bar}>
            <button type="button" className={`btn ${styles.filtersButton}`} onClick={() => setFiltersOpen(true)} aria-haspopup="dialog">
              <Icon name="filter" /> Filters{filterCount > 0 && ` (${filterCount})`}
            </button>
            <div className={styles.chips}><ActiveFilters query={query} onChange={setQuery} /></div>
            <SortSelect value={query.sort} onChange={(sort) => setQuery(withSort(query, sort))} />
          </div>

          <Results products={products} query={query} onChange={setQuery} />
        </section>
      </div>
    </>
  );
}

function Results({ products, query, onChange }: {
  products: ReturnType<typeof useProducts>; query: CatalogQuery; onChange: (next: CatalogQuery) => void;
}) {
  if (products.status === "error") return <ErrorBlock error={products.error} what="products" onRetry={products.retry} />;
  if (products.status === "loading" && !products.stale) return <ProductGridSkeleton />;

  if (products.status === "ready" && products.products.length === 0) {
    const filters = activeFilterCount(query);
    return (
      <EmptyState
        title="No products match"
        action={(
          <>
            {filters > 0 && <button type="button" className="btn primary" onClick={() => onChange(clearFilters(query))}>Clear filters</button>}
            {query.q && <button type="button" className="btn" onClick={() => onChange(withText(query, ""))}>Clear search</button>}
            {!hasAnyCriteria(query) && <span className="muted">The catalog is empty.</span>}
          </>
        )}
      >
        {filters > 0
          ? "Try removing a filter, or clear them all to see everything."
          : query.q ? "Try a different word, or a more general one." : null}
      </EmptyState>
    );
  }

  return (
    <>
      {products.fuzzy && query.q && (
        <p className={styles.fuzzy} role="status">
          No exact matches for {"“"}{query.q}{"”"}. Showing close matches.{" "}
          <Link to={catalogHref(withText(query, ""))} className="link">Clear search</Link>
        </p>
      )}
      <ProductGrid products={products.products} label="Products" busy={products.stale} />
      <div className={styles.more}>
        <p className="muted" aria-live="polite">Showing {products.products.length.toLocaleString("en-US")} of {products.total.toLocaleString("en-US")}</p>
        {products.moreError !== null && (
          <p className={styles.moreError} role="alert">{describeError(products.moreError)}</p>
        )}
        {products.hasMore && (
          <button type="button" className="btn" onClick={products.loadMore} disabled={products.loadingMore}>
            {products.loadingMore ? "Loading…" : products.moreError !== null ? "Try again" : "Load more"}
          </button>
        )}
      </div>
    </>
  );
}
