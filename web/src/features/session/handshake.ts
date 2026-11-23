/* How a session socket introduces itself. The URL carries no credentials (they would end up in server logs and browser
 * history): the client connects to /api/ws?after=<n> and sends ONE first message, `{"type":"auth","token":...}` for a
 * signed-in tab or `{"type":"auth","guest":...}` for a signed-out visitor. The server answers `system/auth_ok` (the socket
 * is then usable), or closes with 4401 (CLOSE_UNAUTHENTICATED in socketMachine.ts) when it did not accept the credentials. */

import type { SocketMessage } from "../../api/types";

export type SocketIdentity = { token: string } | { guest: string };

export function socketUrl(location: { protocol: string; host: string }, after: number): string {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/api/ws?after=${after}`;
}

/** The first frame to send once the socket is open. */
export function authFrame(who: SocketIdentity): string {
  return JSON.stringify("token" in who ? { type: "auth", token: who.token } : { type: "auth", guest: who.guest });
}

/** True for the server's reply to the auth frame; the provider reports the socket as open when it arrives. */
export function isAuthOk(envelope: SocketMessage): boolean {
  return envelope.channel === "system" && envelope.type === "auth_ok";
}
