# Shopping Assistant Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the chat assistant on native tool calling with saved chat history, fact memory (permanent + temporal,
semantic search) and a rolling chat summary, within ~3,000 tokens per exchange on average.

**Architecture:** A sixth gRPC service `assistant` (own Postgres DB and role) stores conversations, messages, summaries
and facts. The orchestrator's agent loop calls Groq `openai/gpt-oss-120b` with 8 merged tools, compact tool results and
a memory block (top facts + summary + latest exchange); a background worker extracts facts and rewrites the summary
with `openai/gpt-oss-20b`; Voyage embeds facts and queries. The web chat panel gets a chat list, server-loaded history,
a `navigate` event and a checkout confirm that opens `/checkout`.

**Tech Stack:** Python 3 / FastAPI / grpc.aio / psycopg 3 / Postgres 17; httpx (Groq, Voyage); React + TypeScript + Vitest.

**Spec:** `docs/superpowers/specs/2026-11-24-agent-chat-rework-design.md`

## Global Constraints

- The assistant never places an order or charges: no code path from chat reaches `OrderService.Checkout`.
- Cart writes act on an exact `line_id` (= the cart line's `sku_id`); an unknown line id is rejected; several matches → ask.
- Replies name products, never ids (ids appear only if the shopper typed one).
- Facts: kinds `permanent` (never expires, single-value keys replaced, list keys collected) and `temporal` (category,
  `expires_at` = created + 30 days, deleted when an order containing that category reaches CONFIRMED).
- Per turn: top 5 facts with score >= 0.45; summary <= 200 words; latest exchange verbatim; at most 6 tool steps.
- Token targets per call: system ~500, tools ~600, facts ~150, summary ~270, latest exchange ~250, tool results ~600.
- Guests: no saved history and no facts (in-memory chat as today).
- Secrets only in `.env`; never logged or printed. Do not touch port 8000. Assistant gRPC port 50056.
- Static prompt parts first (system, tools) so provider prompt caching can reuse them.

## Review Focus

1. Shopper says "remove it"/"the hoodie" with two hoodie lines (different sizes) → asks which, by size/colour; nothing removed.
2. Model calls `cart(action="remove")` with a `line_id` not in the cart (stale or invented) → error to model, cart unchanged.
3. User B requests user A's conversation id (list/messages/rename/delete) → NOT_FOUND, A's data untouched.
4. Voyage key missing or Voyage down → facts still found by keyword overlap; turn still answers.
5. Groq down mid-turn after a cart write was requested → no write happens; reply says to try again.

Each line has its test in the owning task (Tasks 2, 5, 6, 7).

---

## File Structure

- `protos/shopassist_proto/shopassist.proto` + generated `shopassist_proto/*` — AssistantService.
- `services/assistant/{__init__,server,db,facts}.py`, `services/assistant/migrations/001_init.sql` — the service.
- `shopassist_proto/svcauth.py`, `orchestrator/{clients,settings}.py`, `scripts/{db_setup,run_all}.py`,
  `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml` (only if the DB list is hard-coded) — wiring.
- `orchestrator/llm.py` — `chat()` with tools, `model` override, per-call token log.
- `orchestrator/embed.py` — Voyage embeddings (optional).
- `orchestrator/agent/compact.py` (compact lines), `orchestrator/agent/tags.py` (description → tags).
- `orchestrator/agent/tools.py` — 8 tools (rewritten). `orchestrator/agent/prompt.py` — system prompt + tool schemas (rewritten).
- `orchestrator/agent/loop.py` — the turn (rewritten). `orchestrator/agent/memory.py` — retrieval + background worker.
- `orchestrator/agent/planner.py`, `rules.py` — kept only as read-only fallback (LLM JSON planner path removed).
- `orchestrator/agent/state.py` — ConversationMemory kept for guests; ProposalStore gains action `checkout_redirect`.
- `orchestrator/routes_chat.py` — conversation endpoints; confirm of `checkout_redirect` emits `navigate`.
- `orchestrator/checkout_flow.py` — clears temporal facts on CONFIRMED.
- `web/src/features/chat/*`, `web/src/api/endpoints.ts`, `web/src/features/session/events.ts` — UI.
- `scripts/eval_agent.py`, `tests/eval/queries.jsonl` — eval of the new loop. `README.md`, `docs/PLAN.md` — docs.

Spec deviation (builder's call, recorded): product tags are computed in the orchestrator from the description at read
time (`agent/tags.py`) instead of a catalog column; same output, no migration/proto change.

---

### Task 1: AssistantService contract and wiring

**Files:**
- Modify: `protos/shopassist_proto/shopassist.proto`, regenerate with `python scripts/gen_protos.py`
- Modify: `shopassist_proto/svcauth.py` (CALLERS: orchestrator may reach `assistant`), `orchestrator/clients.py`
  (SERVICES + `self.assistant`), `orchestrator/settings.py` (`assistant_addr="localhost:50056"`, `voyage_api_key`,
  `voyage_model="voyage-4-lite"`, `memory_model="openai/gpt-oss-20b"`), `scripts/db_setup.py`
  (`("assistant", "svc_assistant", "ASSISTANT_DATABASE_URL")`), `scripts/run_all.py`
  (`Proc("assistant", "services.assistant.server", "ASSISTANT_ADDR", "localhost:50056", "grpc")`), `docker-compose.yml`,
  `.env.example` (ASSISTANT_ADDR, VOYAGE_API_KEY, VOYAGE_MODEL, MEMORY_MODEL)
- Test: `tests/test_run_all.py`, `tests/test_service_auth.py`, `tests/test_isolation.py` (extend existing tables)

**Interfaces — Produces (proto, package `shopassist.v2`; user id from `x-user-id` metadata like Customer):**
```proto
service AssistantService {
  rpc CreateConversation(CreateConversationRequest) returns (Conversation);   // {title}
  rpc ListConversations(ListConversationsRequest) returns (ListConversationsResponse); // {page_size} newest updated first
  rpc GetConversation(GetConversationRequest) returns (Conversation);         // {conversation_id}; NOT_FOUND if not the user's
  rpc RenameConversation(RenameConversationRequest) returns (Conversation);   // {conversation_id, title}
  rpc DeleteConversation(DeleteConversationRequest) returns (DeleteConversationResponse); // also deletes its messages
  rpc AppendMessage(AppendMessageRequest) returns (ChatMessage);              // {conversation_id, role "user"|"assistant", text, cards_json}
  rpc ListMessages(ListMessagesRequest) returns (ListMessagesResponse);       // {conversation_id, limit} oldest first
  rpc SetSummary(SetSummaryRequest) returns (Conversation);                   // {conversation_id, summary}
  rpc ApplyFacts(ApplyFactsRequest) returns (ListFactsResponse);              // {repeated FactChange changes}
  rpc SearchFacts(SearchFactsRequest) returns (ListFactsResponse);            // {query_text, repeated float query_embedding, limit, min_score}
  rpc ListFacts(ListFactsRequest) returns (ListFactsResponse);
  rpc ClearTemporalFacts(ClearTemporalFactsRequest) returns (ClearTemporalFactsResponse); // {repeated string categories} -> {removed}
}
message Conversation { string conversation_id = 1; string title = 2; string summary = 3; string created_at = 4; string updated_at = 5; }
message ChatMessage { int64 message_id = 1; string conversation_id = 2; string role = 3; string text = 4; string cards_json = 5; string created_at = 6; }
message Fact { string fact_id = 1; string kind = 2; string key = 3; string value = 4; string category = 5; string expires_at = 6; double score = 7; }
message FactChange { string op = 1; /* "remember"|"forget" */ string kind = 2; string key = 3; string value = 4; string category = 5; repeated float embedding = 6; }
```

- [ ] **Step 1:** Extend `tests/test_run_all.py` / `tests/test_service_auth.py` expectations: run_all lists `assistant`
  on 50056; orchestrator may call `assistant`, `order` may not. Run them: FAIL.
- [ ] **Step 2:** Add the proto, regenerate, wire svcauth/clients/settings/db_setup/run_all/compose/.env.example.
- [ ] **Step 3:** Run `pytest tests/test_run_all.py tests/test_service_auth.py -q`: PASS. Commit
  `"Assistant service contract and wiring"`.

### Task 2: Assistant service (storage, fact rules, search)

**Files:**
- Create: `services/assistant/__init__.py`, `server.py` (pattern of `services/payment/server.py`), `db.py`, `facts.py`,
  `migrations/001_init.sql`
- Test: `tests/test_assistant.py` (use the harness in `tests/harness.py` / fixtures like `tests/test_customer.py`)

**Schema:** `conversations(conversation_id text pk, user_id, title, summary text default '', created_at, updated_at)`;
`messages(message_id bigserial pk, conversation_id fk on delete cascade, role, text, cards jsonb, created_at)`;
`facts(fact_id text pk, user_id, kind check in ('permanent','temporal'), key, value, category default '',
embedding real[], created_at, expires_at null)`; index `facts(user_id)`.

**Interfaces — Produces (`services/assistant/facts.py`):**
- `SINGLE_KEYS = {"preferred_name","size_top","size_bottom","shoe_size","height","weight","budget","favorite_color","gender_fit"}`
  plus any key starting `favorite_`, `preferred_`; all other keys collect values.
- `normalise_key(key: str) -> str` (snake_case, "favourite"→"favorite", aliases `size`→`size_top`, `name`→`preferred_name`).
- `plan_changes(existing: list[dict], changes: list[dict]) -> tuple[list[dict], list[str]]` → (rows to insert, fact_ids to delete).
  Remember on a single key replaces; on a list key adds unless the value exists (case-insensitive); forget with value
  `*` removes the key; temporal remember sets `expires_at = now + 30 days`.
- `rank(facts: list[dict], query_text: str, query_embedding: list[float] | None, limit: int, min_score: float) -> list[dict]`:
  cosine when both embeddings exist, else keyword overlap (token Jaccard on key+value, same `min_score`); expired
  temporal facts never returned (and deleted on read).

- [ ] **Step 1: Write failing tests** in `tests/test_assistant.py`:
  - `test_conversation_isolated_per_user`: B's Get/Rename/Delete/ListMessages on A's id → `NOT_FOUND`; A unchanged.
  - `test_delete_conversation_deletes_messages`.
  - `test_list_conversations_newest_first` (AppendMessage bumps `updated_at`).
  - `test_permanent_single_key_replaced`: remember size_top M then L → one fact, value "L".
  - `test_list_key_collects_without_duplicates`: liked_brands Nike, nike, Adidas → 2 facts.
  - `test_forget_star_removes_key`.
  - `test_temporal_expires_after_30_days` (inject clock) and `test_clear_temporal_by_category` (only temporal facts
    in `t-shirts` removed; permanent untouched; `removed == 1`).
  - `test_search_top5_min_score` (embeddings given) and `test_search_keyword_fallback_without_embeddings`
    (query "what size am i" finds `size_top`).
- [ ] **Step 2:** Run `pytest tests/test_assistant.py -q`: FAIL (module missing).
- [ ] **Step 3:** Implement migration, `db.py`, `facts.py`, `server.py` (health, migrations, `listen_address`).
- [ ] **Step 4:** Run `pytest tests/test_assistant.py tests/test_isolation.py -q`: PASS. Commit `"Assistant service"`.

### Task 3: LLM tool calling and Voyage embeddings

**Files:** Modify `orchestrator/llm.py`; create `orchestrator/embed.py`; tests `tests/test_llm.py`, `tests/test_embed.py`.

**Interfaces — Produces:**
- `LLMClient.chat(messages: list[dict], tools: list[dict], *, max_tokens: int = 700, model: str | None = None) -> dict`
  returns `{"content": str | None, "tool_calls": [{"id": str, "name": str, "args": dict}], "usage": int}`; raises
  `LLMError` on failure (same key rotation as `complete_json`; refactor the shared rotation, don't copy it).
  Malformed `arguments` JSON for a call → that call gets `args={"_error": "bad arguments"}` (not a crash).
- `LLMClient.complete_json(..., model: str | None = None)` — new optional model override.
- Every successful call logs `llm call model=%s prompt=%d completion=%d` (usage from the response).
- `Embedder(settings)` with `async embed(texts: list[str], kind: "query"|"document") -> list[list[float]] | None`
  (POST `https://api.voyageai.com/v1/embeddings`, `input_type` = kind; None when no key, timeout 3 s, or any error).

- [ ] **Step 1:** Failing tests with `httpx.MockTransport`: `test_chat_returns_tool_calls`, `test_chat_bad_arguments_marked`,
  `test_chat_rotates_on_429`, `test_model_override_sent`, `test_embed_none_without_key`, `test_embed_none_on_error`.
- [ ] **Step 2:** Run: FAIL. **Step 3:** Implement. **Step 4:** `pytest tests/test_llm.py tests/test_embed.py -q`: PASS.
- [ ] **Step 5:** Commit `"LLM tool calling, model override, token log; Voyage embedder"`.

### Task 4: Compact results and product tags

**Files:** Create `orchestrator/agent/compact.py`, `orchestrator/agent/tags.py`; test `tests/test_agent_compact.py`.

**Interfaces — Produces:**
- `tags(description: str) -> list[str]` — 3-5 lower-case tags from the seeded pattern ("Made from <material>, this
  <feature> <noun> features <feature>. It offers ... for <use>."); unknown pattern → up to 4 longest non-stopword words.
- `product_line(i: int, p: dict) -> str` → `"1. Trail Pack [P-1001] $79 · black, olive · S-XL · in stock · tpu-coated, roll-top, hip-belt, cycling"`
- `cart_line(line: dict) -> str` → `"[SKU-...] Cozy Hoodie [P-1004] M navy ×1 $49"`; `cart_summary(view: dict) -> str`
  (all lines + total; `"Cart is empty."`).
- `order_line(o: dict) -> str` → `"O-5001 shipped $98 · 2 items · 2026-11-20"`.

- [ ] **Step 1:** Failing tests: exact strings above for a fixture product/line/order; `test_tags_from_seed_descriptions`
  (every product in `data/seed/v2/catalog.json` gets 3-5 tags; the backpack example yields `tpu-coated`, `roll-top`,
  `cycling`); `test_six_product_lines_under_600_tokens` (len(text)//4 < 600).
- [ ] **Step 2-4:** FAIL → implement → `pytest tests/test_agent_compact.py -q` PASS. **Step 5:** Commit.

### Task 5: The 8 tools

**Files:** Rewrite `orchestrator/agent/tools.py`, `orchestrator/agent/prompt.py` (tool schemas + system prompt);
modify `orchestrator/agent/state.py` (Proposal action `"checkout_redirect"`); test `tests/test_agent_tools.py`
(replace tool parts of `tests/test_agent_chat.py` that tested the old tools).

**Interfaces — Consumes:** Task 4 formatters. **Produces:**
- `TOOLS: list[dict]` (OpenAI tool schemas, short descriptions, total ~600 tokens) for: `search`, `product`, `cart`,
  `orders`, `cancel_order`, `checkout`, `open_page`, `recall_facts`; `SYSTEM_PROMPT: str` (~500 tokens: role, never pay,
  ask when ambiguous, names not ids, no invented products/prices/stock, shop topics only, facts are hints and the latest
  message wins, checkout rule).
- `AgentTools.run(name: str, args: dict, user_id: str | None, turn_id: str) -> ToolResult` where
  `ToolResult = {"text": str (compact, for the model), "card": dict | None (full data, for the UI), "final": str | None
  (a reply that needs no second model call), "proposal": Proposal | None, "navigate": dict | None, "error": bool}`.
- `cart` actions: `view`; `add(product_id, size, color, quantity=1)` (missing/unknown variant → `final` question
  listing the valid sizes/colours, nothing written); `set_qty(line_id, quantity)`; `remove(line_id)`. Unknown
  `line_id` → `error=True`, text lists the real lines. Success → `final` like `"Added Cozy Hoodie (M, navy). Cart total $98."`.
- `product(ids: list[str] (1-3), size?, color?)` — details + stock per id; no `final` (the model reasons on it).
- `search(...)` → `final` = intro sentence from the current `product_list` rules (keep "similar to" behaviour); no
  results → `final=None` so the model can suggest alternatives.
- `checkout()` → proposal `checkout_redirect` with summary `"Are you sure you'd like to check out?"` and the cart total;
  never calls Order. `cancel_order(order_id)` → proposal `cancel_order` (as today). `open_page(kind, product_id?,
  order_id?, query?, category?)` → `navigate={"path": ...}` (`/p/P-1`, `/shop?category=..&q=..`, `/checkout` never, `/orders`, `/orders/O-1`).
- `recall_facts(query)` → calls Task 7's `MemoryReader.search` (inject a callable in the constructor).
- Account tools without a user → sign-in card, as today.

- [ ] **Step 1: Failing tests:**
  - `test_remove_ambiguous_asks_and_keeps_cart`: cart with Hoodie M navy + Hoodie L navy; model passes no/unknown
    line → `error`, cart unchanged, text names both by size.
  - `test_remove_exact_line_only`: remove `line_id` of the L line → only it removed.
  - `test_set_qty_exact_line`, `test_add_missing_variant_asks` (no SetCartLine call), `test_add_names_product_not_id`
    (`final` contains the name and no `P-`).
  - `test_checkout_never_calls_order` (fake Order stub asserts not called; proposal action `checkout_redirect`).
  - `test_open_page_paths`, `test_tools_schema_under_700_tokens` (len(json.dumps(TOOLS))//4 < 700),
    `test_system_prompt_under_600_tokens`.
- [ ] **Step 2-4:** FAIL → implement → `pytest tests/test_agent_tools.py -q` PASS. **Step 5:** Commit `"Agent: 8 merged tools"`.

### Task 6: The turn loop

**Files:** Rewrite `orchestrator/agent/loop.py`; trim `orchestrator/agent/planner.py` to the rules fallback; modify
`orchestrator/app.py` (construct with memory reader/worker); tests `tests/test_agent_chat.py` (rewrite to the new
loop with a scripted fake LLM), keep `tests/test_agent_rules.py`.

**Interfaces — Consumes:** Task 3 `LLMClient.chat`, Task 5 `TOOLS`/`SYSTEM_PROMPT`/`AgentTools`, Task 7
`MemoryReader.context(user_id, conversation_id, message) -> MemoryBlock` and `MemoryWorker.submit(...)`.
**Produces:** `AgentLoop.run_turn(owner, user_id, turn_id, message, page, conversation_id)` — same events as today
(`turn_started`, `tool_call`, `tool_result`, `proposal`, `message`, `turn_done`, `error`) plus `navigate {turn_id, path}`.

Message order sent to the model: `system` = SYSTEM_PROMPT; then one `system` block "Memory" (facts lines, summary,
cart summary, page context); then the latest exchange as user/assistant messages; then the new user message; tool
results appended as `tool` messages (compact `text`).

Loop: up to 6 steps. After a step with exactly one tool call whose result has `final` and no `error`, end the turn
with that text (no second call). A repeated identical call ends the turn with the last result. On `LLMError`: run
`rules.plan` once; allow only `search`/`product`/`cart view`/`orders` (map old names); any write → reply
`"I can't do that right now. Please try again in a minute."`. Signed-in: save user + assistant messages via
`AppendMessage` (conversation created on the first message, title = first 60 chars) then `MemoryWorker.submit`.
Guests: today's in-memory `ConversationMemory`.

- [ ] **Step 1: Failing tests:** `test_simple_add_is_one_llm_call`, `test_compare_needs_second_call`,
  `test_llm_down_no_write` (fake LLM raises; message "remove the hoodie" → cart unchanged, reply contains "try again"),
  `test_llm_down_search_still_works`, `test_messages_saved_for_signed_in`, `test_guest_not_saved`,
  `test_follow_up_sees_latest_exchange` (second turn's request contains the first reply's product list),
  `test_navigate_event_emitted`, `test_reply_has_no_product_ids`.
- [ ] **Step 2-4:** FAIL → implement → `pytest tests/test_agent_chat.py tests/test_agent_rules.py -q` PASS. **Step 5:** Commit.

### Task 7: Memory: retrieval, background worker, purchase clearing

**Files:** Create `orchestrator/agent/memory.py`; modify `orchestrator/checkout_flow.py` (on the event where the order
is CONFIRMED, call `ClearTemporalFacts(categories of its lines)`; errors logged, never break checkout); test
`tests/test_agent_memory.py`.

**Interfaces — Produces:**
- `MemoryBlock = {"facts": list[str], "summary": str, "latest": list[dict]}`.
- `MemoryReader(clients, embedder).context(user_id, conversation_id, message) -> MemoryBlock`: embeds
  `message + " " + summary[:300]` (query), `SearchFacts(limit=5, min_score=0.45)`, `GetConversation` summary, last 2
  messages. `search(user_id, query) -> list[str]` for `recall_facts`.
- `MemoryWorker(clients, llm, embedder, settings)`: one asyncio queue, one consumer. `submit(user_id, conversation_id,
  user_text, assistant_text)`; job = (a) if `may_hold_fact(user_text)` → `complete_json(model=memory_model)` returning
  `{"changes": [{op, kind, key, value, category}]}` (category must be one of the 10 shop categories or ""), embed each
  remembered `key: value` (document), `ApplyFacts`; (b) summary: `complete_json(model=memory_model)` with old summary
  + this exchange → `{"summary"}`, cut to 200 words, `SetSummary`. Failures are logged and skipped. `close()` drains.
- `may_hold_fact(text: str) -> bool`: true for remember/forget/"call me"/"from now on", or a non-question clause with
  "i"/"i'm"/"my"/"i need"/"i want"/"looking for"; false for plain questions ("do you have hoodies?").

- [ ] **Step 1: Failing tests:** `test_may_hold_fact_cases` (≥10 cases both ways),
  `test_worker_saves_permanent_and_temporal` (fake LLM output), `test_summary_capped_200_words`,
  `test_context_top5_only` (8 facts stored → 5 sent), `test_context_without_voyage_uses_keywords`,
  `test_confirmed_order_clears_temporal_category` (checkout_flow test with a fake assistant stub),
  `test_clear_failure_does_not_break_checkout`.
- [ ] **Step 2-4:** FAIL → implement → `pytest tests/test_agent_memory.py tests/test_api_orders.py -q` PASS. **Step 5:** Commit.

### Task 8: Chat REST endpoints

**Files:** Modify `orchestrator/routes_chat.py`, `orchestrator/schemas.py`; test `tests/test_api.py` (chat section)
or new `tests/test_api_chat.py`.

**Interfaces — Produces (all require sign-in, 404 for another user's id):**
- `GET /api/chat/conversations` → `[{conversation_id, title, updated_at}]`
- `GET /api/chat/conversations/{id}/messages` → `[{role, text, cards, created_at}]`
- `PATCH /api/chat/conversations/{id}` `{title}` (1-80 chars) → conversation; `DELETE /api/chat/conversations/{id}` → `{ok}`
- `POST /api/chat` unchanged shape; for signed-in users `conversation_id` names a saved conversation (created if new).
- Confirm of `checkout_redirect` → publishes `navigate {path: "/checkout"}` on channel `chat`; never `start_checkout`.
  Remove the old `checkout` branch.

- [ ] **Step 1:** Failing tests: list/messages/rename/delete happy path; `test_other_users_conversation_404` for all
  four; `test_confirm_checkout_redirect_navigates_only` (no order created; navigate event seen on the WS).
- [ ] **Step 2-4:** FAIL → implement → `pytest tests/test_api*.py -q` PASS. **Step 5:** Commit.

### Task 9: Web chat panel

**Files:** Modify `web/src/features/chat/{ChatContext.tsx,ChatPanel.tsx,ProposalCard.tsx,transcript.ts,trace.ts,chatStorage.ts}`,
`web/src/api/endpoints.ts`, `web/src/features/session/events.ts`; create `web/src/features/chat/ChatList.tsx`
(+ `.module.css` reusing panel styles); tests next to the files (`*.test.ts`).

**Interfaces — Consumes:** Task 8 endpoints, `navigate` event.
- Signed-in: a "Chats" view in the panel (newest first; open, rename inline, delete with confirm; "New chat").
  Opening loads `GET .../messages` into the transcript (user + assistant turns with their cards). sessionStorage keeps
  only the current conversation id for signed-in users; guests keep today's behaviour.
- `navigate` event → `useNavigate()(path)`. `checkout_redirect` proposal: button "Yes, check out" / "Not now";
  confirm → server sends `navigate` to `/checkout`.
- [ ] **Step 1:** Failing tests: `transcript.test.ts` (`fromHistory(messages)` builds turns with cards),
  `chatStorage.test.ts` (signed-in stores only the id), `events.test.ts` (`toNavigate` parses `{path}` and rejects
  absolute URLs / anything not starting with `/`).
- [ ] **Step 2-4:** FAIL → implement → from `D:/onboarding_tasks/WSGRPC/web`: `npm run typecheck && npm test && npm run build` PASS.
- [ ] **Step 5:** Commit `"Web: chat history list, server-loaded transcripts, navigate, checkout redirect"`.

### Task 10: Eval, docs, end-to-end check

**Files:** Modify `scripts/eval_agent.py` (score the new loop's first tool call + args with a recorded/real LLM),
`tests/eval/queries.jsonl` (add the 5 reported bugs: remove exact item, ambiguous remove asks, add with variant,
names not ids, follow-up "the second one"), `tests/test_eval_scorer.py`; `README.md`, `docs/PLAN.md` (assistant
service, env vars, memory rules, token budget).

- [ ] **Step 1:** Update scorer tests; run `pytest tests/test_eval_scorer.py -q`: PASS.
- [ ] **Step 2:** Full suite: `pytest -q` → all pass (report count). Web: typecheck/test/build pass.
- [ ] **Step 3:** `python scripts/run_all.py` (port 8011 if 8000 is busy; never stop 8000), signed-in manual script:
  search hoodies → add 2nd in M navy → "remove the hoodie" with 2 lines (asks) → "the L one" → new chat "what size am I?"
  → "I need a new shirt, mine tore" → check out (asks, Yes opens /checkout). Log lines show prompt tokens; simple turn
  < 2,000, average ≈ 3,000.
- [ ] **Step 4:** Commit `"Eval and docs for the assistant rework"`.
