/* API shapes used by the app: aliases of what the backend publishes in /openapi.json.
 * Regenerate src/api/schema.d.ts with `npm run gen:api` (the API must be running on :8011); nothing here is hand-written
 * beyond naming. Every other module imports its API types from this file only. */

import type { components, operations } from "./schema";

type Schemas = components["schemas"];

export type User = Schemas["User"];
export type Session = Schemas["SessionOut"];
export type Sku = Schemas["Sku"];
export type Product = Schemas["Product"];
export type ProductCard = Schemas["ProductCard"];
export type Facet = Schemas["Facet"];
export type FacetValue = Schemas["FacetValue"];
export type ProductList = Schemas["ProductListResponse"];
export type StockEntry = Schemas["StockOption"];
export type ProductDetail = Schemas["ProductDetailResponse"];
export type ErrorCode = Schemas["ErrorBody"]["code"];

/** Query parameters of GET /api/products. Repeatable fields are arrays. */
export type ListProductsParams = NonNullable<operations["list_products_api_products_get"]["parameters"]["query"]>;
export type SortKey = NonNullable<ListProductsParams["sort"]>;
