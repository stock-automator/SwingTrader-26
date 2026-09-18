"""
Pydantic request/response models for the public API.

Response bodies are deliberately thin wrappers: the quant layer
(`quant/backtest.py`, `quant/setups.py`) already produces JSON-ready dicts
via `comparison_to_payload` / `ScanReport.as_dict`, so most response models
here just declare the shape for OpenAPI rather than re-deriving it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..quant.backtest import DEFAULT_INITIAL_CAPITAL
from ..quant.engine import DEFAULT_ACCOUNT_TIERS, DEFAULT_FEE_PER_SHARE
from ..quant.risk import MIN_REWARD_RISK_RATIO
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
