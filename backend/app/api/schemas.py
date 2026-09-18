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
from ..quant.strategies import REGISTRY


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


class ScreenerResponse(BaseModel):
    setups: list[SetupResponse]
    scanned: int
    skipped: int
    skip_reasons: dict[str, int]


class HealthResponse(BaseModel):
    status: str
    finnhub_configured: bool
    allow_downloads: bool
