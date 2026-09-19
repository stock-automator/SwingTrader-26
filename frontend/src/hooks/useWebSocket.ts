import { useEffect, useRef, useState } from "react";

export type WebSocketStatus = "connecting" | "open" | "closed";

export interface UseWebSocketOptions {
  /** Called for every message that parses as JSON. Malformed frames are
   * dropped silently - the same tolerance `ScreenerGrid`'s own inline
   * WebSocket handling already uses for `/ws/screener`. */
  onMessage?: (data: unknown) => void;
  /** Reconnect delay ceiling, ms. Default 30s. */
  maxBackoffMs?: number;
  /** Reconnect delay for the first retry, ms. Default 1s. */
  baseBackoffMs?: number;
}

/**
 * Auto-reconnecting WebSocket with exponential backoff.
 *
 * The reconnect delay doubles on every consecutive drop (capped at
 * `maxBackoffMs`) and resets to `baseBackoffMs` the instant a connection
 * successfully opens - a socket that's actually down backs off
 * increasingly rather than hammering the server, while a one-off drop on
 * an otherwise-healthy connection recovers on the next attempt at the
 * fast interval, not wherever the backoff had climbed to.
 *
 * Pass `url: null` to stay disconnected (e.g. while a required query
 * param isn't known yet) - the effect no-ops until a real URL is passed.
 */
export function useWebSocket(
  url: string | null,
  options: UseWebSocketOptions = {},
): WebSocketStatus {
  const { onMessage, maxBackoffMs = 30_000, baseBackoffMs = 1_000 } = options;
  const [status, setStatus] = useState<WebSocketStatus>("connecting");

  // Held in a ref (not a dependency) so a caller passing a fresh
  // `onMessage` closure every render doesn't tear down and reopen the
  // socket - only `url` should do that. Updated in an effect (not
  // during render) so it never runs ahead of React committing the render
  // it came from.
  const onMessageRef = useRef(onMessage);
  useEffect(() => {
    onMessageRef.current = onMessage;
  });

  useEffect(() => {
    if (!url) {
      setStatus("closed");
      return;
    }

    let cancelled = false;
    let attempt = 0;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (cancelled) return;
      setStatus("connecting");

      let ws: WebSocket;
      try {
        ws = new WebSocket(url as string);
      } catch {
        scheduleReconnect();
        return;
      }
      socket = ws;

      ws.onopen = () => {
        attempt = 0;
        if (!cancelled) setStatus("open");
      };
      ws.onmessage = (event) => {
        try {
          onMessageRef.current?.(JSON.parse(event.data));
        } catch {
          // ignore malformed frames
        }
      };
      ws.onclose = () => {
        if (cancelled) return;
        setStatus("closed");
        scheduleReconnect();
      };
      ws.onerror = () => {
        ws.close();
      };
    }

    function scheduleReconnect() {
      const delay = Math.min(baseBackoffMs * 2 ** attempt, maxBackoffMs);
      attempt += 1;
      reconnectTimer = setTimeout(connect, delay);
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [url, baseBackoffMs, maxBackoffMs]);

  return status;
}
