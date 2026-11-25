import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { confirmProposal, dismissProposal, getConversationMessages, sendChat } from "../../api/endpoints";
import { ApiError, describeError } from "../../api/errors";
import type { ChatWsMessage } from "../../api/types";
import { useAuth } from "../auth/AuthContext";
import { useDrawer } from "../drawer/DrawerContext";
import { safePath, toChatMessage, toOrderStatus } from "../session/events";
import { guestIdForBrowser, newId } from "../session/ids";
import { useSocketChannel } from "../session/SessionSocket";
import { clearStored, currentConversation, loadStored, persistable, rememberConversation, saveStored } from "./chatStorage";
import { chatContextFor } from "./context";
import { chatReducer, fromHistory, initialChat, type ChatTurn } from "./transcript";

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
  /** The conversation the transcript belongs to (signed in: the saved chat once the server has made it). */
  conversationId: string;
  /** Signed in: the saved conversation is being read from the server / could not be. */
  historyLoading: boolean;
  historyError: string | null;
  /** Signed in: shows a saved conversation; the next message continues it. */
  openConversation: (id: string) => void;
  /** Tells the chat a saved conversation was deleted: if it is the open one, starts a new chat. */
  conversationDeleted: (id: string) => void;
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
  const { show, close } = useDrawer();
  const navigate = useNavigate();
  const [conversationId, setConversationId] = useState(() => currentConversation(identity));
  // a guest's transcript comes back from sessionStorage; a signed-in one is read from the server
  const [state, dispatch] = useReducer(chatReducer, conversationId, (id) => initialChat(userId ? {} : loadStored(id)));
  const [history, setHistory] = useState<{ loading: boolean; error: string | null }>({ loading: userId !== null, error: null });
  const [pageProduct, setPageProduct] = useState<PageProduct | null>(null);
  const [focusTick, setFocusTick] = useState(0);

  const counter = useRef(0);
  const turnsRef = useRef(state.turns);
  turnsRef.current = state.turns;
  const conversationRef = useRef(conversationId);
  conversationRef.current = conversationId;

  const loadSeq = useRef(0);
  // turns started from this tab: only their navigate / conversation events are acted on (the socket replays old ones after a refresh)
  const sentTurns = useRef(new Set<string>());
  const inFlight = useRef(0);
  const early = useRef(new Map<string, ChatWsMessage[]>());   // such events that beat the POST response naming their turn

  // what survives a refresh of the tab, per conversation (a guest only)
  useEffect(() => {
    const stored = persistable(identity, { texts: { ...state.texts }, decided: { ...state.decided } });
    if (stored) saveStored(conversationId, stored);
  }, [identity, conversationId, state.texts, state.decided]);

  const switchTo = useCallback((id: string) => {
    rememberConversation(identity, id);
    conversationRef.current = id;
    setConversationId(id);
  }, [identity]);

  const loadHistory = useCallback((id: string, onlyIfEmpty: boolean) => {
    const mine = ++loadSeq.current;
    setHistory({ loading: true, error: null });
    getConversationMessages(id).then(
      (messages) => {
        if (mine !== loadSeq.current) return;
        if (!onlyIfEmpty || turnsRef.current.length === 0) dispatch({ type: "loaded", turns: fromHistory(messages) });
        setHistory({ loading: false, error: null });
      },
      (err: unknown) => {
        if (mine !== loadSeq.current) return;
        // a conversation the server has not saved yet (a fresh id) is simply empty
        setHistory({ loading: false, error: err instanceof ApiError && err.status === 404 ? null : describeError(err) });
      },
    );
  }, []);
  useEffect(() => { if (userId) loadHistory(conversationRef.current, true); }, [userId, loadHistory]);

  /** The server made (or moved to) a saved conversation for this turn, or wants a page opened. */
  const act = useCallback((m: ChatWsMessage) => {
    if (m.type === "conversation") {
      if (m.data.conversation_id !== conversationRef.current) switchTo(m.data.conversation_id);
    } else if (m.type === "navigate") {
      const path = safePath(m.data.path);
      if (!path) return;
      if (window.matchMedia("(max-width: 720px)").matches) close();   // on a phone the chat covers the page
      navigate(path);
    }
  }, [switchTo, close, navigate]);

  // the assistant's side of every turn, and the checkout / order events that follow a confirmed proposal; the socket replays them after a refresh
  useSocketChannel("chat", (message) => {
    const m = toChatMessage(message);
    if (!m) return;
    dispatch({ type: "chat", message: m });
    if (m.type !== "conversation" && m.type !== "navigate") return;
    if (sentTurns.current.has(m.data.turn_id)) act(m);
    else if (inFlight.current > 0) early.current.set(m.data.turn_id, [...(early.current.get(m.data.turn_id) ?? []), m]);
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
    inFlight.current += 1;
    const settle = () => { inFlight.current -= 1; if (inFlight.current === 0) early.current.clear(); };
    const body = { message: text, context, conversation_id: conversation, ...(userId ? {} : { guest_id: guestIdForBrowser() }) };
    sendChat(body).then(
      (accepted) => {
        sentTurns.current.add(accepted.turn_id);
        dispatch({ type: "accepted", localId, turnId: accepted.turn_id });
        const waiting = early.current.get(accepted.turn_id) ?? [];
        early.current.delete(accepted.turn_id);
        waiting.forEach(act);
        settle();
      },
      (err: unknown) => { dispatch({ type: "send_failed", localId, message: describeError(err) }); settle(); },
    );
  }, [userId, act]);

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
    clearStored(conversationRef.current);
    loadSeq.current += 1;                                 // a read of the old conversation must not land in the new one
    setHistory({ loading: false, error: null });
    switchTo(newId());
    dispatch({ type: "reset" });
  }, [switchTo]);

  const openConversation = useCallback((id: string) => {
    if (id === conversationRef.current) return;
    switchTo(id);
    dispatch({ type: "reset" });
    loadHistory(id, false);
  }, [switchTo, loadHistory]);

  const conversationDeleted = useCallback((id: string) => { if (id === conversationRef.current) newChat(); }, [newChat]);

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
    turns: state.turns, signedIn: userId !== null, send, retry, confirm, dismiss, newChat, conversationId,
    historyLoading: history.loading, historyError: history.error, openConversation, conversationDeleted,
    pageProduct, setPageProduct, focusTick, openChat,
  }), [state.turns, userId, send, retry, confirm, dismiss, newChat, conversationId, history, openConversation, conversationDeleted, pageProduct, focusTick, openChat]);

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}
