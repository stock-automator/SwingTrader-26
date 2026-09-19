import { useCallback, useRef } from "react";
import { toast } from "sonner";
import { liveFeedWebSocketUrl } from "../../lib/api";
import { useWebSocket } from "../../hooks/useWebSocket";
import type {
  LiveFeedEvent,
  MarketHealthState,
  RoutedOrder,
  ScreenerSetup,
} from "../../types";

function setupKey(s: ScreenerSetup): string {
  // One `/ws/v1/live-feed` signal frame scans a single strategy at a time
  // (unlike the Dashboard's `OpportunitySetup`, which spans several), so
  // ticker alone is already unique within a frame.
  return s.ticker;
}

const REGIME_TOAST: Record<MarketHealthState, { message: string; kind: "success" | "warning" | "error" }> = {
  BULL_CONFIRMED: { message: "Market regime: BULL CONFIRMED 🟢", kind: "success" },
  CAUTION_CHOP: { message: "Market regime: CAUTION CHOP 🟡", kind: "warning" },
  BEAR_DEFENSIVE: { message: "Market regime: BEAR DEFENSIVE 🔴", kind: "error" },
};

/**
 * Invisible: connects to `/ws/v1/live-feed` once and turns arriving events
 * into toast notifications - new non-flat setups, order fills/rejections,
 * and market regime state changes. Mounted once near the app root so it
 * keeps listening regardless of which tab is active, the same "always
 * mounted" reasoning `App.tsx` already applies to `ScreenerGrid`'s own
 * WebSocket.
 */
export function LiveFeedListener() {
  const seenSetupKeys = useRef<Set<string> | null>(null);
  const seenOrderIds = useRef<Set<string>>(new Set());
  const lastRegimeState = useRef<MarketHealthState | null>(null);

  const handleMessage = useCallback((data: unknown) => {
    const event = data as LiveFeedEvent;
    if (!event || typeof event !== "object" || !("type" in event)) return;

    switch (event.type) {
      case "regime": {
        if (
          lastRegimeState.current !== null &&
          lastRegimeState.current !== event.state
        ) {
          const { message, kind } = REGIME_TOAST[event.state];
          toast[kind](message);
        }
        lastRegimeState.current = event.state;
        break;
      }
      case "signal": {
        const active = event.setups.filter((s) => s.direction !== "FLAT");
        const keys = new Set(active.map(setupKey));
        if (seenSetupKeys.current !== null) {
          for (const setup of active) {
            if (!seenSetupKeys.current.has(setupKey(setup))) {
              toast.info(`New ${setup.direction} setup: ${setup.ticker}`, {
                description: setup.note ?? undefined,
              });
            }
          }
        }
        seenSetupKeys.current = keys;
        break;
      }
      case "order_update": {
        for (const order of event.orders as RoutedOrder[]) {
          if (seenOrderIds.current.has(order.order_id)) continue;
          seenOrderIds.current.add(order.order_id);
          if (order.status === "FILLED") {
            toast.success(
              `${order.side.toUpperCase()} ${order.qty} ${order.ticker} filled @ ${order.fill_price?.toFixed(2)}`,
            );
          } else if (order.status === "REJECTED") {
            toast.error(`Order rejected: ${order.ticker} - ${order.error ?? "unknown error"}`);
          } else if (order.status === "CANCELLED") {
            toast.info(`Order cancelled: ${order.ticker}`);
          }
        }
        break;
      }
      case "error":
        break;
    }
  }, []);

  useWebSocket(liveFeedWebSocketUrl(), { onMessage: handleMessage });

  return null;
}
