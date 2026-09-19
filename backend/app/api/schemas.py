"""
Pydantic request/response models for the public API.

Response bodies are deliberately thin wrappers: the quant layer
(`quant/backtest.py`, `quant/setups.py`) already produces JSON-ready dicts
via `comparison_to_payload` / `ScanReport.as_dict`, so most response models
here just declare the shape for OpenAPI rather than re-deriving it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..quant.backtest import DEFAULT_INITIAL_CAPITAL
from ..quant.engine import (
    DEFAULT_ACCOUNT_TIERS,
    DEFAULT_FEE_PER_SHARE,
    DEFAULT_MAX_HOLDING_PERIOD_DAYS,
    EXECUTION_MODE_NEXT_OPEN,
    VALID_EXECUTION_MODES,
)
from ..quant.monte_carlo import DEFAULT_N_SIMULATIONS, DEFAULT_RUIN_THRESHOLD_PCT
from ..quant.risk import MIN_REWARD_RISK_RATIO
from ..quant.slippage_model import DEFAULT_LOOKBACK as DEFAULT_SLIPPAGE_LOOKBACK
from ..quant.strategies import REGISTRY
from ..quant.strategies.base import VALID_LEVEL_TYPES


class BacktestRequest(BaseModel):
    """`POST /api/v1/backtest` body."""

    strategy: str = Field(
        default="donchian_breakout",
        description=f"One of: {', '.join(sorted(REGISTRY))}",
    )
    strategy_params: dict = Field(default_factory=dict)
    tickers: list[str] = Field(
        default_factory=lambda: ["AAPL"],
        description="One or more tickers, run as equally-weighted sleeves.",
    )
    start: str | None = Field(
        default=None, description="Inclusive ISO date, e.g. 2022-01-01"
    )
    end: str | None = Field(default=None, description="Inclusive ISO date")
    initial_capital: float = Field(default=DEFAULT_INITIAL_CAPITAL, gt=0)
    risk_per_trade_pct: float = Field(default=0.02, gt=0, le=1)
    commission: float = Field(default=0.001, ge=0)
    slippage_pct: float = Field(default=0.0005, ge=0)
    risk_free_rate: float = Field(default=0.0, ge=0)
    include_buy_and_hold: bool = True
    benchmark: str = Field(
        default="SPY", description="Ticker for the second benchmark curve."
    )
    fee_per_share: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Fixed $/share maker/taker fee, charged on both legs of a round "
            f"trip (e.g. {DEFAULT_FEE_PER_SHARE} for IBKR-style pricing). "
            "0 (the default) keeps the flat-rate `commission` model."
        ),
    )
    atr_slippage_multiple: float = Field(
        default=0.0,
        ge=0,
        description=(
            "When > 0, replaces `slippage_pct` with a spread scaled off the "
            "traded ticker's own mean ATR/Close ratio."
        ),
    )
    earnings_blackout: bool = Field(
        default=False,
        description="Suppress long entries within 5 trading days of a known earnings date.",
    )
    regime_gating: bool = Field(
        default=False,
        description="Suppress long entries while the SPY macro regime is BEAR_TRENDING or HIGH_VOLATILITY_CHOP.",
    )

    @field_validator("tickers")
    @classmethod
    def _non_empty_tickers(cls, value: list[str]) -> list[str]:
        cleaned = [t.strip().upper() for t in value if t and t.strip()]
        if not cleaned:
            raise ValueError("tickers must contain at least one non-empty symbol")
        return cleaned

    @field_validator("strategy")
    @classmethod
    def _known_strategy(cls, value: str) -> str:
        if value not in REGISTRY:
            known = ", ".join(sorted(REGISTRY))
            raise ValueError(f"Unknown strategy {value!r}. Available: {known}")
        return value


class CurveSummaryResponse(BaseModel):
    label: str
    initial_value: float
    final_value: float
    total_return_pct: float | None
    cagr_pct: float | None
    sharpe_ratio: float | None
    max_drawdown_pct: float | None
    volatility_pct: float | None


class RelativeMetricsResponse(BaseModel):
    benchmark_label: str
    alpha_annual_pct: float | None
    beta: float | None
    sharpe_ratio: float | None
    benchmark_sharpe_ratio: float | None
    information_ratio: float | None
    excess_return_pct: float | None
    tracking_error_pct: float | None
    correlation: float | None
    r_squared: float | None


class BacktestResponse(BaseModel):
    strategy: str
    tickers: list[str]
    initial_capital: float
    start: str
    end: str
    headline: str
    summaries: dict[str, CurveSummaryResponse]
    vs_spy: RelativeMetricsResponse | None
    vs_buy_and_hold: RelativeMetricsResponse | None
    trade_metrics: dict
    equity_curves: list[dict]
    trades: list[dict]
    warnings: list[str]


class SetupResponse(BaseModel):
    ticker: str
    direction: str
    tradable: bool
    as_of: str
    close: float
    regime: str
    adx: float | None
    atr: float | None
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    shares: int | None
    risk_amount: float | None
    relative_strength: float | None
    rank: int | None
    note: str | None
    reward_risk_ratio: float | None = None
    notional_value: float | None = None


class ScreenerResponse(BaseModel):
    setups: list[SetupResponse]
    scanned: int
    skipped: int
    skip_reasons: dict[str, int]
    macro_regime: str = "UNKNOWN"
    circuit_breaker_active: bool = False
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    finnhub_configured: bool
    allow_downloads: bool


class OrderTicketRequest(BaseModel):
    """`POST /api/v1/order-ticket` body - turns one resolved setup into
    broker-ready tickets across the $1k/$5k/$10k comparison tiers."""

    ticker: str = Field(min_length=1)
    entry_price: float = Field(gt=0)
    sl_type: str
    sl_value: float = Field(gt=0)
    tp_type: str
    tp_value: float = Field(gt=0)
    atr: float | None = Field(default=None, gt=0)
    direction: int = Field(default=1)
    order_type: str = Field(default="MARKET")
    account_tiers: list[float] = Field(
        default_factory=lambda: list(DEFAULT_ACCOUNT_TIERS)
    )

    @field_validator("sl_type", "tp_type")
    @classmethod
    def _known_level_type(cls, value: str) -> str:
        if value not in VALID_LEVEL_TYPES:
            raise ValueError(
                f"Unknown level type {value!r}. Available: {sorted(VALID_LEVEL_TYPES)}"
            )
        return value

    @field_validator("direction")
    @classmethod
    def _known_direction(cls, value: int) -> int:
        if value not in (1, -1):
            raise ValueError("direction must be 1 (long) or -1 (short)")
        return value

    @field_validator("account_tiers")
    @classmethod
    def _non_empty_positive_tiers(cls, value: list[float]) -> list[float]:
        if not value or any(v <= 0 for v in value):
            raise ValueError("account_tiers must be non-empty and all positive")
        return value


class OrderTicketResponse(BaseModel):
    ticker: str
    account_equity: float
    order_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: int
    notional_value: float
    risk_amount: float
    reward_risk_ratio: float
    tradable: bool
    note: str | None


class OrderTicketsResponse(BaseModel):
    tickets: list[OrderTicketResponse]
    min_reward_risk_ratio: float = MIN_REWARD_RISK_RATIO


class DataSyncRequest(BaseModel):
    """`POST /api/v1/data/sync` body."""

    tickers: list[str] | None = Field(
        default=None,
        description="Tickers to sync; omit (or null) to sync the full watchlist.",
    )

    @field_validator("tickers")
    @classmethod
    def _upper_tickers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [t.strip().upper() for t in value if t and t.strip()]


class DataSyncResponse(BaseModel):
    status: str
    tickers: list[str]


class CorporateActionResponse(BaseModel):
    anchor_date: str
    old_factor: float
    new_factor: float
    ratio: float


class SyncResultResponse(BaseModel):
    ticker: str
    status: str
    rows_added: int
    corporate_action: CorporateActionResponse | None
    error: str | None


class DataSyncStatusResponse(BaseModel):
    in_progress: bool
    started_at: str | None
    results: list[SyncResultResponse]


class UniverseSyncResponse(BaseModel):
    """`POST /api/v1/universe/sync` response."""

    symbols: list[str]
    synced_at: str
    source_counts: dict[str, int]


class MonteCarloRequest(BaseModel):
    """`POST /api/v1/analytics/monte-carlo` body."""

    strategy: str = Field(default="donchian_breakout")
    strategy_params: dict = Field(default_factory=dict)
    tickers: list[str] = Field(default_factory=lambda: ["AAPL"])
    start: str | None = None
    end: str | None = None
    initial_capital: float = Field(default=DEFAULT_INITIAL_CAPITAL, gt=0)
    risk_per_trade_pct: float = Field(default=0.02, gt=0, le=1)
    commission: float = Field(default=0.001, ge=0)
    slippage_pct: float = Field(default=0.0005, ge=0)
    n_simulations: int = Field(default=DEFAULT_N_SIMULATIONS, ge=1)
    with_replacement: bool = True
    ruin_threshold_pct: float = Field(default=DEFAULT_RUIN_THRESHOLD_PCT, ge=0, lt=1)
    seed: int | None = None

    @field_validator("tickers")
    @classmethod
    def _non_empty_tickers(cls, value: list[str]) -> list[str]:
        cleaned = [t.strip().upper() for t in value if t and t.strip()]
        if not cleaned:
            raise ValueError("tickers must contain at least one non-empty symbol")
        return cleaned

    @field_validator("strategy")
    @classmethod
    def _known_strategy(cls, value: str) -> str:
        if value not in REGISTRY:
            known = ", ".join(sorted(REGISTRY))
            raise ValueError(f"Unknown strategy {value!r}. Available: {known}")
        return value


class MonteCarloResponse(BaseModel):
    n_simulations: int
    n_trades: int
    initial_capital: float
    with_replacement: bool
    ruin_threshold_pct: float
    risk_of_ruin_pct: float
    max_drawdown_p95_pct: float
    max_drawdown_p99_pct: float
    mean_final_equity: float
    median_final_equity: float
    final_equity_p05: float
    final_equity_p95: float
    equity_curve_percentiles: dict[str, list[float]]
    warnings: list[str]


class WalkForwardRequest(BaseModel):
    """`POST /api/v1/analytics/walk-forward` body."""

    strategy: str = Field(default="donchian_breakout")
    strategy_params: dict = Field(default_factory=dict)
    ticker: str = Field(default="AAPL")
    start: str | None = None
    end: str | None = None
    initial_capital: float = Field(default=DEFAULT_INITIAL_CAPITAL, gt=0)
    risk_per_trade_pct: float = Field(default=0.02, gt=0, le=1)
    commission: float = Field(default=0.001, ge=0)
    slippage_pct: float = Field(default=0.0005, ge=0)
    is_months: int = Field(default=12, ge=1)
    oos_months: int = Field(default=3, ge=1)
    step_months: int | None = Field(default=None, ge=1)
    param_name: str | None = Field(
        default=None,
        description="A strategy_params key to run a +/-20% parameter-sensitivity sweep on.",
    )
    param_type: Literal["int", "float"] = "float"
    perturbation_pcts: list[float] | None = Field(default=None)

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("ticker must be non-empty")
        return value

    @field_validator("strategy")
    @classmethod
    def _known_strategy(cls, value: str) -> str:
        if value not in REGISTRY:
            known = ", ".join(sorted(REGISTRY))
            raise ValueError(f"Unknown strategy {value!r}. Available: {known}")
        return value


class WalkForwardWindowResponse(BaseModel):
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    is_return_pct: float | None
    oos_return_pct: float | None
    efficiency: float | None


class ParameterSensitivityPointResponse(BaseModel):
    param_value: float
    return_pct: float | None
    sharpe_ratio: float | None


class ParameterSensitivityResponse(BaseModel):
    param_name: str
    baseline_value: float
    is_cliff: bool
    points: list[ParameterSensitivityPointResponse]


class WalkForwardResponse(BaseModel):
    ticker: str
    is_window_months: int
    oos_window_months: int
    mean_efficiency: float | None
    median_efficiency: float | None
    windows: list[WalkForwardWindowResponse]
    parameter_sensitivity: ParameterSensitivityResponse | None = None
    warnings: list[str] = Field(default_factory=list)


class FactorExposureRequest(BaseModel):
    """`POST /api/v1/analytics/factor-exposure` body."""

    strategy: str = Field(default="donchian_breakout")
    strategy_params: dict = Field(default_factory=dict)
    tickers: list[str] = Field(default_factory=lambda: ["AAPL"])
    start: str | None = None
    end: str | None = None
    initial_capital: float = Field(default=DEFAULT_INITIAL_CAPITAL, gt=0)
    risk_per_trade_pct: float = Field(default=0.02, gt=0, le=1)
    commission: float = Field(default=0.001, ge=0)
    slippage_pct: float = Field(default=0.0005, ge=0)
    risk_free_rate: float = Field(default=0.0, ge=0)
    benchmark: str = Field(default="SPY")

    @field_validator("tickers")
    @classmethod
    def _non_empty_tickers(cls, value: list[str]) -> list[str]:
        cleaned = [t.strip().upper() for t in value if t and t.strip()]
        if not cleaned:
            raise ValueError("tickers must contain at least one non-empty symbol")
        return cleaned

    @field_validator("strategy")
    @classmethod
    def _known_strategy(cls, value: str) -> str:
        if value not in REGISTRY:
            known = ", ".join(sorted(REGISTRY))
            raise ValueError(f"Unknown strategy {value!r}. Available: {known}")
        return value


class FactorExposureResponse(BaseModel):
    alpha_annual_pct: float | None
    beta: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None
    tail_ratio: float | None
    warnings: list[str] = Field(default_factory=list)


# ---- Alerts ----


class AlertDispatchRequest(BaseModel):
    """`POST /api/v1/alerts/dispatch` body."""

    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    ticker: str | None = None
    direction: str | None = None
    url: str | None = None


class AlertDispatchResponse(BaseModel):
    results: dict[str, str] = Field(
        default_factory=dict,
        description="channel name -> 'sent' or 'failed: <reason>'; empty if no "
        "alert channel is configured.",
    )


class AlertChannelsConfigRequest(BaseModel):
    """`PUT /api/v1/alerts/config` body - a full replace of the stored
    channel config; omit a field (or send it empty) to leave that channel
    unconfigured."""

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None
    generic_webhook_url: str | None = None


class AlertChannelsConfigResponse(BaseModel):
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None
    generic_webhook_url: str | None = None
    telegram_configured: bool
    discord_configured: bool
    webhook_configured: bool


class AlertTestRequest(BaseModel):
    """`POST /api/v1/alerts/test` body."""

    channel: Literal["telegram", "discord", "webhook"]


class AlertTestResponse(BaseModel):
    channel: str
    status: str


# ---- Execution (Alpaca paper trading) ----


class ExecutionOrderRequest(BaseModel):
    """`POST /api/v1/execution/orders` body."""

    ticker: str = Field(min_length=1)
    side: Literal["buy", "sell"]
    qty: float = Field(gt=0)
    order_type: Literal["MARKET", "LIMIT", "BRACKET"] = "MARKET"
    limit_price: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, value: str) -> str:
        return value.strip().upper()


class ExecutionOrderResponse(BaseModel):
    id: str
    symbol: str
    qty: str | None
    side: str | None
    type: str | None
    order_class: str | None
    status: str | None
    submitted_at: str | None


class CloseAllPositionsRequest(BaseModel):
    """`POST /api/v1/execution/close-all` body - `confirm` must be `true`;
    this liquidates every open paper position."""

    confirm: bool = False


class ClosedPositionResponse(BaseModel):
    symbol: str | None
    status: int | None
    order_id: str | None


class CloseAllPositionsResponse(BaseModel):
    closed: list[ClosedPositionResponse]


class AlpacaAccountResponse(BaseModel):
    account_number: str
    status: str
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float


class PositionResponse(BaseModel):
    symbol: str
    side: str | None
    qty: float
    avg_entry_price: float
    current_price: float | None
    market_value: float | None
    cost_basis: float
    unrealized_pl: float | None
    unrealized_plpc: float | None


class PositionsResponse(BaseModel):
    positions: list[PositionResponse]


class PortfolioHistoryResponse(BaseModel):
    timestamp: list[str]
    equity: list[float]
    profit_loss: list[float]
    profit_loss_pct: list[float | None]
    base_value: float | None
    timeframe: str


# ---- Signal matrix ----


class SignalMatrixRow(BaseModel):
    """One actionable (LONG/SHORT) setup from one strategy, for the
    cross-strategy "what to look at today" grid."""

    ticker: str
    strategy: str
    direction: str
    tradable: bool
    as_of: str
    close: float
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    shares: int | None
    risk_amount: float | None
    reward_risk_ratio: float | None
    notional_value: float | None
    note: str | None
    win_probability: float | None = Field(
        default=None,
        description=(
            "Historical win-rate estimate from `analytics.expectancy."
            "estimate_win_probability`: a regime-matched backtest win rate "
            "when enough same-regime trades exist, else a block-bootstrap "
            "Monte Carlo percentile over all historical trades, else `null` "
            "when there isn't enough trade history to support either - "
            "never a fabricated number. Always `null` for SHORT rows (the "
            "backtest engine is long-only, so there is nothing to estimate "
            "from). See the sibling `win_probability_*` fields for how a "
            "non-null value was derived."
        ),
    )
    win_probability_method: str | None = Field(
        default=None,
        description=(
            "'regime_matched_backtest', 'block_bootstrap', or "
            "'insufficient_data' - which of `estimate_win_probability`'s "
            "methods produced `win_probability`."
        ),
    )
    win_probability_sample_size: int | None = Field(
        default=None,
        description="Number of historical closed trades backing the estimate.",
    )
    win_probability_confidence_low: float | None = Field(
        default=None,
        description="5th percentile of the block-bootstrap win-rate distribution.",
    )
    win_probability_confidence_high: float | None = Field(
        default=None,
        description="95th percentile of the block-bootstrap win-rate distribution.",
    )
    win_probability_note: str | None = Field(
        default=None,
        description="Human-readable explanation of how win_probability was derived.",
    )


class SignalMatrixResponse(BaseModel):
    generated_at: str
    rows: list[SignalMatrixRow]
    scanned: int
    warnings: list[str] = Field(default_factory=list)


# ---- Point-in-time historical replay ----


class HistoricalDateScanRequest(BaseModel):
    """`POST /api/v1/backtest/historical-date-scan` body."""

    strategy: str = Field(default="donchian_breakout")
    strategy_params: dict = Field(default_factory=dict)
    tickers: list[str] | None = Field(
        default=None, description="Omit to scan the full watchlist."
    )
    target_date: str = Field(description="Inclusive ISO date, e.g. 2023-06-15")
    account_equity: float = Field(default=1000.0, gt=0)
    risk_per_trade_pct: float = Field(default=0.02, gt=0, le=1)
    earnings_blackout: bool = Field(default=False)

    @field_validator("strategy")
    @classmethod
    def _known_strategy(cls, value: str) -> str:
        if value not in REGISTRY:
            known = ", ".join(sorted(REGISTRY))
            raise ValueError(f"Unknown strategy {value!r}. Available: {known}")
        return value

    @field_validator("tickers")
    @classmethod
    def _upper_tickers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [t.strip().upper() for t in value if t and t.strip()]


class HistoricalDateScanResponse(BaseModel):
    target_date: str
    setups: list[SetupResponse]
    scanned: int
    skipped: int
    skip_reasons: dict[str, int]
    warnings: list[str] = Field(default_factory=list)


# ---- Raw bars (charting) ----


class Bar(BaseModel):
    """One OHLCV bar."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class BarsResponse(BaseModel):
    """`GET /api/v1/backtest/bars` - bars on or before `as_of` only, for the
    Simulator UI's candlestick chart. Never carries a bar after `as_of` -
    that is the whole point-in-time guarantee this endpoint exists for."""

    ticker: str
    bars: list[Bar]


# ---- Execution simulation ----


class SimulateTradeExecutionRequest(BaseModel):
    """`POST /api/v1/backtest/simulate-trade-execution` body."""

    ticker: str = Field(min_length=1)
    entry_date: str = Field(description="Inclusive ISO date of the signal bar")
    sl_type: str
    sl_value: float = Field(gt=0)
    tp_type: str
    tp_value: float = Field(gt=0)
    direction: int = Field(default=1)
    account_equity: float = Field(default=1000.0, gt=0)
    risk_per_trade_pct: float = Field(default=0.02, gt=0, le=1)
    execution_mode: str = Field(default=EXECUTION_MODE_NEXT_OPEN)
    slippage_pct: float = Field(default=0.0005, ge=0)
    commission: float = Field(default=0.001, ge=0)
    fee_per_share: float = Field(default=0.0, ge=0)
    atr_slippage_multiple: float = Field(default=0.0, ge=0)
    impact_coefficient: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Scales `slippage_model.estimate_market_impact_pct`'s square-root "
            "participation model. `0.0` (the default) disables market-impact "
            "modelling entirely - existing callers see no behavior change "
            "unless they opt in."
        ),
    )
    avg_volume_lookback: int = Field(
        default=DEFAULT_SLIPPAGE_LOOKBACK,
        ge=1,
        description="Trailing bar count averaged for the market-impact volume denominator.",
    )
    resolve_exit: bool = Field(
        default=True,
        description=(
            "Walk the ticker's bars forward from the fill to resolve the "
            "trade to a stop/target/regime/timeout exit, and populate the "
            "realized_pnl_*/holding_period_days/exit_* response fields. "
            "Set False to keep the prior entry-fill-only behavior."
        ),
    )
    max_holding_period_days: int = Field(default=DEFAULT_MAX_HOLDING_PERIOD_DAYS, ge=1)
    use_regime_filter: bool = Field(
        default=True,
        description=(
            "For long trades, force a REGIME exit the first bar the macro "
            "regime (quant.regime.MacroRegimeDetector) would have blocked "
            "new longs. No effect on short trades (direction=-1)."
        ),
    )

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("execution_mode")
    @classmethod
    def _known_execution_mode(cls, value: str) -> str:
        if value not in VALID_EXECUTION_MODES:
            raise ValueError(
                f"Unknown execution_mode {value!r}. Available: {sorted(VALID_EXECUTION_MODES)}"
            )
        return value

    @field_validator("sl_type", "tp_type")
    @classmethod
    def _known_level_type(cls, value: str) -> str:
        if value not in VALID_LEVEL_TYPES:
            raise ValueError(
                f"Unknown level type {value!r}. Available: {sorted(VALID_LEVEL_TYPES)}"
            )
        return value

    @field_validator("direction")
    @classmethod
    def _known_direction(cls, value: int) -> int:
        if value not in (1, -1):
            raise ValueError("direction must be 1 (long) or -1 (short)")
        return value


# ---- Trade journal ----


class JournalSummaryResponse(BaseModel):
    """`GET /api/v1/journal/summary` - shape mirrors
    `TradeJournal.analyze_all_trades`, which returns `{"error": ...}` when
    there are no completed trades yet rather than a fixed set of keys."""

    total_trades: int | None = None
    completed_trades: int | None = None
    open_trades: int | None = None
    win_rate: float | None = None
    profit_factor: float | None = None
    avg_winner: float | None = None
    avg_loser: float | None = None
    avg_pnl: float | None = None
    median_pnl: float | None = None
    avg_r_multiple: float | None = None
    max_consecutive_losses: int | None = None
    max_drawdown: float | None = None
    avg_holding_days: float | None = None
    error: str | None = None


class DecayWindowResponse(BaseModel):
    trade_count: int
    win_rate: float | None
    avg_r_multiple: float | None
    expectancy: float | None


class JournalDecayResponse(BaseModel):
    windows: dict[str, DecayWindowResponse]


class MaeMfeRequest(BaseModel):
    """`POST /api/v1/journal/mae-mfe` body."""

    trade_id: int
    ticker: str = Field(min_length=1)

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, value: str) -> str:
        return value.strip().upper()


class MaeMfeResponse(BaseModel):
    trade_id: int
    mae_dollars: float
    mae_pct: float
    mfe_dollars: float
    mfe_pct: float


class JournalTradeResponse(BaseModel):
    """One row from `TradeJournal`'s CSV, as-is - the raw feed behind the
    Trade Journal UI's table."""

    id: int
    ticker: str
    entry_date: str | None = None
    entry_price: float | None = None
    entry_thesis: str | None = None
    signal_strength: float | None = None
    stop_loss: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    entry_status: str | None = None
    skip_reason: str | None = None
    actual_entry_date: str | None = None
    actual_entry_price: float | None = None
    exit_date: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    holding_days: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    r_multiple: float | None = None
    notes: str | None = None
    created_at: str | None = None
    source: str | None = None


class JournalTradesResponse(BaseModel):
    trades: list[JournalTradeResponse]


class JournalSimulateRequest(BaseModel):
    """`POST /api/v1/journal/simulate` body - persists one resolved
    `simulate-trade-execution` result (see `SimulateTradeExecutionResponse`)
    as a closed, `MANUAL_SIMULATION`-tagged journal entry via
    `TradeJournal.log_signal` -> `log_entry` -> `log_exit`."""

    ticker: str = Field(min_length=1)
    entry_date: str = Field(description="Inclusive ISO date the position was filled on")
    entry_price: float = Field(gt=0, description="The simulated fill_price")
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    exit_date: str
    exit_price: float = Field(gt=0)
    exit_trigger: Literal["STOP", "TARGET", "REGIME", "TIMEOUT"]
    signal_strength: float = Field(
        default=0.0, description="Optional 0-100 confidence score for cohort analysis"
    )
    entry_thesis: str = Field(default="")
    post_mortem_note: str = Field(min_length=1)

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, value: str) -> str:
        return value.strip().upper()


class MaeMfeDistributionPoint(BaseModel):
    trade_id: int
    ticker: str
    mae_pct: float
    mfe_pct: float
    pnl: float | None
    r_multiple: float | None
    exit_reason: str | None


class MaeMfeDistributionResponse(BaseModel):
    points: list[MaeMfeDistributionPoint]
    warnings: list[str] = Field(default_factory=list)


class SimulateTradeExecutionResponse(BaseModel):
    ticker: str
    execution_mode: str
    fill_date: str
    fill_price: float
    reference_price: float
    slippage_pct_applied: float
    spread_pct: float = Field(
        description="This bar's ATR-implied bid/ask spread, from slippage_model.estimate_dynamic_spread_pct."
    )
    market_impact_pct: float = Field(
        description="Added cost from order size vs. trailing volume, from slippage_model.estimate_market_impact_pct."
    )
    spread_variance_pct: float = Field(
        description="Std-dev of the ATR-implied spread over the trailing window - confidence band on spread_pct."
    )
    slippage_cost: float
    commission_cost: float
    total_cost: float
    shares: int
    stop_loss: float
    take_profit: float
    risk_amount: float
    reward_risk_ratio: float
    notional_value: float
    tradable: bool
    note: str | None
    realized_pnl_dollars: float | None = Field(
        default=None,
        description=(
            "(exit_price - fill_price) * shares * direction. None if "
            "resolve_exit=False or there were no bars after the fill to "
            "walk forward on."
        ),
    )
    realized_pnl_pct: float | None = None
    holding_period_days: int | None = None
    exit_trigger: Literal["STOP", "TARGET", "REGIME", "TIMEOUT"] | None = None
    mae_pct: float | None = Field(
        default=None,
        description="Max adverse excursion vs. fill_price over the actual path from fill to exit_date.",
    )
    mfe_pct: float | None = Field(
        default=None,
        description="Max favorable excursion vs. fill_price over the actual path from fill to exit_date.",
    )
    exit_date: str | None = None
    exit_price: float | None = None
