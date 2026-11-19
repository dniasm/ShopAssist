import type { SocketChannel, SocketMessage } from "../../api/types";

/** The newest events kept so a screen that mounts after they arrived (the checkout page after a refresh, when the socket's
 *  replay lands before the page subscribes) still sees them. Matches the server's per-user buffer order of magnitude. */
export const EVENT_LOG_LIMIT = 300;

export function appendEvent(log: readonly SocketMessage[], envelope: SocketMessage, limit: number = EVENT_LOG_LIMIT): SocketMessage[] {
  const next = [...log, envelope];
  return next.length > limit ? next.slice(next.length - limit) : next;
}

export function eventsOn(log: readonly SocketMessage[], channel: SocketChannel): SocketMessage[] {
  return log.filter((e) => e.channel === channel);
}
