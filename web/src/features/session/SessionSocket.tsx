import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { SocketChannel, SocketMessage } from "../../api/types";
import { useAuth } from "../auth/AuthContext";
import { appendEvent, eventsOn } from "./eventLog";
import { parseSocketMessage } from "./events";
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

function socketUrl(token: string, after: number): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/api/ws?token=${encodeURIComponent(token)}&after=${after}`;
}

/** One WebSocket per signed-in tab. All the connection rules live in socketMachine.ts; this component only performs the
 *  effects it asks for (open a socket, wait, deliver an event, close, sign out) and feeds it what happens. */
export function SessionSocketProvider({ children }: { children: ReactNode }) {
  const { token, signOut } = useAuth();
  const [status, setStatus] = useState<MachineStatus>("idle");

  const machine = useRef<MachineState>(initialMachineState());
  const socket = useRef<WebSocket | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const log = useRef<SocketMessage[]>([]);
  const subscribers = useRef(new Map<SocketChannel, Set<SocketHandler>>());
  const tokenRef = useRef(token);
  tokenRef.current = token;
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
          if (!currentToken) return;
          const ws = new WebSocket(socketUrl(currentToken, effect.after));
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
          signOutRef.current();
          return;
      }
    }
  }, []);

  // A new token (sign-in, switch user) is a new session: new seq space, new buffer, new socket.
  useEffect(() => {
    if (!token) return;
    machine.current = initialMachineState();
    log.current = [];
    dispatch({ type: "start" });
    return () => dispatch({ type: "stop" });
  }, [token, dispatch]);

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
