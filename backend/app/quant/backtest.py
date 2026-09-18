"""
$1,000 relative-growth benchmark engine.

Answers the question the platform exists to answer: if you had put $1,000
into this strategy over this window, what would you have now - versus
putting the same $1,000 into the asset itself, or into SPY?

Three dollar equity curves, every one initialised at the same baseline on
the same first common date:

    strategy      signal-driven backtest (`quant/engine.py`)
    buy_and_hold  the target asset, bought once at the window's first close
    spy           SPY, bought once at the window's first close

Sharing a baseline *and* an index is what makes the curves comparable. A
benchmark normalised to its own first bar rather than the strategy's, or
carrying trading days the strategy never saw, produces a dollar gap that is
partly just calendar mismatch - so `align_curves` intersects the index
before anything is normalised.

The relative metrics (Jensen's alpha, beta, information ratio, excess
return) are computed here in closed form rather than delegated, so they can
be unit-tested against hand-worked numbers and do not shift when a
reporting library changes an annualisation convention. `save_tearsheet`
delegates the *presentation* layer to quantstats, which nobody should
hand-roll; `tests/test_benchmark.py` cross-checks our Sharpe against
quantstats' to keep the in-house math honest.

Non-finite results are returned as `None`, never NaN or inf. A metric can be
genuinely undefined (beta against a flat benchmark, profit factor with no
losing trades), and `json.dumps` emits bare `NaN`/`Infinity` for those,
which is invalid JSON that `JSON.parse` rejects in the browser.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.loader import SPY_TICKER
from .engine import BacktestResult, run_backtest
from .metrics import compute_metrics
from .risk import RiskManager
from .strategies.base import BaseStrategy

#: Product default. Every curve starts here so the headline reads in dollars.
DEFAULT_INITIAL_CAPITAL = 1000.0

#: Daily bars -> annualisation factor for Sharpe, alpha and tracking error.
TRADING_DAYS_PER_YEAR = 252

#: Column `backtesting.py` puts the mark-to-market equity series in.
EQUITY_COLUMN = "Equity"

#: Curve keys, in the order the frontend renders them.
STRATEGY_KEY = "strategy"
BUY_HOLD_KEY = "buy_and_hold"
SPY_KEY = "spy"


def _finite(value: float | None) -> float | None:
    """`float(value)` if it is finite, else None. See the module docstring on
    why NaN and inf must not reach the API boundary."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class CurveSummary:
    """Absolute performance of a single dollar equity curve."""

    label: str
    initial_value: float
    final_value: float
    total_return_pct: float | None
    cagr_pct: float | None
    sharpe_ratio: float | None
    max_drawdown_pct: float | None
    volatility_pct: float | None

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "initial_value": round(self.initial_value, 2),
            "final_value": round(self.final_value, 2),
            "total_return_pct": self.total_return_pct,
            "cagr_pct": self.cagr_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "volatility_pct": self.volatility_pct,
        }


@dataclass(frozen=True)
class RelativeMetrics:
    """Strategy measured against one benchmark curve.

    Attributes:
        alpha_annual_pct: Jensen's alpha, annualised arithmetically
            (`daily_alpha * 252`), in percent. The return the strategy
            produced that its benchmark exposure does not explain.
        beta: Sensitivity of strategy excess returns to benchmark excess
            returns. 1.0 means it moved with the benchmark; None if the
            benchmark never moved.
        sharpe_ratio / benchmark_sharpe_ratio: Annualised, on excess returns.
        information_ratio: Mean active return over tracking error,
            annualised - risk-adjusted skill *relative to* the benchmark,
            which is what "Sharpe vs SPY" informally means.
        excess_return_pct: Strategy total return minus benchmark total
            return, in percentage points. Because both curves start from the
            same capital, this is also the dollar gap over that capital.
        tracking_error_pct: Annualised standard deviation of active returns.
        correlation / r_squared: Fit of the strategy to its benchmark; a low
            r_squared means the alpha and beta above explain little.
    """

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

    def as_dict(self) -> dict:
        return {
            "benchmark_label": self.benchmark_label,
            "alpha_annual_pct": self.alpha_annual_pct,
            "beta": self.beta,
            "sharpe_ratio": self.sharpe_ratio,
            "benchmark_sharpe_ratio": self.benchmark_sharpe_ratio,
            "information_ratio": self.information_ratio,
            "excess_return_pct": self.excess_return_pct,
            "tracking_error_pct": self.tracking_error_pct,
            "correlation": self.correlation,
            "r_squared": self.r_squared,
        }


@dataclass(frozen=True)
class BenchmarkComparison:
    """Full result of one `$1,000 vs buy & hold vs SPY` run."""

    initial_capital: float
    tickers: list[str]
    strategy_name: str
    curves: pd.DataFrame
    summaries: dict[str, CurveSummary]
    vs_spy: RelativeMetrics | None
    vs_buy_and_hold: RelativeMetrics | None
    trades: pd.DataFrame
    trade_metrics: dict
    warnings: list[str] = field(default_factory=list)

    @property
    def start(self) -> pd.Timestamp:
        return self.curves.index[0]

    @property
    def end(self) -> pd.Timestamp:
        return self.curves.index[-1]

    def headline(self) -> str:
        """One-line plain-dollar summary, e.g.
        `"$1,000 grown to $2,450 vs $1,800 in SPY"`."""
        strategy_final = self.summaries[STRATEGY_KEY].final_value
        parts = [f"${self.initial_capital:,.0f} grown to ${strategy_final:,.0f}"]
        if SPY_KEY in self.summaries:
            parts.append(f"${self.summaries[SPY_KEY].final_value:,.0f} in SPY")
        if BUY_HOLD_KEY in self.summaries:
            label = "/".join(self.tickers)
            parts.append(
                f"${self.summaries[BUY_HOLD_KEY].final_value:,.0f} "
                f"buying & holding {label}"
            )
        return " vs ".join(parts)

    def curve_records(self) -> list[dict]:
        """Curves as JSON-ready rows: `{"date": "YYYY-MM-DD", <key>: dollars}`.

        Down-sampling is deliberately *not* done here - lightweight-charts
        handles a few thousand points fine, and thinning an equity curve
        moves the drawdowns a reader is looking for.
        """
        frame = self.curves.round(2)
        records = []
        for timestamp, row in frame.iterrows():
            record: dict = {"date": timestamp.strftime("%Y-%m-%d")}
            for key, value in row.items():
                record[str(key)] = _finite(value)
            records.append(record)
        return records


def extract_strategy_equity(
    result: BacktestResult, initial_capital: float
) -> pd.Series:
    """Pull the dollar equity series out of a `BacktestResult`.

    Raises:
        ValueError: if the curve has no `Equity` column. `backtesting.py`
            owns that column name, so a version bump renaming it would
            otherwise surface as a `KeyError` three layers up - or worse, as
            a silently flat benchmark if a caller used `.get()`.
    """
    curve = result.equity_curve

    if EQUITY_COLUMN not in curve.columns:
        available = list(curve.columns)
        raise ValueError(
            f"Equity curve has no {EQUITY_COLUMN!r} column (got {available}). "
            "The backtesting library's equity-curve schema may have changed."
        )

    equity = curve[EQUITY_COLUMN].astype("float64")
    if equity.empty:
        raise ValueError("Equity curve is empty")

    return equity.rename(STRATEGY_KEY)


def buy_and_hold_curve(prices: pd.Series, initial_capital: float) -> pd.Series:
    """`initial_capital` invested at the first bar and held.

    Fractional shares are assumed, which is what makes the comparison fair:
    forcing whole shares on a $1,000 baseline would leave an arbitrary cash
    remainder - on a $400 stock, 20% of the account sitting idle - and
    understate the benchmark for reasons that have nothing to do with the
    strategy.

    Raises:
        ValueError: if the series is empty or its first price is not
            positive and finite (a halted or gap-filled first bar would
            otherwise scale the entire curve by inf).
    """
    if prices.empty:
        raise ValueError("price series is empty")

    anchor = float(prices.iloc[0])
    if not math.isfinite(anchor) or anchor <= 0:
        raise ValueError(f"first price must be positive and finite, got {anchor!r}")

    return (prices.astype("float64") / anchor) * initial_capital


def align_curves(curves: dict[str, pd.Series]) -> pd.DataFrame:
    """Intersect the curves' indices and rebase each to its own first
    surviving value's scale.

    The strategy curve is authoritative: benchmarks are reindexed onto it.
    A benchmark missing a bar the strategy traded is forward-filled (a
    holiday or a halt, where the last close is the honest mark); a benchmark
    that starts *after* the strategy truncates the window, because there is
    no defensible way to mark it before its first quote.

    Returns:
        DataFrame with one column per curve, sorted by date, no NaNs.
    """
    if STRATEGY_KEY not in curves:
        raise ValueError(f"curves must contain {STRATEGY_KEY!r}")

    strategy = curves[STRATEGY_KEY].dropna()
    if strategy.empty:
        raise ValueError("strategy curve is empty")

    frame = pd.DataFrame({STRATEGY_KEY: strategy})

    for key, series in curves.items():
        if key == STRATEGY_KEY:
            continue
        aligned = (
            series.dropna()
            .reindex(frame.index.union(series.index))
            .ffill()
            .reindex(frame.index)
        )
        frame[key] = aligned

    frame = frame.dropna()
    if frame.empty:
        raise ValueError("No overlapping dates between the strategy and its benchmarks")

    return frame.sort_index()


def rebase(frame: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    """Scale every column so they all start at exactly `initial_capital`.

    Applied after alignment: truncating the window to a common start moves
    each benchmark's first value off its original anchor, and the headline
    is only meaningful if all three curves begin at the same dollar.
    """
    rebased = {}
    for column in frame.columns:
        series = frame[column]
        anchor = float(series.iloc[0])
        if not math.isfinite(anchor) or anchor <= 0:
            raise ValueError(f"curve {column!r} starts at {anchor!r}; cannot rebase")
        rebased[column] = series / anchor * initial_capital

    return pd.DataFrame(rebased, index=frame.index)


def summarize_curve(
    curve: pd.Series, label: str, risk_free_rate: float = 0.0
) -> CurveSummary:
    """Absolute stats for one dollar curve."""
    initial_value = float(curve.iloc[0])
    final_value = float(curve.iloc[-1])

    returns = curve.pct_change().dropna()
    total_return_pct = (
        (final_value / initial_value - 1) * 100 if initial_value > 0 else None
    )

    # `compute_metrics` already owns Sharpe/drawdown/CAGR for equity curves;
    # reimplementing them here would let the API and the CLI reports drift.
    stats = compute_metrics(
        pd.DataFrame({"pnl": []}),
        equity_curve=curve,
        risk_free_rate=risk_free_rate,
        periods_per_year=TRADING_DAYS_PER_YEAR,
    )

    volatility_pct = (
        float(returns.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100)
        if len(returns) > 1
        else None
    )

    return CurveSummary(
        label=label,
        initial_value=initial_value,
        final_value=final_value,
        total_return_pct=_finite(total_return_pct),
        cagr_pct=_finite(stats.get("cagr_pct")),
        sharpe_ratio=_finite(stats.get("sharpe_ratio")),
        max_drawdown_pct=_finite(stats.get("max_drawdown_pct")),
        volatility_pct=_finite(volatility_pct),
    )


def relative_metrics(
    strategy_curve: pd.Series,
    benchmark_curve: pd.Series,
    benchmark_label: str,
    risk_free_rate: float = 0.0,
) -> RelativeMetrics:
    """Alpha, beta and relative risk-adjusted stats for one benchmark pair.

    Both curves must already share an index - `align_curves` guarantees it.
    Sample statistics use `ddof=1` throughout so beta's covariance and the
    tracking error's variance are estimated on the same convention.
    """
    strategy_returns = strategy_curve.pct_change().dropna()
    benchmark_returns = benchmark_curve.pct_change().dropna()

    common = strategy_returns.index.intersection(benchmark_returns.index)
    strategy_returns = strategy_returns.loc[common]
    benchmark_returns = benchmark_returns.loc[common]

    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess_strategy = strategy_returns - daily_rf
    excess_benchmark = benchmark_returns - daily_rf

    beta = alpha_annual_pct = None
    correlation = r_squared = None

    if len(common) > 1:
        benchmark_variance = float(excess_benchmark.var(ddof=1))
        if benchmark_variance > 0:
            covariance = float(excess_strategy.cov(excess_benchmark))
            beta = covariance / benchmark_variance
            daily_alpha = float(excess_strategy.mean()) - beta * float(
                excess_benchmark.mean()
            )
            alpha_annual_pct = daily_alpha * TRADING_DAYS_PER_YEAR * 100

        if float(strategy_returns.std(ddof=1)) > 0 and benchmark_variance > 0:
            correlation = float(strategy_returns.corr(benchmark_returns))
            r_squared = correlation**2

    active = (strategy_returns - benchmark_returns).dropna()
    information_ratio = tracking_error_pct = None
    if len(active) > 1:
        active_std = float(active.std(ddof=1))
        tracking_error_pct = active_std * math.sqrt(TRADING_DAYS_PER_YEAR) * 100
        if active_std > 0:
            information_ratio = (
                float(active.mean()) / active_std * math.sqrt(TRADING_DAYS_PER_YEAR)
            )

    def total_return(curve: pd.Series) -> float | None:
        first = float(curve.iloc[0])
        return (float(curve.iloc[-1]) / first - 1) * 100 if first > 0 else None

    strategy_total = total_return(strategy_curve)
    benchmark_total = total_return(benchmark_curve)
    excess_return_pct = (
        strategy_total - benchmark_total
        if strategy_total is not None and benchmark_total is not None
        else None
    )

    return RelativeMetrics(
        benchmark_label=benchmark_label,
        alpha_annual_pct=_finite(alpha_annual_pct),
        beta=_finite(beta),
        sharpe_ratio=_finite(_annualized_sharpe(excess_strategy, strategy_returns)),
        benchmark_sharpe_ratio=_finite(
            _annualized_sharpe(excess_benchmark, benchmark_returns)
        ),
        information_ratio=_finite(information_ratio),
        excess_return_pct=_finite(excess_return_pct),
        tracking_error_pct=_finite(tracking_error_pct),
        correlation=_finite(correlation),
        r_squared=_finite(r_squared),
    )


def _annualized_sharpe(excess: pd.Series, raw: pd.Series) -> float | None:
    """Mean excess return over the *raw* return standard deviation,
    annualised - matching `analytics.metrics._equity_curve_metrics` so the
    API and the CLI report the same Sharpe for the same curve."""
    if len(raw) < 2:
        return None
    deviation = float(raw.std(ddof=1))
    if deviation <= 0:
        return None
    return float(excess.mean()) / deviation * math.sqrt(TRADING_DAYS_PER_YEAR)


def run_comparison(
    strategy: BaseStrategy,
    frames: dict[str, pd.DataFrame],
    spy_frame: pd.DataFrame | None = None,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    risk_per_trade_pct: float = 0.02,
    commission: float = 0.001,
    slippage_pct: float = 0.0005,
    risk_free_rate: float = 0.0,
    include_buy_and_hold: bool = True,
    fee_per_share: float = 0.0,
    atr_slippage_multiple: float = 0.0,
) -> BenchmarkComparison:
    """Run `strategy` over `frames` and compare it to buy & hold and SPY.

    Args:
        strategy: The strategy under test.
        frames: `{ticker: OHLCV}`. More than one ticker is run as equally
            weighted independent sleeves - `initial_capital / n` each, no
            capital shared between them, summed into one curve. Sleeves
            cannot fund each other, so this is a floor on what a pooled
            portfolio would do, not an estimate of it.
        spy_frame: SPY bars for the benchmark. None omits the SPY curve,
            which is what happens when the provider is unreachable; the run
            still returns, with a warning, rather than failing outright.
        initial_capital: Baseline every curve starts at. Defaults to $1,000.
        risk_per_trade_pct: Fraction of sleeve equity risked per trade.
        commission: Round-trip commission rate. Superseded by
            `fee_per_share` (not stacked) if that's set.
        slippage_pct: Spread applied to fills. Superseded by
            `atr_slippage_multiple` (not stacked) if that's set.
        risk_free_rate: Annualised risk-free rate for Sharpe and alpha.
        include_buy_and_hold: Emit the buy & hold curve for `frames`.
        fee_per_share: See `engine.run_backtest`. `0.0` keeps the flat-rate
            `commission` model.
        atr_slippage_multiple: See `engine.run_backtest`. `0.0` keeps the
            flat-rate `slippage_pct` model.

    Raises:
        ValueError: if `frames` is empty, or a frame is too short to produce
            an equity curve.
    """
    if not frames:
        raise ValueError("frames must contain at least one ticker")

    tickers = list(frames)
    sleeve_capital = initial_capital / len(tickers)
    warnings: list[str] = []

    sleeve_curves: list[pd.Series] = []
    all_trades: list[pd.DataFrame] = []

    for ticker, df in frames.items():
        risk_manager = RiskManager(
            account_equity=sleeve_capital, risk_per_trade_pct=risk_per_trade_pct
        )
        result = run_backtest(
            strategy,
            df,
            risk_manager,
            commission=commission,
            slippage_pct=slippage_pct,
            fee_per_share=fee_per_share,
            atr_slippage_multiple=atr_slippage_multiple,
        )

        sleeve_curves.append(extract_strategy_equity(result, sleeve_capital))

        trades = result.trades.copy()
        if not trades.empty:
            trades.insert(0, "ticker", ticker)
            all_trades.append(trades)
        else:
            warnings.append(
                f"{ticker}: strategy took no trades in this window. With a "
                f"${sleeve_capital:,.0f} sleeve, whole-share sizing rounds to "
                "zero shares whenever the stop distance exceeds the risk "
                "budget - raise initial capital or risk per trade."
            )

    # Sleeves are summed on the union of their dates, forward-filling each so
    # a ticker with a missing bar holds its last mark instead of dropping the
    # whole portfolio's row.
    strategy_equity = (
        pd.concat(sleeve_curves, axis=1).ffill().bfill().sum(axis=1)
        if len(sleeve_curves) > 1
        else sleeve_curves[0]
    ).rename(STRATEGY_KEY)

    curves: dict[str, pd.Series] = {STRATEGY_KEY: strategy_equity}

    if include_buy_and_hold:
        per_sleeve = [
            buy_and_hold_curve(df["Close"], sleeve_capital) for df in frames.values()
        ]
        curves[BUY_HOLD_KEY] = (
            pd.concat(per_sleeve, axis=1).ffill().bfill().sum(axis=1)
            if len(per_sleeve) > 1
            else per_sleeve[0]
        ).rename(BUY_HOLD_KEY)

    if spy_frame is not None and not spy_frame.empty:
        curves[SPY_KEY] = buy_and_hold_curve(
            spy_frame["Close"], initial_capital
        ).rename(SPY_KEY)
    else:
        warnings.append(
            f"{SPY_TICKER} bars unavailable - the SPY benchmark and every "
            "metric relative to it were omitted from this run."
        )

    aligned = rebase(align_curves(curves), initial_capital)

    summaries = {
        key: summarize_curve(aligned[key], _curve_label(key, tickers), risk_free_rate)
        for key in aligned.columns
    }

    vs_spy = (
        relative_metrics(
            aligned[STRATEGY_KEY], aligned[SPY_KEY], SPY_TICKER, risk_free_rate
        )
        if SPY_KEY in aligned.columns
        else None
    )
    vs_buy_and_hold = (
        relative_metrics(
            aligned[STRATEGY_KEY],
            aligned[BUY_HOLD_KEY],
            f"Buy & hold {'/'.join(tickers)}",
            risk_free_rate,
        )
        if BUY_HOLD_KEY in aligned.columns
        else None
    )

    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    trade_metrics = compute_metrics(
        trades if not trades.empty else pd.DataFrame({"pnl": []}),
        equity_curve=aligned[STRATEGY_KEY],
        risk_free_rate=risk_free_rate,
        periods_per_year=TRADING_DAYS_PER_YEAR,
    )

    return BenchmarkComparison(
        initial_capital=initial_capital,
        tickers=tickers,
        strategy_name=strategy.name,
        curves=aligned,
        summaries=summaries,
        vs_spy=vs_spy,
        vs_buy_and_hold=vs_buy_and_hold,
        trades=trades,
        trade_metrics={
            k: _finite(v) if isinstance(v, float) else v
            for k, v in trade_metrics.items()
        },
        warnings=warnings,
    )


def _curve_label(key: str, tickers: list[str]) -> str:
    if key == STRATEGY_KEY:
        return "Strategy"
    if key == BUY_HOLD_KEY:
        return f"Buy & hold {'/'.join(tickers)}"
    if key == SPY_KEY:
        return f"{SPY_TICKER} (S&P 500 ETF)"
    return key


def save_tearsheet(
    comparison: BenchmarkComparison, path: str | Path, title: str | None = None
) -> Path:
    """Write a quantstats HTML tear-sheet for the strategy vs SPY.

    Presentation only - every number the API reports comes from this
    module's own math, so a quantstats version bump cannot move the
    platform's headline figures. Uses the SPY curve as the benchmark when
    present, otherwise buy & hold.

    Raises:
        RuntimeError: if quantstats is not installed. It is an optional
            extra (`pip install -r backend/requirements-report.txt`) because
            it pulls in a large plotting stack the API does not otherwise
            need.
    """
    try:
        import quantstats as qs
    except ImportError as exc:  # pragma: no cover - exercised by hand
        raise RuntimeError(
            "quantstats is required for tear-sheets: "
            "pip install -r backend/requirements-report.txt"
        ) from exc

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    strategy_returns = comparison.curves[STRATEGY_KEY].pct_change().dropna()
    benchmark_key = SPY_KEY if SPY_KEY in comparison.curves else BUY_HOLD_KEY
    benchmark_returns = comparison.curves[benchmark_key].pct_change().dropna()

    qs.reports.html(
        strategy_returns,
        benchmark=benchmark_returns,
        output=str(output),
        title=title or f"{comparison.strategy_name} - {comparison.headline()}",
        download_filename=str(output),
    )
    return output


def comparison_to_payload(comparison: BenchmarkComparison) -> dict:
    """Flatten a comparison into the JSON body `/api/v1/backtest` returns."""
    return {
        "strategy": comparison.strategy_name,
        "tickers": comparison.tickers,
        "initial_capital": comparison.initial_capital,
        "start": comparison.start.strftime("%Y-%m-%d"),
        "end": comparison.end.strftime("%Y-%m-%d"),
        "headline": comparison.headline(),
        "summaries": {k: v.as_dict() for k, v in comparison.summaries.items()},
        "vs_spy": comparison.vs_spy.as_dict() if comparison.vs_spy else None,
        "vs_buy_and_hold": (
            comparison.vs_buy_and_hold.as_dict() if comparison.vs_buy_and_hold else None
        ),
        "trade_metrics": comparison.trade_metrics,
        "equity_curves": comparison.curve_records(),
        "trades": _trades_payload(comparison.trades),
        "warnings": comparison.warnings,
    }


def _trades_payload(trades: pd.DataFrame) -> list[dict]:
    """Closed trades as JSON-ready rows, newest first."""
    if trades.empty:
        return []

    columns = {
        "ticker": "ticker",
        "EntryTime": "entry_time",
        "ExitTime": "exit_time",
        "EntryPrice": "entry_price",
        "ExitPrice": "exit_price",
        "Size": "size",
        "PnL": "pnl",
        "ReturnPct": "return_pct",
    }
    present = {src: dst for src, dst in columns.items() if src in trades.columns}
    frame = trades[list(present)].rename(columns=present)

    records = []
    for _, row in frame.iterrows():
        record = {}
        for key, value in row.items():
            if isinstance(value, pd.Timestamp):
                record[key] = value.strftime("%Y-%m-%d")
            elif isinstance(value, (int, float, np.integer, np.floating)):
                record[key] = _finite(float(value))
            else:
                record[key] = value
        records.append(record)

    return records[::-1]
