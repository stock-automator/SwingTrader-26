import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  getJournalDecay,
  getJournalSummary,
  getJournalTrades,
  getMaeMfeDistribution,
} from "../../lib/api";
import { fmtCurrency, fmtInt, fmtPct, signClass } from "../../lib/format";
import { StatCard } from "../StatCard";
import type {
  JournalDecay,
  JournalSummary,
  JournalTrade,
  MaeMfeDistributionPoint,
} from "../../types";
import { DecayPanel } from "./DecayPanel";
import { MaeMfeScatterChart } from "./MaeMfeScatterChart";
import { JournalTradesTable } from "./JournalTradesTable";

export function JournalDashboard() {
  const [summary, setSummary] = useState<JournalSummary | null>(null);
  const [decay, setDecay] = useState<JournalDecay | null>(null);
  const [trades, setTrades] = useState<JournalTrade[]>([]);
  const [maeMfePoints, setMaeMfePoints] = useState<MaeMfeDistributionPoint[]>([]);
  const [maeMfeWarnings, setMaeMfeWarnings] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [summaryRes, decayRes, tradesRes, maeMfeRes] = await Promise.all([
        getJournalSummary(),
        getJournalDecay(),
        getJournalTrades(),
        getMaeMfeDistribution(),
      ]);
      setSummary(summaryRes);
      setDecay(decayRes);
      setTrades(tradesRes.trades);
      setMaeMfePoints(maeMfeRes.points);
      setMaeMfeWarnings(maeMfeRes.warnings);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold tracking-widest text-text">
          TRADE JOURNAL &amp; STRATEGY DECAY
        </h2>
        <button
          onClick={fetchAll}
          disabled={loading}
          data-testid="journal-refresh"
          className="flex items-center gap-1.5 rounded border border-border bg-panel-alt px-3 py-1.5 text-sm text-text hover:border-accent disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short">
          {error}
        </div>
      )}

      {summary && !summary.error && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label="Completed Trades" value={fmtInt(summary.completed_trades)} />
          <StatCard
            label="Win Rate"
            value={
              summary.win_rate === null ? "—" : fmtPct(summary.win_rate * 100, 1)
            }
          />
          <StatCard
            label="Expectancy"
            value={fmtCurrency(summary.avg_pnl, 2)}
            valueClassName={signClass(summary.avg_pnl)}
          />
          <StatCard
            label="Profit Factor"
            value={summary.profit_factor === null ? "—" : summary.profit_factor.toFixed(2)}
          />
        </div>
      )}

      {decay && summary && <DecayPanel decay={decay} summary={summary} />}

      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-faint">
          MAE / MFE Distribution
        </div>
        <MaeMfeScatterChart points={maeMfePoints} />
        {maeMfeWarnings.length > 0 && (
          <div className="mt-2 rounded border border-amber-dim bg-amber-dim/20 px-3 py-2 text-xs text-amber">
            {maeMfeWarnings.join(" · ")}
          </div>
        )}
      </div>

      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-faint">
          Trade Log
        </div>
        <JournalTradesTable trades={trades} />
      </div>
    </div>
  );
}
