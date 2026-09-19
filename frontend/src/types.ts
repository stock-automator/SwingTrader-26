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
  // From analytics/expectancy.py: a regime-matched historical backtest win
  // rate, or a block-bootstrap Monte Carlo percentile below that sample
  // size, or null when there isn't enough trade history for either (always
  // null for SHORT rows - the backtest engine is long-only).
  win_probability: number | null;
  win_probability_method: string | null;
  win_probability_sample_size: number | null;
  win_probability_confidence_low: number | null;
  win_probability_confidence_high: number | null;
  win_probability_note: string | null;
}

export interface SignalMatrixResponse {
  generated_at: string;
  rows: SignalMatrixRow[];
  scanned: number;
  warnings: string[];
}

// ---- Background scan jobs (POST /api/v1/scans) ----

export type ScanJobStatus = "pending" | "running" | "completed" | "failed";

// A setup returned by a background scan job - shaped like SignalMatrixRow
// plus the fields the Unified Live Opportunity Dashboard needs for ranking
// and portfolio-heat capping.
export interface OpportunitySetup {
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
  win_probability: number | null;
  win_probability_method: string | null;
  win_probability_sample_size: number | null;
  win_probability_confidence_low: number | null;
  win_probability_confidence_high: number | null;
  win_probability_note: string | null;
  r_multiple: number;
  setup_quality_score: number;
  portfolio_heat_capped: boolean;
  capped_reason: string | null;
}

export interface ScanJob {
  job_id: string;
  status: ScanJobStatus;
  created_at: string;
  completed_at: string | null;
  error: string | null;
  results: OpportunitySetup[] | null;
}

export interface StartScanResponse {
  job_id: string;
}

export interface ListScansResponse {
  jobs: ScanJob[];
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

export interface AlpacaAccount {
  account_number: string;
  status: string;
  equity: number;
  cash: number;
  buying_power: number;
  portfolio_value: number;
}

export interface Position {
  symbol: string;
  side: string | null;
  qty: number;
  avg_entry_price: number;
  current_price: number | null;
  market_value: number | null;
  cost_basis: number;
  unrealized_pl: number | null;
  unrealized_plpc: number | null;
}

export interface PortfolioHistory {
  timestamp: string[];
  equity: number[];
  profit_loss: number[];
  profit_loss_pct: (number | null)[];
  base_value: number | null;
  timeframe: string;
}

export interface ClosedPosition {
  symbol: string | null;
  status: number | null;
  order_id: string | null;
}

export interface CloseAllPositionsResponse {
  closed: ClosedPosition[];
}

// ---- Alerts ----

export type AlertChannelName = "telegram" | "discord" | "webhook";

export interface AlertChannelsConfig {
  telegram_bot_token: string | null;
  telegram_chat_id: string | null;
  discord_webhook_url: string | null;
  generic_webhook_url: string | null;
}

export interface AlertChannelsConfigResponse extends AlertChannelsConfig {
  telegram_configured: boolean;
  discord_configured: boolean;
  webhook_configured: boolean;
}

export interface AlertTestResponse {
  channel: AlertChannelName;
  status: string;
}

// ---- Trade Journal ----

export interface JournalSummary {
  total_trades: number | null;
  completed_trades: number | null;
  open_trades: number | null;
  win_rate: number | null;
  profit_factor: number | null;
  avg_winner: number | null;
  avg_loser: number | null;
  avg_pnl: number | null;
  median_pnl: number | null;
  avg_r_multiple: number | null;
  max_consecutive_losses: number | null;
  max_drawdown: number | null;
  avg_holding_days: number | null;
  error: string | null;
}

export interface DecayWindow {
  trade_count: number;
  win_rate: number | null;
  avg_r_multiple: number | null;
  expectancy: number | null;
}

export interface JournalDecay {
  windows: Record<string, DecayWindow>;
}

export interface JournalTrade {
  id: number;
  ticker: string;
  entry_date: string | null;
  entry_price: number | null;
  entry_thesis: string | null;
  signal_strength: number | null;
  stop_loss: number | null;
  target_1: number | null;
  target_2: number | null;
  entry_status: string | null;
  skip_reason: string | null;
  actual_entry_date: string | null;
  actual_entry_price: number | null;
  exit_date: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  holding_days: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  r_multiple: number | null;
  notes: string | null;
  created_at: string | null;
}

export interface JournalTradesResponse {
  trades: JournalTrade[];
}

export interface MaeMfeDistributionPoint {
  trade_id: number;
  ticker: string;
  mae_pct: number;
  mfe_pct: number;
  pnl: number | null;
  r_multiple: number | null;
  exit_reason: string | null;
}

export interface MaeMfeDistributionResponse {
  points: MaeMfeDistributionPoint[];
  warnings: string[];
}

// ---- Point-in-time replay (backend/app/api/replay.py, schemas mirrored
// verbatim from backend/app/api/schemas.py) ----

export type ExecutionMode = "NEXT_OPEN" | "SAME_CLOSE_SLIPPAGE";

export type LevelType = "PERCENTAGE" | "FIXED" | "ATR";

export interface HistoricalDateScanRequest {
  strategy: Strategy;
  strategy_params?: Record<string, number>;
  tickers?: string[] | null;
  target_date: string;
  account_equity?: number;
  risk_per_trade_pct?: number;
  earnings_blackout?: boolean;
}

// `SetupResponse` in schemas.py - identical shape to `ScreenerSetup` above
// (same backend model), aliased so replay call sites read as what they are.
export type HistoricalSetup = ScreenerSetup;

export interface HistoricalDateScanResponse {
  target_date: string;
  setups: HistoricalSetup[];
  scanned: number;
  skipped: number;
  skip_reasons: Record<string, number>;
  warnings: string[];
}

export interface SimulateTradeExecutionRequest {
  ticker: string;
  entry_date: string;
  sl_type: LevelType;
  sl_value: number;
  tp_type: LevelType;
  tp_value: number;
  direction?: 1 | -1;
  account_equity?: number;
  risk_per_trade_pct?: number;
  execution_mode?: ExecutionMode;
  slippage_pct?: number;
  commission?: number;
  fee_per_share?: number;
  atr_slippage_multiple?: number;
  impact_coefficient?: number;
  avg_volume_lookback?: number;
  // Walk the fill forward to a resolved exit (stop/target/regime/timeout)
  // and populate realized_pnl_*/holding_period_days/exit_* below. Defaults
  // true on the backend (backend/app/api/schemas.py SimulateTradeExecutionRequest).
  resolve_exit?: boolean;
  max_holding_period_days?: number;
  use_regime_filter?: boolean;
}

export type ExitTrigger = "STOP" | "TARGET" | "REGIME" | "TIMEOUT";

// Entry fill economics (spread + market impact -> fill price -> sized
// stop/target/shares), plus - when `resolve_exit` (default true) walks the
// trade forward - the realized outcome fields. Those are null when
// resolve_exit=False or there were no bars after the fill to walk forward
// on (backend/app/api/schemas.py SimulateTradeExecutionResponse, ~934).
export interface SimulateTradeExecutionResponse {
  ticker: string;
  execution_mode: ExecutionMode;
  fill_date: string;
  fill_price: number;
  reference_price: number;
  slippage_pct_applied: number;
  spread_pct: number;
  market_impact_pct: number;
  spread_variance_pct: number;
  slippage_cost: number;
  commission_cost: number;
  total_cost: number;
  shares: number;
  stop_loss: number;
  take_profit: number;
  risk_amount: number;
  reward_risk_ratio: number;
  notional_value: number;
  tradable: boolean;
  note: string | null;
  realized_pnl_dollars: number | null;
  realized_pnl_pct: number | null;
  holding_period_days: number | null;
  exit_trigger: ExitTrigger | null;
  mae_pct: number | null;
  mfe_pct: number | null;
  exit_date: string | null;
  exit_price: number | null;
}

// ---- Raw bars (charting) - backend/app/api/replay.py GET /bars ----

export interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface BarsResponse {
  ticker: string;
  bars: Bar[];
}

// ---- Journal: simulate (persist a resolved simulate-trade-execution
// result as a closed MANUAL_SIMULATION entry) ----

export interface JournalSimulateRequest {
  ticker: string;
  entry_date: string;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  exit_date: string;
  exit_price: number;
  exit_trigger: ExitTrigger;
  signal_strength?: number;
  entry_thesis?: string;
  post_mortem_note: string;
}

export interface JournalTradeResponse {
  id: number;
  ticker: string;
  entry_date: string | null;
  entry_price: number | null;
  entry_thesis: string | null;
  signal_strength: number | null;
  stop_loss: number | null;
  target_1: number | null;
  target_2: number | null;
  entry_status: string | null;
  skip_reason: string | null;
  actual_entry_date: string | null;
  actual_entry_price: number | null;
  exit_date: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  holding_days: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  r_multiple: number | null;
  notes: string | null;
  created_at: string | null;
  source: string | null;
}
