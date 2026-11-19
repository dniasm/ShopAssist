import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router";
import { confirmProposal, dismissProposal, listProducts, sendChat } from "../../api/endpoints";
import { ApiError, describeError } from "../../api/errors";
import type { ChatCard, ChatContext as ChatRequestContext } from "../../api/types";
import { useAuth } from "../auth/AuthContext";
import { useDrawer } from "../drawer/DrawerContext";
import { toChatMessage, toCheckoutEvent, toOrderStatus } from "../session/events";
import { useSocketChannel } from "../session/SessionSocket";
import { clearStored, loadStored, saveStored } from "./chatStorage";
import { SIGN_IN_PROMPT, classifySignedOut, searchQueryFrom } from "./signedOut";
import { chatReducer, initialChat, type ChatTurn } from "./transcript";

export interface PageProduct { id: string; name: string }

interface ChatApi {
  turns: ChatTurn[];
  signedIn: boolean;
  send: (text: string) => void;
  /** Sends a turn's own question again (after an error). */
  retry: (turnKey: string) => void;
  confirm: (proposalId: string) => void;
  dismiss: (proposalId: string) => void;
  clear: () => void;
  /** The product page the shopper is on, if any: sent as context and shown as a chip. */
  pageProduct: PageProduct | null;
  setPageProduct: (product: PageProduct | null) => void;
  /** Bumps whenever something asks the composer to take focus (the "/" key, "Ask about this"). */
  focusTick: number;
  openChat: () => void;
}

const ChatContext = createContext<ChatApi | null>(null);

export function useChat(): ChatApi {
  const value = useContext(ChatContext);
  if (!value) throw new Error("useChat must be used inside <ChatProvider>");
  return value;
}

/** Registers the product page being viewed for as long as the caller is mounted. */
export function useRegisterPageProduct(product: PageProduct | null): void {
  const { setPageProduct } = useChat();
  const id = product?.id ?? null;
  const name = product?.name ?? null;
  useEffect(() => {
    setPageProduct(id && name ? { id, name } : null);
    return () => setPageProduct(null);
  }, [id, name, setPageProduct]);
}

/** One chat per signed-in customer (and one for a signed-out visitor): switching user starts that user's own transcript. */
export function ChatProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return <ChatSession key={user?.user_id ?? "anon"} userId={user?.user_id ?? null}>{children}</ChatSession>;
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

function ChatSession({ userId, children }: { userId: string | null; children: ReactNode }) {
  const location = useLocation();
  const { show } = useDrawer();
  const [state, dispatch] = useReducer(chatReducer, userId, (id) => initialChat(loadStored(id)));
  const [pageProduct, setPageProduct] = useState<PageProduct | null>(null);
  const [focusTick, setFocusTick] = useState(0);

  // the freshest route and page product, for callbacks that must not go stale
  const live = useRef({ path: location.pathname, product: pageProduct });
  live.current = { path: location.pathname, product: pageProduct };
  const counter = useRef(0);
  const turnsRef = useRef(state.turns);
  turnsRef.current = state.turns;

  // what survives a refresh of the tab
  useEffect(() => { saveStored(userId, { texts: { ...state.texts }, decided: { ...state.decided } }); }, [userId, state.texts, state.decided]);

  // the assistant's side of every turn, and the checkout / order events that follow a confirmed proposal; the socket replays them after a refresh
  useSocketChannel("chat", (message) => {
    const m = toChatMessage(message);
    if (m) dispatch({ type: "chat", message: m });
  }, { replay: true });
  useSocketChannel("checkout", (message) => {
    const event = toCheckoutEvent(message);
    if (event) dispatch({ type: "checkout", seq: message.seq, event });
  }, { replay: true });
  useSocketChannel("order", (message) => {
    const data = toOrderStatus(message);
    if (data) dispatch({ type: "order_status", seq: message.seq, data });
  }, { replay: true });

  const openChat = useCallback(() => { show("chat"); setFocusTick((n) => n + 1); }, [show]);

  const runSignedOut = useCallback(async (localId: string, text: string) => {
    if (classifySignedOut(text) === "account") {
      dispatch({ type: "local_reply", localId, message: { text: SIGN_IN_PROMPT, cards: [], signIn: true }, source: "search" });
      return;
    }
    const query = searchQueryFrom(text);
    try {
      const found = await listProducts({ q: query, page_size: 4 });
      const cards: ChatCard[] = found.products.length ? [{ kind: "products", data: found.products }] : [];
      const message = found.products.length
        ? `Here is what I found for \u201C${query}\u201D${found.total > found.products.length ? ` (${found.total} in all; the shop page has the rest)` : ""}.`
        : `I couldn\u2019t find anything for \u201C${query}\u201D. Try fewer words, or a colour and a kind of product.`;
      dispatch({ type: "local_reply", localId, message: { text: message, cards }, source: "search" });
    } catch (err) {
      dispatch({ type: "send_failed", localId, message: describeError(err) });
    }
  }, []);

  const send = useCallback((raw: string) => {
    const text = raw.trim().slice(0, 500);
    if (!text) return;
    const localId = `local-${++counter.current}-${Date.now()}`;
    const context: ChatRequestContext = { path: live.current.path, ...(live.current.product ? { product_id: live.current.product.id } : {}) };
    dispatch({ type: "user_sent", localId, text, context });
    if (!userId) { void runSignedOut(localId, text); return; }
    sendChat({ message: text, context }).then(
      (accepted) => dispatch({ type: "accepted", localId, turnId: accepted.turn_id }),
      (err: unknown) => dispatch({ type: "send_failed", localId, message: describeError(err) }),
    );
  }, [userId, runSignedOut]);

  const retry = useCallback((turnKey: string) => {
    const turn = turnsRef.current.find((t) => t.key === turnKey);
    if (turn?.request) send(turn.request.text);
  }, [send]);

  const confirm = useCallback((id: string) => {
    dispatch({ type: "proposal_confirming", id });
    confirmProposal(id).then(
      () => dispatch({ type: "proposal_confirmed", id }),
      (err: unknown) => {
        if (err instanceof ApiError && err.status === 409) dispatch({ type: "proposal_handled", id, note: "This was already confirmed or dismissed, so nothing more happened." });
        else dispatch({ type: "proposal_failed", id, note: describeError(err) });
      },
    );
  }, []);

  const dismiss = useCallback((id: string) => {
    dispatch({ type: "proposal_dismissed", id });
    dismissProposal(id).catch(() => undefined);   // the shopper's choice stands here; the server only forgets the proposal
  }, []);

  const clear = useCallback(() => { dispatch({ type: "reset" }); clearStored(userId); }, [userId]);

  // "/" opens the assistant from anywhere, unless the shopper is typing in a field
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented || isTyping(e.target)) return;
      e.preventDefault();
      openChat();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [openChat]);

  const value = useMemo<ChatApi>(() => ({
    turns: state.turns, signedIn: userId !== null, send, retry, confirm, dismiss, clear, pageProduct, setPageProduct, focusTick, openChat,
  }), [state.turns, userId, send, retry, confirm, dismiss, clear, pageProduct, focusTick, openChat]);

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}
