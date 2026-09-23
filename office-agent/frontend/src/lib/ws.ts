import type { WsClientMessage, WsServerEvent } from "../types/wire";

/** Same thread-id-in-URL convention app.js used: reuse ?thread= from the
 * query string, generate+persist one via history.replaceState otherwise. */
export function resolveThreadId(): string {
  const url = new URL(window.location.href);
  const existing = url.searchParams.get("thread");
  if (existing) return existing;
  const id = crypto.randomUUID().slice(0, 8);
  url.searchParams.set("thread", id);
  window.history.replaceState(null, "", url.toString());
  return id;
}

export type ConnectionStatus = "connecting" | "open" | "reconnecting";

export interface AgentSocket {
  send: (message: WsClientMessage) => void;
  close: () => void;
}

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 15000;

/** Thin wrapper around the /ws/{threadId} connection: dispatches every
 * parsed server event to `onEvent`, exposes a typed `send`.
 *
 * Reconnect/resume is genuinely new behavior, not a port -- app.js has no
 * equivalent, a dropped socket there needs a manual page reload. On an
 * unexpected close (anything other than our own close() call), retries
 * with exponential backoff (1s, 2s, 4s, ... capped at 15s) until it
 * reconnects. Resync relies entirely on the backend's own behavior of
 * sending a fresh "state" + "history" pair on every (re)connection (see
 * runtime_lg's connect handler) -- the reducer's existing "history" case
 * replaces state.items wholesale.
 *
 * That wholesale replacement is exactly why send() can't just fire the
 * instant the socket's `open` event does: `open` only means the TCP/WS
 * handshake finished, not that this connection's own "state"+"history"
 * pair (sent by the server right after accept(), asynchronously, on its
 * own schedule) has been received and applied yet. A message sent in that
 * gap arrives at the server fine, but the reply and the not-yet-arrived
 * "history" event race on the client: if "history" (still reflecting
 * pre-turn state -- the server sent it before it ever read the message
 * off the socket) lands after the optimistic local bubble was added, it
 * wholesale-replaces items and wipes that bubble back out, while the
 * turn itself keeps running server-side with no visible trace of what
 * was asked. Caught live: a send fired the instant the page painted
 * routinely beat a real "history" round trip even on localhost. Fixed by
 * queueing here and flushing only once this connection's own first
 * "history" event has actually been seen -- not on `open`, and reset on
 * every reconnect, since a fresh reconnect gets its own fresh
 * state+history pair to wait for. */
export function connect(
  threadId: string,
  onEvent: (event: WsServerEvent) => void,
  onStatusChange?: (status: ConnectionStatus) => void,
): AgentSocket {
  let deliberatelyClosed = false;
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let backoffMs = INITIAL_BACKOFF_MS;
  let readyToSend = false;
  let pendingBeforeReady: WsClientMessage[] = [];

  const openSocket = () => {
    readyToSend = false;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/${threadId}`);
    socket = ws;

    ws.addEventListener("open", () => {
      backoffMs = INITIAL_BACKOFF_MS;
      onStatusChange?.("open");
    });

    ws.addEventListener("message", (ev) => {
      // Frames already buffered on a socket being closed for a thread
      // switch belong to the old thread.
      if (deliberatelyClosed) return;
      let parsed: WsServerEvent;
      try {
        parsed = JSON.parse(ev.data);
      } catch {
        return;
      }
      onEvent(parsed);
      if (parsed.type === "history" && !readyToSend) {
        readyToSend = true;
        const queued = pendingBeforeReady;
        pendingBeforeReady = [];
        for (const message of queued) ws.send(JSON.stringify(message));
      }
    });

    ws.addEventListener("close", () => {
      if (deliberatelyClosed) return;
      onStatusChange?.("reconnecting");
      reconnectTimer = setTimeout(() => {
        backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
        openSocket();
      }, backoffMs);
    });
  };

  onStatusChange?.("connecting");
  openSocket();

  const send = (message: WsClientMessage) => {
    if (readyToSend && socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
    } else {
      pendingBeforeReady.push(message);
    }
  };

  const close = () => {
    deliberatelyClosed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    socket?.close();
  };

  return { send, close };
}
