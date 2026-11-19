import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { confirmProposal, dismissProposal, sendChat } from "../../api/endpoints";
import { ApiError, describeError } from "../../api/errors";
import { useAuth } from "../auth/AuthContext";
import { useDrawer } from "../drawer/DrawerContext";
import { toChatMessage, toCheckoutEvent, toOrderStatus } from "../session/events";
import { guestIdForBrowser, newId } from "../session/ids";
import { useSocketChannel } from "../session/SessionSocket";
import { clearStored, currentConversation, loadStored, rememberConversation, saveStored } from "./chatStorage";
import { chatContextFor } from "./context";
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
  /** "New chat": clears the transcript and starts a new conversation (a new conversation_id). */
  newChat: () => void;
  /** The product page the shopper is on, if any: shown as a chip (what is SENT comes from the route at send time). */
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

/** One chat per identity: signing in, signing out or switching customer mounts a fresh session, which starts a NEW conversation. */
export function ChatProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return <ChatSession key={user?.user_id ?? "anon"} userId={user?.user_id ?? null}>{children}</ChatSession>;
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

function ChatSession({ userId, children }: { userId: string | null; children: ReactNode }) {
  const identity = userId ?? "anon";
  const { show } = useDrawer();
  const [conversationId, setConversationId] = useState(() => currentConversation(identity));
  const [state, dispatch] = useReducer(chatReducer, conversationId, (id) => initialChat(loadStored(id)));
  const [pageProduct, setPageProduct] = useState<PageProduct | null>(null);
  const [focusTick, setFocusTick] = useState(0);

  const counter = useRef(0);
  const turnsRef = useRef(state.turns);
  turnsRef.current = state.turns;
  const conversationRef = useRef(conversationId);
  conversationRef.current = conversationId;

  // what survives a refresh of the tab, per conversation
  useEffect(() => { saveStored(conversationId, { texts: { ...state.texts }, decided: { ...state.decided } }); }, [conversationId, state.texts, state.decided]);

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

  const send = useCallback((raw: string) => {
    const text = raw.trim().slice(0, 500);
    if (!text) return;
    const localId = `local-${++counter.current}-${Date.now()}`;
    const context = chatContextFor(window.location.pathname);          // the page the shopper is on NOW, not when the drawer opened
    const conversation = conversationRef.current;
    dispatch({ type: "user_sent", localId, text, context });
    const body = { message: text, context, conversation_id: conversation, ...(userId ? {} : { guest_id: guestIdForBrowser() }) };
    sendChat(body).then(
      (accepted) => dispatch({ type: "accepted", localId, turnId: accepted.turn_id }),
      (err: unknown) => dispatch({ type: "send_failed", localId, message: describeError(err) }),
    );
  }, [userId]);

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

  const newChat = useCallback(() => {
    const next = newId();
    clearStored(conversationRef.current);
    rememberConversation(identity, next);
    conversationRef.current = next;
    setConversationId(next);
    dispatch({ type: "reset" });
  }, [identity]);

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
    turns: state.turns, signedIn: userId !== null, send, retry, confirm, dismiss, newChat, pageProduct, setPageProduct, focusTick, openChat,
  }), [state.turns, userId, send, retry, confirm, dismiss, newChat, pageProduct, focusTick, openChat]);

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}
