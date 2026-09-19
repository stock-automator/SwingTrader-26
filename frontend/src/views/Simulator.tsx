import { useEffect, useState, type FormEvent } from "react";
import { motion } from "framer-motion";
import { toast } from "sonner";
import {
  ApiError,
  getBars,
  postHistoricalDateScan,
  postJournalSimulate,
  postSimulateTradeExecution,
} from "../lib/api";
import { fmtCurrency, fmtNum, fmtPct } from "../lib/format";
import { CandlestickChart, type CandleBar } from "../components/CandlestickChart";
import { STRATEGIES } from "../types";
import type {
  ExecutionMode,
  ExitTrigger,
  HistoricalDateScanResponse,
  HistoricalSetup,
  LevelType,
  SimulateTradeExecutionResponse,
  Strategy,
} from "../types";

// ---- Fade/slide vocabulary matching App.tsx's AnimatePresence tabs (see
// frontend/src/App.tsx) - reused here for in-view section transitions. ----
const FADE_SLIDE = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -6 },
  transition: { duration: 0.18 },
};

const LEVEL_TYPES: LevelType[] = ["FIXED", "PERCENTAGE", "ATR"];
const EXECUTION_MODES: ExecutionMode[] = ["NEXT_OPEN", "SAME_CLOSE_SLIPPAGE"];

function directionOf(setup: HistoricalSetup): 1 | -1 {
  return setup.direction === "SHORT" ? -1 : 1;
}

interface ScanFormState {
  strategy: Strategy;
  targetDate: string;
  targetTime: string;
  tickers: string;
  account_equity: number;
  risk_per_trade_pct: number;
  earnings_blackout: boolean;
}

const DEFAULT_SCAN_FORM: ScanFormState = {
  strategy: "donchian_breakout",
  targetDate: "",
  targetTime: "16:00",
  tickers: "",
  account_equity: 1000,
  risk_per_trade_pct: 0.02,
  earnings_blackout: false,
};

interface SimFormState {
  execution_mode: ExecutionMode;
  entry_date: string;
  sl_type: LevelType;
  sl_value: number;
  tp_type: LevelType;
  tp_value: number;
  account_equity: number;
  risk_per_trade_pct: number;
  slippage_pct: number;
  commission: number;
  fee_per_share: number;
  atr_slippage_multiple: number;
  impact_coefficient: number;
  max_holding_period_days: number;
  use_regime_filter: boolean;
}

// Badge styling per `exit_trigger` - matches the short/long/amber color
// vocabulary already used for stop/target cells in CandidatesTable above.
const EXIT_TRIGGER_STYLES: Record<ExitTrigger, string> = {
  STOP: "border-short-dim bg-short-dim/20 text-short",
  TARGET: "border-long-dim bg-long-dim/20 text-long",
  REGIME: "border-amber-dim bg-amber-dim/20 text-amber",
  TIMEOUT: "border-border-soft bg-panel-alt text-text-dim",
};

function buildSimForm(setup: HistoricalSetup, scanForm: ScanFormState): SimFormState {
  const entry = setup.entry_price ?? setup.close;
  const slValue =
    setup.stop_loss != null ? Math.abs(entry - setup.stop_loss) : entry * 0.05;
  const tpValue =
    setup.take_profit != null ? Math.abs(setup.take_profit - entry) : entry * 0.1;
  return {
    execution_mode: "NEXT_OPEN",
    entry_date: setup.as_of.slice(0, 10),
    sl_type: "FIXED",
    sl_value: Number(slValue.toFixed(4)),
    tp_type: "FIXED",
    tp_value: Number(tpValue.toFixed(4)),
    account_equity: scanForm.account_equity,
    risk_per_trade_pct: scanForm.risk_per_trade_pct,
    slippage_pct: 0.0005,
    commission: 0.001,
    fee_per_share: 0,
    atr_slippage_multiple: 0,
    impact_coefficient: 0,
    max_holding_period_days: 60,
    use_regime_filter: true,
  };
}

function errMessage(err: unknown): string {
  return err instanceof ApiError
    ? err.message
    : err instanceof Error
      ? err.message
      : String(err);
}

function NumField({
  label,
  value,
  step,
  onChange,
}: {
  label: string;
  value: number;
  step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-text-dim">
      {label}
      <input
        type="number"
        value={value}
        step={step ?? 1}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
      />
    </label>
  );
}

function CandidatesTable({
  result,
  onSelect,
}: {
  result: HistoricalDateScanResponse;
  onSelect: (setup: HistoricalSetup) => void;
}) {
  const tradable = result.setups.filter((s) => s.tradable);
  const other = result.setups.filter((s) => !s.tradable);

  return (
    <motion.div {...FADE_SLIDE} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3 text-xs text-text-dim">
        <span>
          Scanned <strong className="text-text">{result.scanned}</strong>
        </span>
        <span>
          Skipped <strong className="text-text">{result.skipped}</strong>
        </span>
        <span>
          As of <strong className="text-text">{result.target_date}</strong> -
          zero lookahead (backend slices every frame at this date before
          scanning).
        </span>
      </div>

      {result.setups.length === 0 ? (
        <div
          data-testid="sim-candidates-empty"
          className="rounded border border-border bg-panel px-3 py-8 text-center text-sm text-text-faint"
        >
          No candidate setups as of this date.
        </div>
      ) : (
        <div
          data-testid="sim-candidates-table"
          className="max-h-[420px] overflow-auto rounded border border-border bg-panel"
        >
          <table className="w-full min-w-[820px] border-collapse text-sm">
            <thead className="sticky top-0 bg-panel">
              <tr className="border-b border-border text-left text-[11px] uppercase tracking-wider text-text-faint">
                <th className="px-3 py-2">Ticker</th>
                <th className="px-3 py-2">Direction</th>
                <th className="px-3 py-2">Regime</th>
                <th className="px-3 py-2 text-right">Close</th>
                <th className="px-3 py-2 text-right">Entry</th>
                <th className="px-3 py-2 text-right">Stop</th>
                <th className="px-3 py-2 text-right">Target</th>
                <th className="px-3 py-2 text-right">R/R</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {[...tradable, ...other].map((s) => (
                <tr
                  key={s.ticker}
                  data-testid="sim-candidate-row"
                  className={`border-b border-border-soft last:border-0 hover:bg-panel-alt ${
                    !s.tradable ? "opacity-50" : ""
                  }`}
                >
                  <td className="px-3 py-2 font-semibold">{s.ticker}</td>
                  <td className="px-3 py-2 text-text-dim">{s.direction}</td>
                  <td className="px-3 py-2 text-text-dim">{s.regime}</td>
                  <td className="px-3 py-2 text-right">{fmtNum(s.close)}</td>
                  <td className="px-3 py-2 text-right">
                    {fmtNum(s.entry_price)}
                  </td>
                  <td className="px-3 py-2 text-right text-short">
                    {fmtNum(s.stop_loss)}
                  </td>
                  <td className="px-3 py-2 text-right text-long">
                    {fmtNum(s.take_profit)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {s.reward_risk_ratio === null
                      ? "—"
                      : `${fmtNum(s.reward_risk_ratio, 1)}R`}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => onSelect(s)}
                      disabled={!s.tradable}
                      data-testid="sim-select-button"
                      className="rounded border border-accent bg-accent/10 px-2 py-1 text-xs font-semibold text-accent hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      Simulate Trade
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </motion.div>
  );
}

function SimulateTradeDrawer({
  setup,
  scanForm,
  onClose,
}: {
  setup: HistoricalSetup | null;
  scanForm: ScanFormState;
  onClose: () => void;
}) {
  const [form, setForm] = useState<SimFormState | null>(null);
  const [result, setResult] = useState<SimulateTradeExecutionResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState("");
  const [journalLoading, setJournalLoading] = useState(false);
  const [journalError, setJournalError] = useState<string | null>(null);

  const isOpen = setup !== null;
  const active = setup !== null && (form ?? buildSimForm(setup, scanForm));

  function update<K extends keyof SimFormState>(key: K, value: SimFormState[K]) {
    setForm((f) => ({ ...(f ?? (setup ? buildSimForm(setup, scanForm) : ({} as SimFormState))), [key]: value }));
  }

  function reset() {
    setForm(null);
    setResult(null);
    setError(null);
    setNote("");
    setJournalError(null);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!setup || !active) return;
    setLoading(true);
    setError(null);
    setJournalError(null);
    try {
      const res = await postSimulateTradeExecution({
        ticker: setup.ticker,
        entry_date: active.entry_date,
        sl_type: active.sl_type,
        sl_value: active.sl_value,
        tp_type: active.tp_type,
        tp_value: active.tp_value,
        direction: directionOf(setup),
        account_equity: active.account_equity,
        risk_per_trade_pct: active.risk_per_trade_pct,
        execution_mode: active.execution_mode,
        slippage_pct: active.slippage_pct,
        commission: active.commission,
        fee_per_share: active.fee_per_share,
        atr_slippage_multiple: active.atr_slippage_multiple,
        impact_coefficient: active.impact_coefficient,
        max_holding_period_days: active.max_holding_period_days,
        use_regime_filter: active.use_regime_filter,
        resolve_exit: true,
      });
      setResult(res);
    } catch (err) {
      setError(errMessage(err));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const canLogJournal =
    setup !== null &&
    result !== null &&
    result.exit_date != null &&
    result.exit_price != null &&
    result.exit_trigger != null &&
    note.trim().length > 0;

  async function handleLogJournal() {
    if (!setup || !result || !canLogJournal) return;
    setJournalLoading(true);
    setJournalError(null);
    try {
      await postJournalSimulate({
        ticker: setup.ticker,
        entry_date: result.fill_date,
        entry_price: result.fill_price,
        stop_loss: result.stop_loss,
        take_profit: result.take_profit,
        exit_date: result.exit_date as string,
        exit_price: result.exit_price as number,
        exit_trigger: result.exit_trigger as ExitTrigger,
        post_mortem_note: note,
      });
      toast.success(`Logged ${setup.ticker} to journal (MANUAL_SIMULATION)`);
      setNote("");
    } catch (err) {
      const message = errMessage(err);
      setJournalError(message);
      toast.error(message);
    } finally {
      setJournalLoading(false);
    }
  }

  return (
    <>
      <div
        onClick={() => {
          onClose();
          reset();
        }}
        className={`fixed inset-0 z-20 bg-black/50 transition-opacity ${
          isOpen ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />
      {/* Slide-over pattern matches ../components/OrderTicketDrawer.tsx
          exactly (CSS transform transition, not a framer-motion spring -
          that drawer doesn't actually use framer-motion despite being the
          codebase's reference "drawer" component). */}
      <aside
        className={`fixed right-0 top-0 z-30 flex h-full w-full max-w-md flex-col border-l border-border bg-bg shadow-2xl transition-transform duration-200 ${
          isOpen ? "translate-x-0" : "translate-x-full"
        }`}
        data-testid="simulate-trade-drawer"
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="text-sm font-bold tracking-wide text-text">
            {setup ? `Simulate ${setup.ticker}` : "Simulate Trade"}
          </div>
          <button
            onClick={() => {
              onClose();
              reset();
            }}
            className="rounded px-2 py-1 text-sm text-text-dim hover:text-text"
          >
            ✕
          </button>
        </div>

        {setup && active && (
          <form
            onSubmit={handleSubmit}
            className="flex flex-1 flex-col gap-3 overflow-y-auto p-4"
          >
            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1 text-xs text-text-dim">
                Execution Mode
                <select
                  value={active.execution_mode}
                  onChange={(e) =>
                    update("execution_mode", e.target.value as ExecutionMode)
                  }
                  data-testid="sim-execution-mode"
                  className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
                >
                  {EXECUTION_MODES.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs text-text-dim">
                Signal Bar Date
                <input
                  type="date"
                  value={active.entry_date}
                  onChange={(e) => update("entry_date", e.target.value)}
                  data-testid="sim-entry-date"
                  className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
                />
              </label>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1 text-xs text-text-dim">
                Stop Type
                <select
                  value={active.sl_type}
                  onChange={(e) => update("sl_type", e.target.value as LevelType)}
                  className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
                >
                  {LEVEL_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <NumField
                label="Stop Value"
                value={active.sl_value}
                step={0.01}
                onChange={(v) => update("sl_value", v)}
              />
              <label className="flex flex-col gap-1 text-xs text-text-dim">
                Target Type
                <select
                  value={active.tp_type}
                  onChange={(e) => update("tp_type", e.target.value as LevelType)}
                  className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
                >
                  {LEVEL_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <NumField
                label="Target Value"
                value={active.tp_value}
                step={0.01}
                onChange={(v) => update("tp_value", v)}
              />
            </div>

            <div className="grid grid-cols-2 gap-3 border-t border-border pt-3">
              <NumField
                label="Account Equity ($)"
                value={active.account_equity}
                step={100}
                onChange={(v) => update("account_equity", v)}
              />
              <NumField
                label="Risk / Trade"
                value={active.risk_per_trade_pct}
                step={0.005}
                onChange={(v) => update("risk_per_trade_pct", v)}
              />
              <NumField
                label="Slippage %"
                value={active.slippage_pct}
                step={0.0001}
                onChange={(v) => update("slippage_pct", v)}
              />
              <NumField
                label="Commission"
                value={active.commission}
                step={0.0005}
                onChange={(v) => update("commission", v)}
              />
              <NumField
                label="Fee / Share ($)"
                value={active.fee_per_share}
                step={0.001}
                onChange={(v) => update("fee_per_share", v)}
              />
              <NumField
                label="ATR Slippage Multiple"
                value={active.atr_slippage_multiple}
                step={0.05}
                onChange={(v) => update("atr_slippage_multiple", v)}
              />
            </div>

            <div className="grid grid-cols-2 gap-3 border-t border-border pt-3">
              <NumField
                label="Max Holding Period (days)"
                value={active.max_holding_period_days}
                step={1}
                onChange={(v) => update("max_holding_period_days", v)}
              />
              <label className="flex items-center gap-2 pt-4 text-xs text-text-dim">
                <input
                  type="checkbox"
                  checked={active.use_regime_filter}
                  onChange={(e) => update("use_regime_filter", e.target.checked)}
                  data-testid="sim-use-regime-filter"
                  className="accent-accent"
                />
                Regime Exit Filter
              </label>
            </div>

            <button
              type="submit"
              disabled={loading}
              data-testid="sim-run-button"
              className="rounded border border-accent bg-accent/10 px-4 py-2 text-sm font-semibold text-accent hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? "Simulating…" : "Simulate Fill"}
            </button>

            {error && (
              <div className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short">
                {error}
              </div>
            )}

            {result && (
              <motion.div {...FADE_SLIDE} className="flex flex-col gap-3">
                <div className="rounded border border-border bg-panel p-3">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-dim">
                    Entry Fill Simulation - {result.fill_date}
                  </div>
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                    <dt className="text-text-faint">Reference Price</dt>
                    <dd className="text-right">
                      {fmtNum(result.reference_price)}
                    </dd>
                    <dt className="text-text-faint">Fill Price</dt>
                    <dd className="text-right font-semibold">
                      {fmtNum(result.fill_price)}
                    </dd>
                    <dt className="text-text-faint">Spread</dt>
                    <dd className="text-right">
                      {fmtPct(result.spread_pct * 100, 3)}
                    </dd>
                    <dt className="text-text-faint">Market Impact</dt>
                    <dd className="text-right">
                      {fmtPct(result.market_impact_pct * 100, 3)}
                    </dd>
                    <dt className="text-text-faint">Total Slippage Applied</dt>
                    <dd className="text-right">
                      {fmtPct(result.slippage_pct_applied * 100, 3)}
                    </dd>
                    <dt className="text-text-faint">Slippage Cost</dt>
                    <dd className="text-right">
                      {fmtCurrency(result.slippage_cost, 2)}
                    </dd>
                    <dt className="text-text-faint">Commission Cost</dt>
                    <dd className="text-right">
                      {fmtCurrency(result.commission_cost, 2)}
                    </dd>
                    <dt className="text-text-faint">Total Cost</dt>
                    <dd className="text-right">
                      {fmtCurrency(result.total_cost, 2)}
                    </dd>
                    <dt className="text-text-faint">Shares</dt>
                    <dd className="text-right">{result.shares}</dd>
                    <dt className="text-text-faint">Stop Loss</dt>
                    <dd className="text-right text-short">
                      {fmtNum(result.stop_loss)}
                    </dd>
                    <dt className="text-text-faint">Take Profit</dt>
                    <dd className="text-right text-long">
                      {fmtNum(result.take_profit)}
                    </dd>
                    <dt className="text-text-faint">Risk Amount</dt>
                    <dd className="text-right">
                      {fmtCurrency(result.risk_amount, 2)}
                    </dd>
                    <dt className="text-text-faint">R-Multiple</dt>
                    <dd className="text-right">
                      {fmtNum(result.reward_risk_ratio, 1)}R
                    </dd>
                    <dt className="text-text-faint">Notional Value</dt>
                    <dd className="text-right">
                      {fmtCurrency(result.notional_value, 2)}
                    </dd>
                  </dl>
                  {!result.tradable && (
                    <div className="mt-2 rounded border border-amber-dim bg-amber-dim/20 px-2 py-1 text-xs text-amber">
                      {result.note ?? "Not tradable at this account size."}
                    </div>
                  )}
                </div>

                {/* Populated when `resolve_exit` (default true) walked the
                    fill forward to a stop/target/regime/timeout exit - see
                    backend/app/api/schemas.py SimulateTradeExecutionResponse.
                    Fields stay null if there were no bars after the fill. */}
                <div
                  data-testid="sim-realized-outcome"
                  className="rounded border border-border bg-panel p-3"
                >
                  <div className="mb-2 flex items-center justify-between">
                    <div className="text-xs font-semibold uppercase tracking-wider text-text-dim">
                      Realized Outcome
                    </div>
                    {result.exit_trigger && (
                      <span
                        data-testid="sim-exit-trigger-badge"
                        className={`rounded border px-2 py-0.5 text-[11px] font-semibold ${EXIT_TRIGGER_STYLES[result.exit_trigger]}`}
                      >
                        {result.exit_trigger}
                      </span>
                    )}
                  </div>
                  {result.exit_date == null ? (
                    <div className="text-xs text-text-faint">
                      Trade did not resolve to an exit - no post-fill bars
                      were available to walk forward on.
                    </div>
                  ) : (
                    <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                      <dt className="text-text-faint">Exit Date</dt>
                      <dd className="text-right">{result.exit_date}</dd>
                      <dt className="text-text-faint">Exit Price</dt>
                      <dd className="text-right">{fmtNum(result.exit_price)}</dd>
                      <dt className="text-text-faint">Holding Period</dt>
                      <dd className="text-right">
                        {result.holding_period_days == null
                          ? "—"
                          : `${result.holding_period_days}d`}
                      </dd>
                      <dt className="text-text-faint">Realized P&amp;L ($)</dt>
                      <dd
                        className={`text-right font-semibold ${
                          (result.realized_pnl_dollars ?? 0) >= 0
                            ? "text-long"
                            : "text-short"
                        }`}
                      >
                        {fmtCurrency(result.realized_pnl_dollars, 2)}
                      </dd>
                      <dt className="text-text-faint">Realized P&amp;L (%)</dt>
                      <dd
                        className={`text-right font-semibold ${
                          (result.realized_pnl_pct ?? 0) >= 0
                            ? "text-long"
                            : "text-short"
                        }`}
                      >
                        {fmtPct(
                          result.realized_pnl_pct == null
                            ? null
                            : result.realized_pnl_pct * 100,
                          2,
                        )}
                      </dd>
                      <dt className="text-text-faint">MAE</dt>
                      <dd className="text-right text-short">
                        {fmtPct(
                          result.mae_pct == null ? null : result.mae_pct * 100,
                          2,
                        )}
                      </dd>
                      <dt className="text-text-faint">MFE</dt>
                      <dd className="text-right text-long">
                        {fmtPct(
                          result.mfe_pct == null ? null : result.mfe_pct * 100,
                          2,
                        )}
                      </dd>
                    </dl>
                  )}
                </div>

                <div className="flex flex-col gap-2 border-t border-border pt-3">
                  <label className="flex flex-col gap-1 text-xs text-text-dim">
                    Post-Mortem Note (required to log)
                    <textarea
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      rows={3}
                      placeholder="What did you see in this setup? Would you have taken it live?"
                      data-testid="sim-postmortem-note"
                      className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
                    />
                  </label>
                  {journalError && (
                    <div className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short">
                      {journalError}
                    </div>
                  )}
                  <button
                    type="button"
                    disabled={!canLogJournal || journalLoading}
                    onClick={handleLogJournal}
                    title={
                      result && result.exit_date == null
                        ? "This fill did not resolve to an exit - no post-fill bars were available."
                        : undefined
                    }
                    data-testid="sim-log-journal-button"
                    className="rounded border border-accent bg-accent/10 px-4 py-2 text-sm font-semibold text-accent hover:bg-accent/20 disabled:cursor-not-allowed disabled:border-border disabled:bg-panel-alt disabled:text-text-faint disabled:opacity-60"
                  >
                    {journalLoading
                      ? "Logging…"
                      : "Log to Journal (MANUAL_SIMULATION)"}
                  </button>
                </div>
              </motion.div>
            )}
          </form>
        )}
      </aside>
    </>
  );
}

export default function Simulator() {
  const [scanForm, setScanForm] = useState<ScanFormState>(DEFAULT_SCAN_FORM);
  const [scanResult, setScanResult] = useState<HistoricalDateScanResponse | null>(
    null,
  );
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanLoading, setScanLoading] = useState(false);

  const [selectedSetup, setSelectedSetup] = useState<HistoricalSetup | null>(
    null,
  );

  const [blindTape, setBlindTape] = useState(false);
  const [revealIndex, setRevealIndex] = useState(0);

  const [bars, setBars] = useState<CandleBar[]>([]);
  const [barsLoading, setBarsLoading] = useState(false);
  const [barsError, setBarsError] = useState<string | null>(null);

  function updateScanForm<K extends keyof ScanFormState>(
    key: K,
    value: ScanFormState[K],
  ) {
    setScanForm((f) => ({ ...f, [key]: value }));
  }

  async function handleScanSubmit(e: FormEvent) {
    e.preventDefault();
    if (!scanForm.targetDate) return;
    setScanLoading(true);
    setScanError(null);
    try {
      const res = await postHistoricalDateScan({
        strategy: scanForm.strategy,
        target_date: scanForm.targetDate,
        tickers: scanForm.tickers.trim()
          ? scanForm.tickers
              .split(",")
              .map((t) => t.trim().toUpperCase())
              .filter(Boolean)
          : undefined,
        account_equity: scanForm.account_equity,
        risk_per_trade_pct: scanForm.risk_per_trade_pct,
        earnings_blackout: scanForm.earnings_blackout,
      });
      setScanResult(res);
      setRevealIndex(0);
    } catch (err) {
      setScanError(errMessage(err));
      setScanResult(null);
    } finally {
      setScanLoading(false);
    }
  }

  // Fires once a candidate setup is selected (`sim-select-button`) - it
  // carries both the ticker and the as-of date needed for a zero-lookahead
  // `GET /api/v1/backtest/bars` fetch (bars on or before `as_of` only).
  // Re-fires if the scan's target date changes for the same selection.
  useEffect(() => {
    if (!selectedSetup) return;
    let cancelled = false;
    const asOf = scanForm.targetDate || selectedSetup.as_of.slice(0, 10);
    setBarsLoading(true);
    setBarsError(null);
    getBars({ ticker: selectedSetup.ticker, as_of: asOf, lookback_days: 250 })
      .then((res) => {
        if (cancelled) return;
        setBars(
          res.bars.map((b) => ({
            time: b.date,
            open: b.open,
            high: b.high,
            low: b.low,
            close: b.close,
          })),
        );
        setRevealIndex(0);
      })
      .catch((err) => {
        if (cancelled) return;
        setBarsError(errMessage(err));
        setBars([]);
      })
      .finally(() => {
        if (!cancelled) setBarsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedSetup, scanForm.targetDate]);

  const allBars: CandleBar[] = bars;
  const visibleBars = blindTape ? allBars.slice(0, revealIndex + 1) : allBars;

  return (
    <div className="flex flex-col gap-5">
      <form
        onSubmit={handleScanSubmit}
        className="flex flex-col gap-3 rounded border border-border bg-panel p-4"
      >
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <label className="flex flex-col gap-1 text-xs text-text-dim">
            Strategy
            <select
              value={scanForm.strategy}
              onChange={(e) =>
                updateScanForm("strategy", e.target.value as Strategy)
              }
              data-testid="sim-strategy-select"
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
            Target Date
            <input
              type="date"
              value={scanForm.targetDate}
              max={new Date().toISOString().slice(0, 10)}
              onChange={(e) => updateScanForm("targetDate", e.target.value)}
              required
              data-testid="sim-target-date"
              className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-dim">
            Target Time
            <input
              type="time"
              value={scanForm.targetTime}
              onChange={(e) => updateScanForm("targetTime", e.target.value)}
              title="Daily OHLCV data - time is for reference only and is not sent to the backend, which resolves to the daily bar as of Target Date."
              data-testid="sim-target-time"
              className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="col-span-2 flex flex-col gap-1 text-xs text-text-dim sm:col-span-1">
            Tickers (optional, comma sep.)
            <input
              type="text"
              value={scanForm.tickers}
              onChange={(e) => updateScanForm("tickers", e.target.value)}
              placeholder="Full watchlist if empty"
              data-testid="sim-tickers-input"
              className="rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="flex items-center gap-2 pt-4 text-xs text-text-dim">
            <input
              type="checkbox"
              checked={scanForm.earnings_blackout}
              onChange={(e) =>
                updateScanForm("earnings_blackout", e.target.checked)
              }
              data-testid="sim-earnings-blackout"
              className="accent-accent"
            />
            Earnings Blackout
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <NumField
            label="Account Equity ($)"
            value={scanForm.account_equity}
            step={100}
            onChange={(v) => updateScanForm("account_equity", v)}
          />
          <NumField
            label="Risk / Trade"
            value={scanForm.risk_per_trade_pct}
            step={0.005}
            onChange={(v) => updateScanForm("risk_per_trade_pct", v)}
          />
        </div>

        <div className="flex items-center gap-4 border-t border-border pt-3">
          <button
            type="submit"
            disabled={scanLoading || !scanForm.targetDate}
            data-testid="sim-scan-button"
            className="rounded border border-accent bg-accent/10 px-4 py-2 text-sm font-semibold text-accent hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {scanLoading ? "Scanning…" : "Scan As-Of Date"}
          </button>
          <label className="flex items-center gap-2 text-xs text-text-dim">
            <input
              type="checkbox"
              checked={blindTape}
              onChange={(e) => setBlindTape(e.target.checked)}
              data-testid="sim-blind-tape-toggle"
              className="accent-accent"
            />
            Blind Tape Walkthrough
          </label>
        </div>
      </form>

      {scanError && (
        <div className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short">
          {scanError}
        </div>
      )}

      {scanResult && (
        <CandidatesTable result={scanResult} onSelect={setSelectedSetup} />
      )}

      {blindTape && (
        <motion.div {...FADE_SLIDE} className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <div className="text-xs font-semibold uppercase tracking-wider text-text-dim">
              Blind Tape Walkthrough
              {selectedSetup && (
                <span className="ml-2 text-text-faint">
                  {selectedSetup.ticker} · as of{" "}
                  {scanForm.targetDate || selectedSetup.as_of.slice(0, 10)}
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={() =>
                setRevealIndex((i) => Math.min(i + 1, Math.max(allBars.length - 1, 0)))
              }
              disabled={allBars.length === 0 || revealIndex >= allBars.length - 1}
              data-testid="sim-step-forward"
              className="rounded border border-border bg-panel-alt px-3 py-1 text-xs text-text hover:border-accent disabled:cursor-not-allowed disabled:opacity-40"
            >
              Step Forward →
            </button>
          </div>
          {barsError && (
            <div
              data-testid="sim-blind-tape-error"
              className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short"
            >
              {barsError}
            </div>
          )}
          <CandlestickChart
            bars={visibleBars}
            emptyLabel={
              barsLoading
                ? "Loading bars…"
                : selectedSetup
                  ? undefined
                  : "Select a candidate below (\"Simulate Trade\") to load its price history."
            }
          />
        </motion.div>
      )}

      <SimulateTradeDrawer
        setup={selectedSetup}
        scanForm={scanForm}
        onClose={() => setSelectedSetup(null)}
      />
    </div>
  );
}
