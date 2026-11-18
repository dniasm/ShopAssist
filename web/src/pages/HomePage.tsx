import { Link } from "react-router";
import { formatMoney } from "../api/money";
import type { FacetValue } from "../api/types";
import { ErrorBlock } from "../components/StateBlocks";
import { slugLabel, pluralize } from "../features/catalog/labels";
import { ProductGrid, ProductGridSkeleton } from "../features/catalog/ProductGrid";
import { shopHref } from "../features/catalog/query";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { TOP_RATED_COUNT, useHome } from "../hooks/useHome";
import styles from "./HomePage.module.css";

export function HomePage() {
  useDocumentTitle(null);
  const home = useHome();

  const categories: FacetValue[] = home.status === "ready"
    ? [...(home.data.facets.find((f) => f.field === "category")?.values ?? [])].sort((a, b) => b.count - a.count || a.value.localeCompare(b.value))
    : [];
  const sub = home.status === "ready"
    ? `${pluralize(home.data.total, "product")} in ${pluralize(categories.length, "category", "categories")}, from ${formatMoney(home.data.price_min_cents)} to ${formatMoney(home.data.price_max_cents)}.`
    : " ";

  return (
    <>
      <div className="title-row">
        <div>
          <h1>Home</h1>
          <p className="sub">{sub}</p>
        </div>
        <Link to="/shop" className="btn primary">Browse all products</Link>
      </div>

      {home.status === "error" ? (
        <ErrorBlock error={home.error} what="the shop" onRetry={home.reload} />
      ) : (
        <div className={styles.layout}>
          <section aria-labelledby="cats" className={styles.cats}>
            <h2 id="cats" className="section-title">Categories</h2>
            {home.status === "loading" ? <CategorySkeleton /> : <CategoryList categories={categories} />}
          </section>

          <section aria-labelledby="top" className={styles.top}>
            <div className={styles.topHead}>
              <h2 id="top" className="section-title">Top rated</h2>
              <Link to={shopHref({ sort: "rating" })} className="link">View all</Link>
            </div>
            {home.status === "loading"
              ? <ProductGridSkeleton count={TOP_RATED_COUNT} />
              : <ProductGrid products={home.data.products} label="Top rated products" />}
          </section>
        </div>
      )}
    </>
  );
}

/** A ranked list: the row's thin bar is its share of the largest category. Grey, with the largest in ink. */
function CategoryList({ categories }: { categories: FacetValue[] }) {
  const largest = Math.max(1, ...categories.map((c) => c.count));
  return (
    <ul className={styles.list}>
      {categories.map((c, i) => (
        <li key={c.value}>
          <Link to={shopHref({ category: [c.value] })} className={styles.row}>
            <span className={styles.rowTop}>
              <span className={styles.rowName}>{slugLabel(c.value)}</span>
              <span className="muted">{c.count.toLocaleString("en-US")}<span className="sr-only"> products</span></span>
            </span>
            <span className={styles.track} aria-hidden="true">
              <span className={styles.bar} data-top={i === 0} style={{ width: `${Math.max(2, (c.count / largest) * 100)}%` }} />
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function CategorySkeleton() {
  return (
    <ul className={styles.list} aria-label="Loading categories" aria-busy="true">
      {Array.from({ length: 8 }, (_v, i) => (
        <li key={i} className={styles.row}><span className="skel" style={{ width: `${40 + ((i * 17) % 40)}%` }} /><span className={styles.track} /></li>
      ))}
    </ul>
  );
}
