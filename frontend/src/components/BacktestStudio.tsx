import { useState, type FormEvent } from "react";
import { ApiError, runBacktest } from "../lib/api";
import { fmtCurrency, fmtNum, fmtPct, signClass } from "../lib/format";
import {
  STRATEGIES,
  type BacktestRequest,
  type BacktestResponse,
  type Strategy,
} from "../types";
import { StatCard } from "./StatCard";
import { EquityChart } from "./EquityChart";
import { TradesTable } from "./TradesTable";
import { WarningsBanner } from "./WarningsBanner";

interface FormState {
  strategy: Strategy;
  tickers: string;
  start: string;
  end: string;
  initial_capital: number;
  risk_per_trade_pct: number;
  commission: number;
  slippage_pct: number;
  risk_free_rate: number;
  include_buy_and_hold: boolean;
  benchmark: string;
  realistic_costs: boolean;
  earnings_blackout: boolean;
  regime_gating: boolean;
}

const DEFAULT_FORM: FormState = {
  strategy: "donchian_breakout",
  tickers: "AAPL",
  start: "",
  end: "",
  initial_capital: 1000,
  risk_per_trade_pct: 0.02,
  commission: 0.001,
  slippage_pct: 0.0005,
  risk_free_rate: 0.0,
  include_buy_and_hold: true,
  benchmark: "SPY",
  realistic_costs: false,
  earnings_blackout: false,
  regime_gating: false,
};

//: $0.005/share (IBKR-style) and 10% of a ticker's own mean ATR/Close ratio
//: - applied when "Realistic Fees & Slippage" is toggled on.
const REALISTIC_FEE_PER_SHARE = 0.005;
const REALISTIC_ATR_SLIPPAGE_MULTIPLE = 0.1;

function NumField({
  label,
  value,
  step,
  onChange,
  testId,
}: {
  label: string;
  value: number;
  step?: number;
  onChange: (v: number) => void;
  testId?: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-text-dim">
      {label}
      <input
        type="number"
        value={value}
        step={step ?? 1}
        onChange={(e) => onChange(Number(e.target.value))}
        data-testid={testId}
        className="w-full rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
      />
    </label>
  );
}

export function BacktestStudio() {
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const body: BacktestRequest = {
      strategy: form.strategy,
      tickers: form.tickers
        .split(",")
        .map((t) => t.trim().toUpperCase())
        .filter(Boolean),
      start: form.start || null,
      end: form.end || null,
      initial_capital: form.initial_capital,
      risk_per_trade_pct: form.risk_per_trade_pct,
      commission: form.commission,
      slippage_pct: form.slippage_pct,
      risk_free_rate: form.risk_free_rate,
      include_buy_and_hold: form.include_buy_and_hold,
      benchmark: form.benchmark,
      fee_per_share: form.realistic_costs ? REALISTIC_FEE_PER_SHARE : 0,
      atr_slippage_multiple: form.realistic_costs
        ? REALISTIC_ATR_SLIPPAGE_MULTIPLE
        : 0,
      earnings_blackout: form.earnings_blackout,
      regime_gating: form.regime_gating,
    };
    try {
      const res = await runBacktest(body);
      setResult(res);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : String(err),
      );
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-3 rounded border border-border bg-panel p-4"
      >
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <label className="flex flex-col gap-1 text-xs text-text-dim">
            Strategy
            <select
              value={form.strategy}
              onChange={(e) => update("strategy", e.target.value as Strategy)}
              data-testid="backtest-strategy-select"
              className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
            >
              {STRATEGIES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label className="col-span-2 flex flex-col gap-1 text-xs text-text-dim sm:col-span-1">
            Tickers (comma sep.)
            <input
              type="text"
              value={form.tickers}
              onChange={(e) => update("tickers", e.target.value)}
              placeholder="AAPL, MSFT"
              data-testid="backtest-ticker-input"
              className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-dim">
            Start
            <input
              type="date"
              value={form.start}
              onChange={(e) => update("start", e.target.value)}
              data-testid="backtest-start-date"
              className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-dim">
            End
            <input
              type="date"
              value={form.end}
              onChange={(e) => update("end", e.target.value)}
              data-testid="backtest-end-date"
              className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-dim">
            Benchmark
            <input
              type="text"
              value={form.benchmark}
              onChange={(e) =>
                update("benchmark", e.target.value.toUpperCase())
              }
              className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="flex items-center gap-2 pt-4 text-xs text-text-dim">
            <input
              type="checkbox"
              checked={form.include_buy_and_hold}
              onChange={(e) => update("include_buy_and_hold", e.target.checked)}
              className="accent-accent"
            />
            Buy &amp; hold
          </label>
        </div>

        <div className="flex flex-wrap gap-4 border-t border-border pt-3">
          <label className="flex items-center gap-2 text-xs text-text-dim">
            <input
              type="checkbox"
              checked={form.realistic_costs}
              onChange={(e) => update("realistic_costs", e.target.checked)}
              data-testid="backtest-toggle-realistic-costs"
              className="accent-accent"
            />
            Realistic Fees &amp; Slippage ($0.005/share + ATR spread)
          </label>
          <label className="flex items-center gap-2 text-xs text-text-dim">
            <input
              type="checkbox"
              checked={form.earnings_blackout}
              onChange={(e) => update("earnings_blackout", e.target.checked)}
              data-testid="backtest-toggle-earnings-blackout"
              className="accent-accent"
            />
            Earnings Blackout (5-day)
          </label>
          <label className="flex items-center gap-2 text-xs text-text-dim">
            <input
              type="checkbox"
              checked={form.regime_gating}
              onChange={(e) => update("regime_gating", e.target.checked)}
              data-testid="backtest-toggle-regime-gating"
              className="accent-accent"
            />
            Regime Gating (suppress longs in bear/high-vol regimes)
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <NumField
            label="Initial Capital ($)"
            value={form.initial_capital}
            step={100}
            onChange={(v) => update("initial_capital", v)}
            testId="backtest-initial-capital"
          />
          <NumField
            label="Risk / Trade"
            value={form.risk_per_trade_pct}
            step={0.005}
            onChange={(v) => update("risk_per_trade_pct", v)}
          />
          <NumField
            label="Commission"
            value={form.commission}
            step={0.0005}
            onChange={(v) => update("commission", v)}
          />
          <NumField
            label="Slippage %"
            value={form.slippage_pct}
            step={0.0001}
            onChange={(v) => update("slippage_pct", v)}
          />
          <NumField
            label="Risk-free Rate"
            value={form.risk_free_rate}
            step={0.005}
            onChange={(v) => update("risk_free_rate", v)}
          />
        </div>

        <div>
          <button
            type="submit"
            disabled={loading || form.tickers.trim() === ""}
            data-testid="backtest-run-button"
            className="rounded border border-accent bg-accent/10 px-4 py-2 text-sm font-semibold text-accent hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Running…" : "Run Backtest"}
          </button>
        </div>
      </form>

      {error && (
        <div className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short">
          {error}
        </div>
      )}

      {result && (
        <div className="flex flex-col gap-5">
          {result.warnings.length > 0 && (
            <WarningsBanner warnings={result.warnings} />
          )}

          <div className="rounded border border-border bg-panel-alt px-4 py-3 text-base font-medium text-text">
            {result.headline}
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard
              label="Total Return"
              value={fmtPct(result.summaries.strategy.total_return_pct)}
              valueClassName={signClass(
                result.summaries.strategy.total_return_pct,
              )}
            />
            <StatCard
              label="CAGR"
              value={fmtPct(result.summaries.strategy.cagr_pct)}
              valueClassName={signClass(result.summaries.strategy.cagr_pct)}
            />
            <StatCard
              label="Sharpe"
              value={fmtNum(result.summaries.strategy.sharpe_ratio)}
            />
            <StatCard
              label="Max Drawdown"
              value={fmtPct(result.summaries.strategy.max_drawdown_pct)}
              valueClassName="text-short"
            />
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {(["strategy", "buy_and_hold", "spy"] as const).map((key) => {
              const s = result.summaries[key];
              if (!s) return null;
              return (
                <div
                  key={key}
                  className="rounded border border-border bg-panel p-3"
                >
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-dim">
                    {s.label}
                  </div>
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                    <dt className="text-text-faint">Final Value</dt>
                    <dd className="text-right">{fmtCurrency(s.final_value)}</dd>
                    <dt className="text-text-faint">Return</dt>
                    <dd
                      className={`text-right ${signClass(s.total_return_pct)}`}
                    >
                      {fmtPct(s.total_return_pct)}
                    </dd>
                    <dt className="text-text-faint">CAGR</dt>
                    <dd className={`text-right ${signClass(s.cagr_pct)}`}>
                      {fmtPct(s.cagr_pct)}
                    </dd>
                    <dt className="text-text-faint">Sharpe</dt>
                    <dd className="text-right">{fmtNum(s.sharpe_ratio)}</dd>
                    <dt className="text-text-faint">Max DD</dt>
                    <dd className="text-right text-short">
                      {fmtPct(s.max_drawdown_pct)}
                    </dd>
                    <dt className="text-text-faint">Volatility</dt>
                    <dd className="text-right">{fmtPct(s.volatility_pct)}</dd>
                  </dl>
                </div>
              );
            })}
          </div>

          <EquityChart data={result.equity_curves} />

          {result.vs_spy && (
            <div className="rounded border border-border bg-panel p-3">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-dim">
                vs {result.vs_spy.benchmark_label}
              </div>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
                <dt className="text-text-faint">Alpha (ann.)</dt>
                <dd
                  className={`text-right ${signClass(result.vs_spy.alpha_annual_pct)}`}
                >
                  {fmtPct(result.vs_spy.alpha_annual_pct)}
                </dd>
                <dt className="text-text-faint">Beta</dt>
                <dd className="text-right">{fmtNum(result.vs_spy.beta)}</dd>
                <dt className="text-text-faint">Information Ratio</dt>
                <dd className="text-right">
                  {fmtNum(result.vs_spy.information_ratio)}
                </dd>
                <dt className="text-text-faint">Excess Return</dt>
                <dd
                  className={`text-right ${signClass(result.vs_spy.excess_return_pct)}`}
                >
                  {fmtPct(result.vs_spy.excess_return_pct)}
                </dd>
                <dt className="text-text-faint">Correlation</dt>
                <dd className="text-right">
                  {fmtNum(result.vs_spy.correlation)}
                </dd>
                <dt className="text-text-faint">Tracking Error</dt>
                <dd className="text-right">
                  {fmtPct(result.vs_spy.tracking_error_pct)}
                </dd>
                <dt className="text-text-faint">R²</dt>
                <dd className="text-right">
                  {fmtNum(result.vs_spy.r_squared)}
                </dd>
                <dt className="text-text-faint">Benchmark Sharpe</dt>
                <dd className="text-right">
                  {fmtNum(result.vs_spy.benchmark_sharpe_ratio)}
                </dd>
              </dl>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <StatCard
              label="Total Trades"
              value={fmtNum(result.trade_metrics.total_trades, 0)}
            />
            <StatCard
              label="Win Rate"
              value={
                result.trade_metrics.win_rate === null
                  ? "—"
                  : fmtPct(result.trade_metrics.win_rate * 100, 1)
              }
            />
            <StatCard
              label="Profit Factor"
              value={fmtNum(result.trade_metrics.profit_factor)}
            />
            <StatCard
              label="Expectancy (E)"
              value={fmtCurrency(result.trade_metrics.expectancy, 2)}
              valueClassName={signClass(result.trade_metrics.expectancy)}
            />
            <StatCard
              label="Max R-Multiple"
              value={
                result.trade_metrics.max_r_multiple === null
                  ? "—"
                  : `${fmtNum(result.trade_metrics.max_r_multiple, 1)}R`
              }
            />
          </div>

          <TradesTable trades={result.trades} />
        </div>
      )}
    </div>
  );
}
