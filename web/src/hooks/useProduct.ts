import { getProduct } from "../api/endpoints";
import { useAsyncData } from "./useAsyncData";

/** One product with its per-SKU stock, for the product page. */
export function useProduct(productId: string) {
  return useAsyncData((signal) => getProduct(productId, signal), productId);
}
