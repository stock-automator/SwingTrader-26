"""
Performance metrics engine.

Computes standard trading-performance statistics from a closed-trades
DataFrame (as produced by `engine/backtester.py` or `engine/forward_tester.py`)
and an equity curve, and exports both to disk for reporting.
"""

from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")  # headless/non-interactive backend, safe for tests and cron jobs
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def compute_metrics(
    trades_df: pd.DataFrame,
    equity_curve: Optional[pd.Series] = None,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> dict:
    """Compute standard performance metrics.

    Args:
        trades_df: One row per closed trade, must have a `pnl` column
            (absolute currency P&L per trade).
        equity_curve: Time-indexed equity series. Required for Sharpe,
            Sortino, max drawdown and CAGR - those are `0.0`/`None` if
            omitted or too short.
        risk_free_rate: Annualized risk-free rate, e.g. 0.04 for 4%.
        periods_per_year: Bars per year used to annualize Sharpe/Sortino,
            e.g. 252 for daily bars.

    Returns:
        Dict with: total_trades, win_rate, expectancy, profit_factor,
        sharpe_ratio, sortino_ratio, max_drawdown_pct, cagr_pct,
        max_r_multiple.
    """
    metrics: dict = {}

    if len(trades_df) == 0:
        metrics.update(
            {
                "total_trades": 0,
                "win_rate": 0.0,
                "expectancy": 0.0,
                "profit_factor": 0.0,
                "max_r_multiple": None,
            }
        )
    else:
        pnl = trades_df["pnl"]
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]

        gross_win = wins.sum()
        gross_loss = abs(losses.sum())

        metrics["total_trades"] = int(len(pnl))
        metrics["win_rate"] = float(len(wins) / len(pnl))
        metrics["expectancy"] = float(pnl.mean())
        if gross_loss > 0:
            metrics["profit_factor"] = float(gross_win / gross_loss)
        else:
            metrics["profit_factor"] = float("inf") if gross_win > 0 else 0.0
        metrics["max_r_multiple"] = _max_r_multiple(trades_df)

    if equity_curve is not None and len(equity_curve) > 1:
        metrics.update(
            _equity_curve_metrics(equity_curve, risk_free_rate, periods_per_year)
        )
    else:
        metrics.update(
            {
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "cagr_pct": None,
            }
        )

    return metrics


def _max_r_multiple(trades_df: pd.DataFrame) -> float | None:
    """Best realised reward:risk ratio across `trades_df`'s closed trades,
    or `None` if the frame doesn't carry the columns needed to compute it.

    `engine.py`'s `run_backtest` produces `EntryPrice`/`ExitPrice`/`SL` (the
    `backtesting` library's native trade-record columns); other trade
    sources in this codebase (e.g. `forward_tester.py`) don't, and this is
    an optional enrichment, not a required stat - a missing column set
    returns `None` rather than raising.
    """
    required = {"EntryPrice", "ExitPrice", "SL"}
    if not required.issubset(trades_df.columns):
        return None

    valid = trades_df.dropna(subset=list(required))
    if valid.empty:
        return None

    risk_per_share = (valid["EntryPrice"] - valid["SL"]).abs()
    reward_per_share = valid["ExitPrice"] - valid["EntryPrice"]

    sized = risk_per_share > 0
    if not sized.any():
        return None

    r_multiples = reward_per_share[sized] / risk_per_share[sized]
    return float(r_multiples.max())


def _equity_curve_metrics(
    equity_curve: pd.Series, risk_free_rate: float, periods_per_year: int
) -> dict:
    returns = equity_curve.pct_change().dropna()

    metrics = {}

    if len(returns) == 0 or returns.std() == 0:
        metrics["sharpe_ratio"] = 0.0
    else:
        excess = returns - risk_free_rate / periods_per_year
        metrics["sharpe_ratio"] = float(
            excess.mean() / returns.std() * np.sqrt(periods_per_year)
        )

    downside = returns[returns < 0]
    if len(downside) == 0 or downside.std() == 0:
        metrics["sortino_ratio"] = 0.0
    else:
        excess = returns - risk_free_rate / periods_per_year
        metrics["sortino_ratio"] = float(
            excess.mean() / downside.std() * np.sqrt(periods_per_year)
        )

    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    metrics["max_drawdown_pct"] = float(drawdown.min() * 100)

    if isinstance(equity_curve.index, pd.DatetimeIndex):
        start_value, end_value = equity_curve.iloc[0], equity_curve.iloc[-1]
        days = (equity_curve.index[-1] - equity_curve.index[0]).days
        years = days / 365.25
        if years > 0 and start_value > 0:
            metrics["cagr_pct"] = float(
                ((end_value / start_value) ** (1 / years) - 1) * 100
            )
        else:
            metrics["cagr_pct"] = 0.0
    else:
        metrics["cagr_pct"] = None

    return metrics


def export_trades_csv(trades_df: pd.DataFrame, path: str) -> None:
    """Write `trades_df` to `path`, creating parent directories as needed."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    trades_df.to_csv(output_path, index=False)


def save_equity_curve_chart(equity_curve: pd.Series, path: str) -> None:
    """Render `equity_curve` to a PNG at `path`."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(equity_curve.index, equity_curve.values, linewidth=1.5, color="#1f6feb")
    ax.fill_between(
        equity_curve.index, equity_curve.values, alpha=0.08, color="#1f6feb"
    )
    ax.set_title("Equity Curve", fontsize=13, fontweight="bold")
    ax.set_xlabel("Date")
    ax.set_ylabel("Equity ($)")
    ax.yaxis.set_major_formatter(lambda value, _: f"{value:,.0f}")
    ax.grid(True, alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(output_path, dpi=120)
    plt.close(fig)
