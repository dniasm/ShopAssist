# Shopping assistant rework: design (2026-11-24)

## Why
The chat assistant hallucinates, removes the wrong cart item (the rule fallback takes the first product shown,
`orchestrator/agent/rules.py:284`), adds the wrong variant, talks in product ids (`prompt.py` asks for them; memory
stores `[shown: P-…]`), loses track on follow-ups (6 turns, 1 h, in RAM, tool results reduced to one line), forgets
everything across chats, and places orders itself instead of sending the shopper to checkout.

## Goals
1. The assistant can do everything the shop does except pay: search/browse (filters, sort, more results), product
   details, stock, add / change quantity / remove cart lines, list and track orders, propose cancelling an order,
   open a page (product, shop with filters, cart, orders).
2. Cart edits act on the exact line. If the request matches more than one line or variant, or names nothing
   ("remove it" with 2+ lines), it asks which one, listing them by name, size and colour. It never guesses.
3. It talks about products by name (ids stay internal; shown only if the shopper uses them).
4. Follow-ups work: it sees the real conversation and full tool results.
5. Chat history: signed-in shoppers can browse and reopen their past chats (full logs), rename and delete them.
6. Cross-chat memory is fact based: one `facts` list, permanent (overridable) and temporal (expire / cleared on
   purchase), retrieved by semantic search, not dumped into the prompt.
7. Context within a chat is a short rolling summary of the last 3-6 exchanges, fed to the system prompt.
8. "Pay / check out": it asks "Are you sure you'd like to check out?"; Yes opens `/checkout`. It never places an order.

## Non-goals / anti-goals
- No payment or order placement from chat. No new UI framework, no LangGraph, no Qdrant, no pgvector.
- Guests: no saved history or memory (current temporary chat stays).
- Anti-goal: any write that guesses which item was meant.

## Approaches (picked: A + A)
- Agent: **A rebuild on native tool calling** (chosen) vs B patch the one-action JSON planner (same root causes stay).
- Memory store: **A new `assistant` gRPC service with its own DB** (chosen, matches the five existing services)
  vs B tables in `customer` (less code, mixes chat data into accounts).

## Design
**Agent loop (orchestrator/agent/).** `LLMClient` gets a `chat(messages, tools)` call (OpenAI-compatible `tools`,
Groq `openai/gpt-oss-120b`, same key pool). Message order (static first, so Groq prompt caching can reuse it):
system prompt, tool definitions, then the memory block (matching facts, chat summary, a short cart summary), the
latest exchange verbatim, the page context and the new message. Tool calls run up to 6 steps; each result goes back
in compact form (below). The final text is the reply; cards come from the tool results with full data, as now.
If the LLM is unavailable, the old rule planner is used only for read-only lookups; for any cart write it replies
asking the shopper to try again (no guessing).

**Fewer calls per turn.** When a tool's result answers the request by itself (a cart change, a product list, an
order status) the turn ends with a fixed sentence built from the result ("Added Cozy Hoodie (M, navy). Cart total
$98.") plus its cards, without a second model call. The model is called again only when it has to reason about a
result (compare, nothing matched, ambiguity, a question about the products). The cart summary in the prompt means
"what's in my cart" / "remove the hoodie" need no lookup first.

**Tools (8, merged; same capabilities as before plus the new ones).**
- `search(query?, category?, color?, size?, brand?, min_price?, max_price?, sort?, page?)`
- `product(ids[1..3], size?, color?)`: details + stock for up to 3 products in one call (compare in one lookup)
- `cart(action: view|add|set_qty|remove, product_id?, size?, color?, quantity?, line_id?)`: `add` asks if the
  variant is missing/unknown; `set_qty`/`remove` take a `line_id` from the cart summary or a cart result only and
  reject an unknown one; several matching lines → ask which, by name, size, colour
- `orders(order_id?)`: no id lists the latest orders, an id gives details/tracking
- `cancel_order(order_id)`: confirm card, as now
- `checkout()`: confirm card "Are you sure you'd like to check out?"; confirming navigates to `/checkout`, nothing
  else. The old checkout-in-chat path is removed.
- `open_page(kind: product|shop|cart|orders|order, …)`: sends a `navigate` event the web app follows
- `recall_facts(query)`: fact lookup when the automatic search missed something
A wrong `action` or missing argument returns an error message to the model, which corrects it next step.

**Compact tool results.** Same facts, no JSON clutter. A product line:
`1. Trail Pack [P-1001] $79 · black, olive · S-XL · in stock · TPU-coated, roll-top, hip-belt, cycling`; a cart
line: `L2 Cozy Hoodie [P-1004] M navy ×1 $49`. Ids stay for tool calls; replies use names. Descriptions come only
from `product` (search lists carry tags instead).

**Product tags (catalog).** A `tags` field (3-5 short words: material, key feature, use) taken from each product's
description by a small rule-based parser when the catalog is seeded (descriptions follow a fixed pattern; no model).
Search results include them, so "which of these is waterproof / for cycling?" is answered from the list.

**Assistant service (services/assistant/, own DB + role like the others).** Tables:
`conversations(id, user_id, title, summary, created_at, updated_at)`, `messages(id, conversation_id, role, text,
cards jsonb, created_at)`, `facts(id, user_id, kind, key, value, category, embedding real[], created_at,
expires_at)`. RPCs: create/list/rename/delete conversation, append/list messages, set summary, upsert/forget/search
facts, clear temporal facts by category. Fact search = cosine in Python over the user's rows (tens, at most hundreds).

**Chat history.** Every message of a signed-in shopper is saved. The panel has a chat list (newest first, title =
first question, renamable); opening one shows its full log and lets them continue it. Delete removes the chat.

**Facts (one list, `facts`, two kinds).**
- `permanent`: stays until the shopper changes it: name, size(s), height, weight, colour/brand preferences, budget,
  style. Single-value keys are overridden by a new value ("I'm an L now" replaces M); list keys collect
  (`liked_brands`). "Forget my size" deletes. Never expires.
- `temporal`: a current need ("I need a new shirt, mine tore", "looking for a gift for my dad"). Stored with the
  shop category it is about (`t-shirts`) and `expires_at` = 30 days. Deleted as soon as an order containing a product
  of that category is placed (the orchestrator already watches order events, `orchestrator/order_watch.py`), or
  when it expires, whichever comes first. A need with no shop category just expires.
- Extraction runs after each reply in the background (one worker, smaller Groq model `openai/gpt-oss-20b`): it
  returns remember/forget items with kind, key, value, category; code decides replace/collect/delete (day 7
  `plan_changes` rules). A cheap pre-check skips messages that cannot hold a fact; "remember/forget" always runs.
- Facts are NOT put in the prompt wholesale. Each turn the message (plus the chat summary) is embedded (Voyage
  `voyage-4-lite`, `VOYAGE_API_KEY`) and the top 5 facts with score >= 0.45 are added. Each fact is embedded once,
  when saved. Without a Voyage key: keyword overlap on key/value instead. The model can also call `recall_facts(query)`
  when it needs something the automatic search missed ("what size am I?").

**Conversation context.** Per chat a rolling `summary` of the last 6 exchanges, at most ~200 words, rewritten after
each reply in the same background job (same small model, input = old summary + latest exchange). The prompt gets
that summary plus the latest exchange verbatim (my call: "remove the second one" needs the exact list just shown,
which a summary loses). Older messages stay in the log only.

**Prompt budget.** The binding limit is not the model's context (131k) but Groq's free tier: 8,000 tokens per
minute per key (2 keys today), and every call resends the prompt. Target per call: system prompt ~500, tool
definitions ~600, facts ~150 (up to 5), summary ~270 (~200 words), latest exchange ~250, message + page ~50,
tool results ~600 (6 compact product lines with tags). First call ~1,850; a second call ~2,500. A simple turn
(one call) ~1,850; one that needs reasoning about a result ~4,300; about 3,000 on average. Summary and facts are
kept at full size on purpose (memory loss is worse than tokens); the trims come from prompt, tools and results.
Check whether Groq's cached prompt tokens count toward TPM before relying on caching.

**Web (web/src/features/chat/).** A chat list in the panel (new chat, open, rename, delete) for signed-in users;
history loads from `GET /api/chat/conversations/{id}/messages` instead of sessionStorage. Live progress stays on the
WebSocket. New `navigate` event → router navigation. Checkout confirm card → `/checkout`.

**Wiring.** catalog `tags` (migration + seed + proto field), proto + generated code, `db_setup.py` (6th DB/role), `run_all.py`, `docker-compose.yml`, `.env.example`,
health page, CI unchanged except the extra DB.

## Acceptance
- Tests: remove/add ambiguity asks; remove acts on the given line only; unknown line id rejected; checkout never
  calls Order.Checkout; facts replace/collect/forget; facts and summaries deleted with their chat where they belong to it; conversations isolated per user.
- Tests: permanent fact overridden not duplicated; temporal fact removed when an order with its category is placed
  and when expired; only top-matching facts reach the prompt; summary stays within its word cap.
- `scripts/eval_agent.py` updated with the bug cases above; full pytest + web typecheck/test/build green.
- Token use logged per call; a simple turn stays under ~2,000 tokens, an average turn ~3,000.
- Manual run: the five reported bugs no longer reproduce.

## Left to the builder
Exact prompt wording, fact key list, summary prompt, 30-day TTL value, card shapes for new tools, chat-list styling (reuse existing panel styles).
