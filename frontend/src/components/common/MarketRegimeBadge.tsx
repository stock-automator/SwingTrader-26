import { useEffect, useState } from "react";
import { getMarketRegime } from "../../lib/api";
import type { MarketHealthState, MarketRegimeResponse } from "../../types";

const POLL_INTERVAL_MS = 60_000;

const STATE_DISPLAY: Record<
  MarketHealthState,
  { emoji: string; label: string; className: string }
> = {
  BULL_CONFIRMED: {
    emoji: "🟢",
    label: "BULL CONFIRMED",
    className: "border-long-dim bg-long-dim/30 text-long",
  },
  CAUTION_CHOP: {
    emoji: "🟡",
    label: "CAUTION CHOP",
    className: "border-amber-dim bg-amber-dim/30 text-amber",
  },
  BEAR_DEFENSIVE: {
    emoji: "🔴",
    label: "BEAR DEFENSIVE",
    className: "border-short-dim bg-short-dim/30 text-short",
  },
};

/** Top-level "Market Traffic Light" status badge: SPY/QQQ EMA alignment,
 * S&P 500 breadth, and VIX regime combined into one state, backed by
 * `GET /api/v1/market/regime`. Shared across any view that wants a market
 * health indicator (`components/common/` - not re-declared per view). */
export function MarketRegimeBadge() {
  const [report, setReport] = useState<MarketRegimeResponse | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    function poll() {
      getMarketRegime()
        .then((data) => {
          if (!cancelled) {
            setReport(data);
            setError(false);
          }
        })
        .catch(() => {
          if (!cancelled) setError(true);
        });
    }

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (error || !report) {
    return (
      <span
        data-testid="market-regime-badge"
        className="rounded border border-border bg-panel-alt px-2 py-1 text-xs font-medium text-text-faint"
      >
        ⚫ MARKET REGIME UNAVAILABLE
      </span>
    );
  }

  const { emoji, label, className } = STATE_DISPLAY[report.state];
  const vix = report.vix_level !== null ? report.vix_level.toFixed(1) : "—";
  const breadth = report.breadth_pct !== null ? `${report.breadth_pct.toFixed(0)}%` : "—";
  const tooltip =
    `SPY: ${report.spy_alignment} · QQQ: ${report.qqq_alignment}\n` +
    `Breadth: ${breadth} (${report.breadth_above}/${report.breadth_total} above 50-EMA)\n` +
    `VIX: ${vix} (${report.vix_regime})`;

  return (
    <span
      data-testid="market-regime-badge"
      title={tooltip}
      className={`rounded border px-2 py-1 text-xs font-semibold ${className}`}
    >
      {emoji} {label}
    </span>
  );
}
