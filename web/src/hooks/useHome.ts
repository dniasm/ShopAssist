import { listProducts } from "../api/endpoints";
import { useAsyncData } from "./useAsyncData";

export const TOP_RATED_COUNT = 6;

/** The home page's one request: the top-rated products, whose response also carries the unfiltered category counts,
 *  the catalog size and its price range. */
export function useHome() {
  return useAsyncData((signal) => listProducts({ sort: "rating", page_size: TOP_RATED_COUNT }, signal), "home");
}
