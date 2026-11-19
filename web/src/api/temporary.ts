/* TEMPORARY (Phase 5.4). The chat contract is not in /openapi.json yet, so these shapes are written from the PLAN ("REST contract" rows
 * for /api/chat and "WebSocket" chat channel row). When Sonnet-A publishes them: run `npm run gen:api`, let the aliases in types.ts point at
 * `components["schemas"]` (WsMessage then already includes the chat variants), and delete this file. */

// ---------- REST ----------

export interface ChatContext { path: string; product_id?: string }
export interface ChatRequest { message: string; context: ChatContext }
export interface ChatAccepted { turn_id: string }
export interface ProposalAck { ok: boolean }

// ---------- WebSocket: channel `chat` ----------

export type ChatCardKind = "products" | "product" | "stock" | "cart" | "order" | "orders";
/** `data` is deliberately unknown: each kind is checked and turned into a view model in features/chat/cards.ts. */
export interface ChatCard { kind: ChatCardKind; data: unknown }

interface ChatEnvelope<T extends string, D> { seq: number; ts: string; channel: "chat"; type: T; data: D }

export type ChatWsMessage =
  | ChatEnvelope<"turn_started", { turn_id: string }>
  | ChatEnvelope<"tool_call", { turn_id: string; tool: string; args: Record<string, unknown> }>
  | ChatEnvelope<"tool_result", { turn_id: string; tool: string; summary: string }>
  | ChatEnvelope<"message", { turn_id: string; text: string; cards: ChatCard[] }>
  | ChatEnvelope<"proposal", { turn_id: string; proposal_id: string; action: string; args: Record<string, unknown>; summary: string }>
  | ChatEnvelope<"turn_done", { turn_id: string; source: "llm" | "rules" }>
  | ChatEnvelope<"error", { turn_id: string; message: string }>;
