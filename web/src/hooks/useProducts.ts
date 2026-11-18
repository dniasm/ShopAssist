import { useCallback, useEffect, useRef, useState } from "react";
import { isAbort } from "../api/client";
import { listProducts } from "../api/endpoints";
import type { Facet, ProductCard } from "../api/types";
import { toApiParams, queryKey, type CatalogQuery } from "../features/catalog/query";

export const PAGE_SIZE = 24;

export interface ProductsState {
  /** loading: a request for the current query is in flight. `products` may still hold the previous query's rows (stale). */
  status: "loading" | "ready" | "error";
  products: ProductCard[];
  total: number;
  facets: Facet[];
  priceMinCents: number;
  priceMaxCents: number;
  /** The API found no exact match for the text search and is showing close matches instead. */
  fuzzy: boolean;
  /** The query the rows on screen answer (null before the first response). */
  forKey: string | null;
  /** True while `products` belong to an earlier query and a newer one is loading. */
  stale: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  error: unknown;
  /** A failed "Load more" keeps the rows already shown and reports here. */
  moreError: unknown;
  retry: () => void;
  loadMore: () => void;
}

interface Data {
  products: ProductCard[];
  total: number;
  facets: Facet[];
  priceMinCents: number;
  priceMaxCents: number;
  fuzzy: boolean;
  nextToken: string;
  /** which query these rows answer */
  forKey: string | null;
}

const EMPTY: Data = { products: [], total: 0, facets: [], priceMinCents: 0, priceMaxCents: 0, fuzzy: false, nextToken: "", forKey: null };

/** The catalog results for one URL-derived query: the first page, then "Load more" pages appended.
 *  Changing the query aborts the old request and keeps the old rows on screen (marked stale) until the new ones land. */
export function useProducts(query: CatalogQuery, { pageSize = PAGE_SIZE, restorePages = 1 }: { pageSize?: number; restorePages?: number } = {}): ProductsState {
  const key = queryKey(query);
  const [data, setData] = useState<Data>(EMPTY);
  const [status, setStatus] = useState<ProductsState["status"]>("loading");
  const [error, setError] = useState<unknown>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const queryRef = useRef(query);
  queryRef.current = query;
  const keyRef = useRef(key);
  keyRef.current = key;
  const moreController = useRef<AbortController | null>(null);
  const restoreRef = useRef(restorePages);
  restoreRef.current = restorePages;

  useEffect(() => {
    const controller = new AbortController();
    moreController.current?.abort();
    setStatus("loading");
    setError(null);
    setMoreError(null);
    setLoadingMore(false);
    const load = async () => {
      // First page, then (when returning to a listing that had "Load more" pages) the same number of further pages,
      // appended in order and published together so nothing flickers.
      const first = await listProducts(toApiParams(queryRef.current, { pageSize }), controller.signal);
      let products = first.products;
      let nextToken = first.next_page_token;
      for (let page = 1; page < restoreRef.current && nextToken; page++) {
        const more = await listProducts(toApiParams(queryRef.current, { pageSize, pageToken: nextToken }), controller.signal);
        const seen = new Set(products.map((p) => p.product_id));
        products = [...products, ...more.products.filter((p) => !seen.has(p.product_id))];
        nextToken = more.next_page_token;
      }
      return { first, products, nextToken };
    };
    load().then(
      ({ first, products, nextToken }) => {
        if (controller.signal.aborted) return;
        setData({
          products, total: first.total, facets: first.facets, nextToken,
          priceMinCents: first.price_min_cents, priceMaxCents: first.price_max_cents, fuzzy: first.fuzzy, forKey: key,
        });
        setStatus("ready");
      },
      (err: unknown) => {
        if (controller.signal.aborted || isAbort(err)) return;
        setError(err);
        setStatus("error");
      },
    );
    return () => controller.abort();
  }, [key, pageSize, attempt]);

  const loadMore = useCallback(() => {
    if (!data.nextToken || loadingMore || data.forKey !== keyRef.current) return;
    const controller = new AbortController();
    moreController.current = controller;
    const forKey = data.forKey;
    setLoadingMore(true);
    setMoreError(null);
    listProducts(toApiParams(queryRef.current, { pageSize, pageToken: data.nextToken }), controller.signal).then(
      (res) => {
        if (controller.signal.aborted) return;
        setData((prev) => {
          if (prev.forKey !== forKey) return prev;
          const seen = new Set(prev.products.map((p) => p.product_id));
          return { ...prev, products: [...prev.products, ...res.products.filter((p) => !seen.has(p.product_id))], nextToken: res.next_page_token };
        });
        setLoadingMore(false);
      },
      (err: unknown) => {
        if (controller.signal.aborted || isAbort(err)) return;
        setMoreError(err);
        setLoadingMore(false);
      },
    );
  }, [data.nextToken, data.forKey, loadingMore, pageSize]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  return {
    status,
    products: data.products,
    total: data.total,
    facets: data.facets,
    priceMinCents: data.priceMinCents,
    priceMaxCents: data.priceMaxCents,
    fuzzy: data.fuzzy,
    forKey: data.forKey,
    stale: status === "loading" && data.forKey !== null,
    hasMore: data.nextToken !== "",
    loadingMore,
    error,
    moreError,
    retry,
    loadMore,
  };
}
