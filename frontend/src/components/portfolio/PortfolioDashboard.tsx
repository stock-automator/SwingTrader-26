import { useCallback, useEffect, useState } from "react";
import { RefreshCw, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import {
  ApiError,
  closeAllPositions,
  getAccount,
  getPortfolioHistory,
  getPositions,
} from "../../lib/api";
import { fmtCurrency, fmtPct, signClass } from "../../lib/format";
import { StatCard } from "../StatCard";
import type { AlpacaAccount, PortfolioHistory, Position } from "../../types";
import { PortfolioEquityChart } from "./PortfolioEquityChart";
import { PositionsTable } from "./PositionsTable";
import { CloseAllModal } from "./CloseAllModal";

const REFRESH_MS = 30000;

function NotConfiguredNotice() {
  return (
    <div
      data-testid="portfolio-not-configured"
      className="flex items-start gap-3 rounded border border-amber-dim bg-amber-dim/20 px-4 py-3 text-sm text-amber"
    >
      <ShieldAlert size={18} className="mt-0.5 shrink-0" />
      <div>
        Alpaca paper trading isn't configured on this server — set{" "}
        <code className="rounded bg-black/30 px-1 py-0.5">ALPACA_API_KEY</code>{" "}
        /{" "}
        <code className="rounded bg-black/30 px-1 py-0.5">
          ALPACA_API_SECRET
        </code>{" "}
        to enable the Portfolio Dashboard.
      </div>
    </div>
  );
}

export function PortfolioDashboard() {
  const [account, setAccount] = useState<AlpacaAccount | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [history, setHistory] = useState<PortfolioHistory | null>(null);
  const [loading, setLoading] = useState(false);
  const [notConfigured, setNotConfigured] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closeAllOpen, setCloseAllOpen] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [accountRes, positionsRes, historyRes] = await Promise.all([
        getAccount(),
        getPositions(),
        getPortfolioHistory("1M", "1D"),
      ]);
      setAccount(accountRes);
      setPositions(positionsRes.positions);
      setHistory(historyRes);
      setNotConfigured(false);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setNotConfigured(true);
        setError(null);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchAll]);

  async function handleCloseAll() {
    try {
      const res = await closeAllPositions();
      toast.success(`Closed ${res.closed.length} position(s)`);
      setCloseAllOpen(false);
      await fetchAll();
    } catch (err) {
      toast.error(
        `Close all failed: ${err instanceof ApiError ? err.message : String(err)}`,
      );
    }
  }

  const dailyPnl =
    history && history.equity.length >= 2
      ? history.equity[history.equity.length - 1] -
        history.equity[history.equity.length - 2]
      : null;
  const dailyPnlPct =
    history && history.profit_loss_pct.length > 0
      ? history.profit_loss_pct[history.profit_loss_pct.length - 1]
      : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold tracking-widest text-text">
          PORTFOLIO &amp; EXECUTION
        </h2>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchAll}
            disabled={loading}
            data-testid="portfolio-refresh"
            className="flex items-center gap-1.5 rounded border border-border bg-panel-alt px-3 py-1.5 text-sm text-text hover:border-accent disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <button
            onClick={() => setCloseAllOpen(true)}
            disabled={notConfigured || positions.length === 0}
            data-testid="close-all-button"
            className="rounded border border-short bg-short-dim/30 px-3 py-1.5 text-sm font-semibold text-short hover:bg-short-dim/50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Close All Positions
          </button>
        </div>
      </div>

      {notConfigured && <NotConfiguredNotice />}
      {error && (
        <div className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short">
          {error}
        </div>
      )}

      {!notConfigured && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label="Equity" value={fmtCurrency(account?.equity, 2)} />
            <StatCard
              label="Buying Power"
              value={fmtCurrency(account?.buying_power, 2)}
            />
            <StatCard label="Cash" value={fmtCurrency(account?.cash, 2)} />
            <StatCard
              label="Daily P&L"
              value={fmtCurrency(dailyPnl, 2)}
              valueClassName={signClass(dailyPnl)}
              sub={dailyPnlPct === null ? undefined : fmtPct(dailyPnlPct * 100)}
            />
          </div>

          {history && history.equity.length > 0 && (
            <div className="rounded border border-border bg-panel p-3">
              <PortfolioEquityChart history={history} />
            </div>
          )}

          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-faint">
              Open Positions
            </div>
            <PositionsTable positions={positions} />
          </div>
        </>
      )}

      <CloseAllModal
        open={closeAllOpen}
        positionCount={positions.length}
        onCancel={() => setCloseAllOpen(false)}
        onConfirm={handleCloseAll}
      />
    </div>
  );
}
