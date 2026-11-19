/* API shapes used by the app: aliases of what the backend publishes in /openapi.json.
 * Regenerate src/api/schema.d.ts with `npm run gen:api` (the API must be running on :8011); nothing here is hand-written
 * beyond naming. Every other module imports its API types from this file only. */

import type { components, operations } from "./schema";
import type { ChatWsMessage } from "./temporary";

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

export type ServicesHealth = Schemas["ServicesHealthOut"];
export type ServiceHealth = Schemas["ServiceHealth"];
export type LlmStatus = Schemas["LlmStatusOut"];
export type LlmKeyStatus = Schemas["LlmKeyStatus"];
export type LlmLimits = Schemas["LlmLimits"];

/** Query parameters of GET /api/products. Repeatable fields are arrays. */
export type ListProductsParams = NonNullable<operations["list_products_api_products_get"]["parameters"]["query"]>;
export type SortKey = NonNullable<ListProductsParams["sort"]>;

export type Cart = Schemas["CartOut"];
export type CartLine = Schemas["CartLine"];
export type CheckoutAccepted = Schemas["CheckoutOut"];
export type PaymentMethod = Schemas["CheckoutIn"]["payment_method"];
export type Order = Schemas["Order"];
export type OrderLine = Schemas["OrderLine"];
export type OrderStatus = Schemas["Order"]["status"];
export type OrderEvent = Schemas["OrderEvent"];
export type OrderSummary = Schemas["OrderSummary"];
export type OrderList = Schemas["OrdersOut"];

export type CancelOrderBody = Schemas["CancelIn"];
export type Shortage = Schemas["Shortage"];

// WebSocket messages: the backend publishes them through the schema-only route GET /api/ws/schema.
export type WsMessage = NonNullable<
  operations["ws_schema_api_ws_schema_get"]["responses"][200]["content"]
>["application/json"];
// TEMPORARY: the chat variants are not in the schema yet (./temporary.ts). SocketMessage is every message the socket can carry.
export type SocketMessage = WsMessage | ChatWsMessage;
export type SocketChannel = SocketMessage["channel"];
export type CheckoutStepData = Schemas["CheckoutStepData"];
export type CheckoutDoneData = Schemas["CheckoutDoneData"];
export type CheckoutFailedData = Schemas["CheckoutFailedData"];
export type CheckoutInterruptedData = Schemas["CheckoutInterruptedData"];
export type OrderStatusData = Schemas["OrderStatusData"];

// TEMPORARY chat shapes, see ./temporary.ts
export type { ChatAccepted, ChatCard, ChatCardKind, ChatContext, ChatRequest, ChatWsMessage, ProposalAck } from "./temporary";
