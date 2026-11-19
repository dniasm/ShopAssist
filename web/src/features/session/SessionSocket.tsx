import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { SocketChannel, SocketMessage } from "../../api/types";
import { useAuth } from "../auth/AuthContext";
import { useDrawer } from "../drawer/DrawerContext";
import { appendEvent, eventsOn } from "./eventLog";
import { parseSocketMessage } from "./events";
import { guestIdForBrowser } from "./ids";
import { initialMachineState, reduceMachine, type MachineEffect, type MachineEvent, type MachineState, type MachineStatus } from "./socketMachine";

export type SocketHandler = (envelope: SocketMessage) => void;

interface SocketApi {
  status: MachineStatus;
  /** Calls `handler` for every future event on `channel`. With `replay`, first calls it for the buffered ones, in order,
   *  so a screen that mounts after a page refresh still sees what the socket replayed before it subscribed. */
  subscribe: (channel: SocketChannel, handler: SocketHandler, options?: { replay?: boolean }) => () => void;
}

const SocketContext = createContext<SocketApi | null>(null);

export function useSessionSocket(): SocketApi {
  const value = useContext(SocketContext);
  if (!value) throw new Error("useSessionSocket must be used inside <SessionSocketProvider>");
  return value;
}

/** Subscribes for as long as the component is mounted; the handler may change every render. */
export function useSocketChannel(channel: SocketChannel, handler: SocketHandler, options?: { replay?: boolean }): void {
  const { subscribe } = useSessionSocket();
  const latest = useRef(handler);
  latest.current = handler;
  const replay = options?.replay ?? false;
  useEffect(() => subscribe(channel, (env) => latest.current(env), { replay }), [subscribe, channel, replay]);
}

/** A signed-in tab connects with its token; a signed-out visitor with a guest id of their own (chat events only). */
function socketUrl(who: { token: string } | { guest: string }, after: number): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const identity = "token" in who ? `token=${encodeURIComponent(who.token)}` : `guest=${encodeURIComponent(who.guest)}`;
  return `${scheme}://${window.location.host}/api/ws?${identity}&after=${after}`;
}

/** One WebSocket per signed-in tab. All the connection rules live in socketMachine.ts; this component only performs the
 *  effects it asks for (open a socket, wait, deliver an event, close, sign out) and feeds it what happens. */
export function SessionSocketProvider({ children }: { children: ReactNode }) {
  const { token, signOut } = useAuth();
  const { open: drawer } = useDrawer();
  const [status, setStatus] = useState<MachineStatus>("idle");

  // A signed-out visitor only gets a socket once they open the assistant (and keeps it for the rest of the visit).
  const [guestWanted, setGuestWanted] = useState(false);
  useEffect(() => { if (drawer === "chat") setGuestWanted(true); }, [drawer]);
  const guest = !token && guestWanted ? guestIdForBrowser() : null;

  const machine = useRef<MachineState>(initialMachineState());
  const socket = useRef<WebSocket | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const log = useRef<SocketMessage[]>([]);
  const subscribers = useRef(new Map<SocketChannel, Set<SocketHandler>>());
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const guestRef = useRef(guest);
  guestRef.current = guest;
  const signOutRef = useRef(signOut);
  signOutRef.current = signOut;

  const dispatch = useCallback((event: MachineEvent) => {
    const step = reduceMachine(machine.current, event);
    machine.current = step.state;
    setStatus(step.state.status);
    for (const effect of step.effects) perform(effect);

    function perform(effect: MachineEffect) {
      switch (effect.type) {
        case "open": {
          const currentToken = tokenRef.current;
          const currentGuest = guestRef.current;
          const who = currentToken ? { token: currentToken } : currentGuest ? { guest: currentGuest } : null;
          if (!who) return;
          const ws = new WebSocket(socketUrl(who, effect.after));
          socket.current = ws;
          // Events from a socket that is no longer the current one (closed on purpose, replaced) are ignored.
          ws.onopen = () => { if (socket.current === ws) dispatch({ type: "opened" }); };
          ws.onmessage = (e: MessageEvent<unknown>) => {
            if (socket.current !== ws || typeof e.data !== "string") return;
            const envelope = parseSocketMessage(e.data);
            if (envelope) dispatch({ type: "message", envelope });
          };
          ws.onclose = (e: CloseEvent) => { if (socket.current === ws) { socket.current = null; dispatch({ type: "closed", code: e.code }); } };
          return;
        }
        case "schedule":
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => dispatch({ type: "timer" }), effect.delayMs);
          return;
        case "deliver": {
          log.current = appendEvent(log.current, effect.envelope);
          for (const handler of subscribers.current.get(effect.envelope.channel) ?? []) {
            try { handler(effect.envelope); } catch (err) { console.error("socket handler failed", err); }
          }
          return;
        }
        case "close": {
          window.clearTimeout(timer.current);
          const ws = socket.current;
          socket.current = null;
          ws?.close(1000, "client closing");
          return;
        }
        case "sign_out":
          if (tokenRef.current) signOutRef.current();      // a guest has no session to end
          return;
      }
    }
  }, []);

  // A new identity (sign-in, switch user, guest) is a new session: new seq space, new buffer, new socket.
  const identity = token ?? guest;
  useEffect(() => {
    if (!identity) return;
    machine.current = initialMachineState();
    log.current = [];
    dispatch({ type: "start" });
    return () => dispatch({ type: "stop" });
  }, [identity, dispatch]);

  const subscribe = useCallback<SocketApi["subscribe"]>((channel, handler, options) => {
    if (options?.replay) {
      for (const env of eventsOn(log.current, channel)) handler(env);
    }
    const set = subscribers.current.get(channel) ?? new Set<SocketHandler>();
    set.add(handler);
    subscribers.current.set(channel, set);
    return () => { set.delete(handler); };
  }, []);

  const value = useMemo<SocketApi>(() => ({ status, subscribe }), [status, subscribe]);
  return <SocketContext.Provider value={value}>{children}</SocketContext.Provider>;
}
