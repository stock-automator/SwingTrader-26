// Types mirroring the SwingTrader backend API contract (FastAPI, /api/v1/*).

export type Strategy = "donchian_breakout" | "moving_average_cross";

export const STRATEGIES: { value: Strategy; label: string }[] = [
  { value: "donchian_breakout", label: "Donchian Breakout" },
  { value: "moving_average_cross", label: "Moving Average Cross" },
];

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
}

export interface ScreenerResponse {
  setups: ScreenerSetup[];
  scanned: number;
  skipped: number;
  skip_reasons: Record<string, number>;
  warnings: string[];
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
