// Types mirroring the SwingTrader backend API contract (FastAPI, /api/v1/*).

export type Strategy =
  | "donchian_breakout"
  | "moving_average_cross"
  | "vcp_breakout"
  | "relative_strength"
  | "bollinger_keltner_squeeze"
  | "kama_trend"
  | "zscore_mean_reversion"
  | "supertrend_psar"
  | "obv_divergence"
  | "dual_momentum";

export const STRATEGIES: { value: Strategy; label: string }[] = [
  { value: "donchian_breakout", label: "Donchian Breakout" },
  { value: "moving_average_cross", label: "Moving Average Cross" },
  { value: "vcp_breakout", label: "VCP Breakout" },
  { value: "relative_strength", label: "Relative Strength Pullback" },
  { value: "bollinger_keltner_squeeze", label: "Bollinger-Keltner Squeeze" },
  { value: "kama_trend", label: "KAMA Dynamic Trend" },
  { value: "zscore_mean_reversion", label: "Z-Score Mean Reversion (Hurst)" },
  { value: "supertrend_psar", label: "Supertrend + Parabolic SAR" },
  { value: "obv_divergence", label: "OBV Bullish Divergence" },
  { value: "dual_momentum", label: "Dual Momentum Engine" },
];

// ---- Macro regime / circuit breaker ----

export type MacroRegime =
  | "BULL_TRENDING"
  | "BEAR_TRENDING"
  | "HIGH_VOLATILITY_CHOP"
  | "NEUTRAL"
  | "UNKNOWN";

export interface HealthResponse {
  status: string;
  finnhub_configured: boolean;
  allow_downloads: boolean;
}

// ---- Screener ----

export type Direction = "LONG" | "SHORT" | "EXIT_LONG" | "FLAT";

export type Regime = "BULL_TREND" | "BEAR_TREND" | "CHOPPY" | "UNKNOWN";

export interface ScreenerSetup {
  ticker: string;
  direction: Direction;
  tradable: boolean;
  as_of: string;
  close: number;
  regime: Regime;
  adx: number | null;
  atr: number | null;
  entry_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  shares: number | null;
  risk_amount: number | null;
  relative_strength: number | null;
  rank: number | null;
  note: string | null;
  reward_risk_ratio: number | null;
  notional_value: number | null;
}

export interface ScreenerResponse {
  setups: ScreenerSetup[];
  scanned: number;
  skipped: number;
  skip_reasons: Record<string, number>;
  warnings: string[];
  macro_regime: MacroRegime;
  circuit_breaker_active: boolean;
}

// ---- Backtest ----

export interface BacktestRequest {
  strategy: Strategy;
  strategy_params?: Record<string, number>;
  tickers: string[];
  start: string | null;
  end: string | null;
  initial_capital: number;
  risk_per_trade_pct: number;
  commission: number;
  slippage_pct: number;
  risk_free_rate: number;
  include_buy_and_hold: boolean;
  benchmark: string;
  fee_per_share: number;
  atr_slippage_multiple: number;
  earnings_blackout: boolean;
  regime_gating: boolean;
}

export interface CurveSummary {
  label: string;
  initial_value: number | null;
  final_value: number | null;
  total_return_pct: number | null;
  cagr_pct: number | null;
  sharpe_ratio: number | null;
  max_drawdown_pct: number | null;
  volatility_pct: number | null;
}

export interface RelativeMetrics {
  benchmark_label: string;
  alpha_annual_pct: number | null;
  beta: number | null;
  sharpe_ratio: number | null;
  benchmark_sharpe_ratio: number | null;
  information_ratio: number | null;
  excess_return_pct: number | null;
  tracking_error_pct: number | null;
  correlation: number | null;
  r_squared: number | null;
}

export interface TradeMetrics {
  total_trades: number;
  win_rate: number | null;
  expectancy: number | null;
  profit_factor: number | null;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  max_drawdown_pct: number | null;
  cagr_pct: number | null;
  max_r_multiple: number | null;
}

export interface EquityCurvePoint {
  date: string;
  strategy: number;
  buy_and_hold?: number;
  spy?: number;
}

export interface Trade {
  ticker: string;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  size: number;
  pnl: number;
  return_pct: number;
}

export interface BacktestResponse {
  strategy: string;
  tickers: string[];
  initial_capital: number;
  start: string;
  end: string;
  headline: string;
  summaries: {
    strategy: CurveSummary;
    buy_and_hold?: CurveSummary;
    spy?: CurveSummary;
  };
  vs_spy: RelativeMetrics | null;
  vs_buy_and_hold: RelativeMetrics | null;
  trade_metrics: TradeMetrics;
  equity_curves: EquityCurvePoint[];
  trades: Trade[];
  warnings: string[];
}

// ---- Order Ticket ----

export interface OrderTicketRequest {
  ticker: string;
  entry_price: number;
  sl_type: "PERCENTAGE" | "FIXED" | "ATR";
  sl_value: number;
  tp_type: "PERCENTAGE" | "FIXED" | "ATR";
  tp_value: number;
  atr?: number | null;
  direction?: 1 | -1;
  order_type?: string;
  account_tiers?: number[];
}

export interface OrderTicket {
  ticker: string;
  account_equity: number;
  order_type: string;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  quantity: number;
  notional_value: number;
  risk_amount: number;
  reward_risk_ratio: number;
  tradable: boolean;
  note: string | null;
}

export interface OrderTicketsResponse {
  tickets: OrderTicket[];
  min_reward_risk_ratio: number;
}

// ---- Data sync ----

export interface CorporateActionAdjustment {
  anchor_date: string;
  old_factor: number;
  new_factor: number;
  ratio: number;
}

export interface SyncResult {
  ticker: string;
  status: string;
  rows_added: number;
  corporate_action: CorporateActionAdjustment | null;
  error: string | null;
}

export interface DataSyncStatusResponse {
  in_progress: boolean;
  started_at: string | null;
  results: SyncResult[];
}

export interface DataSyncResponse {
  status: string;
  tickers: string[];
}

// ---- Signal matrix ----

export interface SignalMatrixRow {
  ticker: string;
  strategy: string;
  direction: Direction;
  tradable: boolean;
  as_of: string;
  close: number;
  entry_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  shares: number | null;
  risk_amount: number | null;
  reward_risk_ratio: number | null;
  notional_value: number | null;
  note: string | null;
  // Always null today - no model-backed estimate exists yet. Never render a
  // synthesized number here; see backend/app/api/signals.py.
  win_probability: number | null;
}

export interface SignalMatrixResponse {
  generated_at: string;
  rows: SignalMatrixRow[];
  scanned: number;
  warnings: string[];
}

// ---- Execution (Alpaca paper trading) ----

export interface ExecutionOrderRequest {
  ticker: string;
  side: "buy" | "sell";
  qty: number;
  order_type?: "MARKET" | "LIMIT" | "BRACKET";
  limit_price?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
}

export interface ExecutionOrderResponse {
  id: string;
  symbol: string;
  qty: string | null;
  side: string | null;
  type: string | null;
  order_class: string | null;
  status: string | null;
  submitted_at: string | null;
}
