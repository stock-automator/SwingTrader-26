import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { getLiveScreener, screenerWebSocketUrl } from "../lib/api";
import { fmtInt, fmtNum, fmtPct } from "../lib/format";
import {
  STRATEGIES,
  type MacroRegime,
  type ScreenerResponse,
  type ScreenerSetup,
  type Strategy,
} from "../types";
import { OrderTicketDrawer } from "./OrderTicketDrawer";

const DIRECTION_STYLES: Record<ScreenerSetup["direction"], string> = {
  LONG: "text-long bg-long-dim/40 border-long-dim",
  SHORT: "text-short bg-short-dim/40 border-short-dim",
  EXIT_LONG: "text-orange-400 bg-orange-900/30 border-orange-800",
  FLAT: "text-text-faint bg-transparent border-border-soft",
};

const REGIME_STYLES: Record<ScreenerSetup["regime"], string> = {
  BULL_TREND: "text-long",
  BEAR_TREND: "text-short",
  CHOPPY: "text-amber",
  UNKNOWN: "text-text-faint",
};

function ConnectionDot({ live }: { live: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-text-dim">
      <span
        className={`inline-block h-2 w-2 rounded-full ${live ? "bg-long" : "bg-text-faint"}`}
      />
      {live ? "live" : "polling"}
    </span>
  );
}

function RMultipleBadge({
  reward_risk_ratio,
}: {
  reward_risk_ratio: number | null;
}) {
  if (reward_risk_ratio === null)
    return <span className="text-text-faint">—</span>;
  const strong = reward_risk_ratio >= 2.5;
  return (
    <span
      className={`inline-block rounded border px-1.5 py-0.5 text-xs font-semibold ${
        strong
          ? "border-long-dim bg-long-dim/40 text-long"
          : "border-amber-dim bg-amber-dim/40 text-amber"
      }`}
    >
      {reward_risk_ratio.toFixed(1)}R
    </span>
  );
}

interface ScreenerGridProps {
  onRegimeChange?: (regime: MacroRegime, circuitBreakerActive: boolean) => void;
}

export function ScreenerGrid({ onRegimeChange }: ScreenerGridProps = {}) {
  const [strategy, setStrategy] = useState<Strategy>("donchian_breakout");
  const [accountEquity, setAccountEquity] = useState(1000);
  const [riskPct, setRiskPct] = useState(0.02);
  const [data, setData] = useState<ScreenerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [wsLive, setWsLive] = useState(false);
  const [hideFlat, setHideFlat] = useState(true);
  const [earningsBlackout, setEarningsBlackout] = useState(false);
  const [ticketSetup, setTicketSetup] = useState<ScreenerSetup | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const fetchOnce = useCallback(() => {
    setLoading(true);
    getLiveScreener({
      strategy,
      account_equity: accountEquity,
      risk_per_trade_pct: riskPct,
      earnings_blackout: earningsBlackout,
    })
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, [strategy, accountEquity, riskPct, earningsBlackout]);

  useEffect(() => {
    if (data) onRegimeChange?.(data.macro_regime, data.circuit_breaker_active);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // Initial load + whenever params change, refetch once (covers the case the
  // websocket is unavailable) and (re)open the live socket.
  useEffect(() => {
    fetchOnce();

    wsRef.current?.close();
    setWsLive(false);
    const url = screenerWebSocketUrl({
      strategy,
      account_equity: accountEquity,
      risk_per_trade_pct: riskPct,
      earnings_blackout: earningsBlackout,
    });
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => setWsLive(true);
    ws.onclose = () => setWsLive(false);
    ws.onerror = () => setWsLive(false);
    ws.onmessage = (event) => {
      try {
        const frame = JSON.parse(event.data);
        if (frame && typeof frame === "object" && "error" in frame) {
          setError(String(frame.error));
          return;
        }
        setData(frame as ScreenerResponse);
        setError(null);
      } catch {
        // ignore malformed frames
      }
    };

    return () => {
      ws.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy, accountEquity, riskPct, earningsBlackout]);

  const setups = data?.setups ?? [];
  const visibleSetups = hideFlat
    ? setups.filter((s) => s.direction !== "FLAT")
    : setups;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-4 rounded border border-border bg-panel p-3">
        <label className="flex flex-col gap-1 text-xs text-text-dim">
          Strategy
          <select
            value={strategy}
            onChange={(e) => setStrategy(e.target.value as Strategy)}
            className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
          >
            {STRATEGIES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-text-dim">
          Account Equity
          <input
            type="number"
            value={accountEquity}
            min={0}
            onChange={(e) => setAccountEquity(Number(e.target.value))}
            className="w-28 rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-text-dim">
          Risk / Trade
          <input
            type="number"
            value={riskPct}
            min={0}
            max={1}
            step={0.005}
            onChange={(e) => setRiskPct(Number(e.target.value))}
            className="w-24 rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
          />
        </label>
        <label className="flex items-center gap-2 text-xs text-text-dim">
          <input
            type="checkbox"
            checked={hideFlat}
            onChange={(e) => setHideFlat(e.target.checked)}
            className="accent-accent"
          />
          Hide flat
        </label>
        <label className="flex items-center gap-2 text-xs text-text-dim">
          <input
            type="checkbox"
            checked={earningsBlackout}
            onChange={(e) => setEarningsBlackout(e.target.checked)}
            className="accent-accent"
          />
          Earnings blackout
        </label>
        <button
          onClick={fetchOnce}
          disabled={loading}
          className="flex items-center gap-1.5 rounded border border-border bg-panel-alt px-3 py-1.5 text-sm text-text hover:border-accent disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          {loading ? "Scanning…" : "Rescan"}
        </button>
        <div className="ml-auto flex items-center gap-3">
          <ConnectionDot live={wsLive} />
          {data && (
            <span className="text-xs text-text-dim">
              scanned {data.scanned} · skipped {data.skipped}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short">
          {error}
        </div>
      )}

      {data?.warnings && data.warnings.length > 0 && (
        <div className="rounded border border-amber-dim bg-amber-dim/20 px-3 py-2 text-xs text-amber">
          {data.warnings.join(" · ")}
        </div>
      )}

      <div className="overflow-x-auto rounded border border-border bg-panel">
        <table className="w-full min-w-[1000px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-left text-[11px] uppercase tracking-wider text-text-faint">
              <th className="px-3 py-2">Ticker</th>
              <th className="px-3 py-2">Direction</th>
              <th className="px-3 py-2">Regime</th>
              <th className="px-3 py-2 text-right">Close</th>
              <th className="px-3 py-2 text-right">Entry</th>
              <th className="px-3 py-2 text-right">Stop</th>
              <th className="px-3 py-2 text-right">Target</th>
              <th className="px-3 py-2 text-right">Shares</th>
              <th className="px-3 py-2 text-right">R</th>
              <th className="px-3 py-2 text-right">ADX</th>
              <th className="px-3 py-2 text-right">Rel. Strength</th>
              <th className="px-3 py-2 text-right">Rank</th>
              <th className="px-3 py-2">Note</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {visibleSetups.map((s) => (
              <tr
                key={s.ticker}
                data-testid="setup-row"
                className="border-b border-border-soft last:border-0 hover:bg-panel-alt"
              >
                <td className="px-3 py-2 font-semibold">{s.ticker}</td>
                <td className="px-3 py-2">
                  <span
                    className={`inline-block rounded border px-1.5 py-0.5 text-xs font-medium ${DIRECTION_STYLES[s.direction]}`}
                  >
                    {s.direction}
                  </span>
                </td>
                <td className={`px-3 py-2 text-xs ${REGIME_STYLES[s.regime]}`}>
                  {s.regime}
                </td>
                <td className="px-3 py-2 text-right">{fmtNum(s.close)}</td>
                <td className="px-3 py-2 text-right text-text-dim">
                  {fmtNum(s.entry_price)}
                </td>
                <td className="px-3 py-2 text-right text-short">
                  {fmtNum(s.stop_loss)}
                </td>
                <td className="px-3 py-2 text-right text-long">
                  {fmtNum(s.take_profit)}
                </td>
                <td className="px-3 py-2 text-right">{fmtInt(s.shares)}</td>
                <td className="px-3 py-2 text-right">
                  <RMultipleBadge reward_risk_ratio={s.reward_risk_ratio} />
                </td>
                <td className="px-3 py-2 text-right text-text-dim">
                  {fmtNum(s.adx, 1)}
                </td>
                <td className="px-3 py-2 text-right">
                  {s.relative_strength === null
                    ? "—"
                    : fmtPct(s.relative_strength * 100, 1)}
                </td>
                <td className="px-3 py-2 text-right text-text-dim">
                  {s.rank ?? "—"}
                </td>
                <td
                  className="max-w-[220px] truncate px-3 py-2 text-xs text-text-dim"
                  title={s.note ?? ""}
                >
                  {s.note ?? ""}
                </td>
                <td className="px-3 py-2 text-right">
                  {s.direction === "LONG" && s.tradable && (
                    <button
                      onClick={() => setTicketSetup(s)}
                      data-testid="order-ticket-button"
                      className="rounded border border-border bg-panel-alt px-2 py-1 text-xs text-text hover:border-accent"
                    >
                      🎫 Ticket
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {visibleSetups.length === 0 && !loading && (
              <tr>
                <td
                  colSpan={14}
                  className="px-3 py-8 text-center text-text-faint"
                >
                  No setups to show.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <OrderTicketDrawer
        setup={ticketSetup}
        onClose={() => setTicketSetup(null)}
      />
    </div>
  );
}
