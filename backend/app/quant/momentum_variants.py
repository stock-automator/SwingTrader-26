"""
Momentum variants on point-in-time S&P 500 membership (cycle 04c).

Every candidate reuses `momentum.simulate` (month-end decision at the close,
fills at the next open, 0.05% slippage per side), the baseline's eligibility
rules (member, close >= $5, $20M ADV, 6-1 lookback) and its top 10:

1. Vol-scaled (Barroso and Santa-Clara 2015): baseline weights times
   min(1, 15% / realised vol of the unscaled strategy's daily returns over
   the last 126 days); the rest stays in cash.
2. Smooth, "frog in the pan" (Da, Gurun and Warachka 2014): among the top 2N
   by 6-1 return, rank on sign(return) x (% up days - % down days) over the
   same window and hold the top N.
3. Residual (Blitz, Huij and Martens 2011): regress monthly returns on SPY
   over up to 36 months; rank on the 6-1 sum of residuals over their
   standard deviation.
4. 52-week high (George and Hwang 2004): rank on close / 252-day high close.
5. Inverse vol: the baseline's names weighted by 1 / 60-day volatility.

Ties in a candidate's score are broken by 6-1 return, then by ticker.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .momentum import (
    MomentumConfig,
    Panel,
    Selector,
    eligibility,
    momentum_scores,
    momentum_selector,
    month_end_mask,
    select_holdings,
    simulate,
)

BASELINE = MomentumConfig(lookback=126, skip=21, top_n=10)

VOL_SCALED = "Vol-scaled (15% target)"
SMOOTH = "Smooth momentum (FIP)"
RESIDUAL = "Residual momentum (vs SPY)"
HIGH_52W = "52-week-high proximity"
INVERSE_VOL = "Inverse-vol weighted"
CANDIDATES = [VOL_SCALED, SMOOTH, RESIDUAL, HIGH_52W, INVERSE_VOL]

VOL_TARGET = 0.15
VOL_DAYS = 126
VOL_MIN_DAYS = 21
RESID_MONTHS = 36
RESID_MIN_MONTHS = 12
RESID_RECENT = 5  # monthly returns from 6 months ago to 1 month ago
HIGH_DAYS = 252
INV_VOL_DAYS = 60


# --- 1. volatility scaling ---------------------------------------------------


def strategy_returns(equity: pd.Series, holdings: pd.Series) -> pd.Series:
    """Daily returns from the first day holding anything (the flat cash
    period before it would understate volatility)."""
    invested = holdings[holdings > 0]
    if invested.empty:
        return pd.Series(dtype=float)
    first = invested.index[0]
    return equity.pct_change().loc[first:]


def vol_scale(
    returns: pd.Series,
    target: float = VOL_TARGET,
    days: int = VOL_DAYS,
    min_days: int = VOL_MIN_DAYS,
) -> pd.Series:
    """min(1, target / annualised realised vol of the last `days` returns),
    known at each close; 1.0 (unscaled) before `min_days` returns."""
    vol = returns.rolling(days, min_periods=min_days).std() * math.sqrt(252)
    return (target / vol).clip(upper=1.0).fillna(1.0)


def vol_scaled_selector(
    panel: Panel, cfg: MomentumConfig, scale: pd.Series
) -> Selector:
    base = momentum_selector(panel, cfg)
    factor = scale.reindex(panel.index).fillna(1.0).to_numpy()

    def select(i: int, held: set[str]) -> dict[str, float]:
        return {t: w * factor[i] for t, w in base(i, held).items()}

    return select


def baseline_scale(panel: Panel, cfg: MomentumConfig = BASELINE) -> pd.Series:
    """The scale series from one unscaled baseline run over all bars."""
    ref = simulate(
        panel, momentum_selector(panel, cfg), panel.index[0], panel.index[-1]
    )
    return vol_scale(strategy_returns(ref.equity, ref.holdings))


# --- ranking helpers ---------------------------------------------------------


def _with_tiebreak(primary: pd.DataFrame, secondary: pd.DataFrame) -> pd.DataFrame:
    """Same order as `primary`; equal values ordered by `secondary`."""
    tie = secondary.rank(axis=1, pct=True).fillna(0.0) * 0.5
    return primary.rank(axis=1, method="min") + tie


def ranked_selector(panel: Panel, cfg: MomentumConfig, score: pd.DataFrame) -> Selector:
    """Equal-weight top N by `score` among baseline-eligible names, with the
    baseline's hold buffer (top `buffer_mult` x N)."""
    mom = momentum_scores(panel.close, panel.mark, cfg.lookback, cfg.skip)
    rank = _with_tiebreak(score, mom)
    ok = eligibility(panel, cfg) & score.notna()

    def select(i: int, held: set[str]) -> dict[str, float]:
        names = select_holdings(
            rank.iloc[i], ok.iloc[i], held, cfg.top_n, cfg.buffer_mult
        )
        return {t: 1.0 / cfg.top_n for t in names}

    return select


# --- 2. smooth momentum ------------------------------------------------------


def fip_scores(
    close: pd.DataFrame, mark: pd.DataFrame, lookback: int, skip: int
) -> pd.DataFrame:
    """sign(lookback-skip return) x (% up days - % down days) over the
    daily returns in the same window; NaN without full history."""
    ret = mark.pct_change(fill_method=None)
    window = lookback - skip

    def share(days: pd.DataFrame) -> pd.DataFrame:
        counts = days.astype(float).where(ret.notna())
        return counts.rolling(window, min_periods=window).sum().shift(skip)

    mom = momentum_scores(close, mark, lookback, skip)
    return np.sign(mom) * (share(ret > 0) - share(ret < 0)) / window


def _top(scores: pd.Series, k: int) -> pd.Index:
    """Best `k` by score, ties by ticker."""
    return scores.sort_index().sort_values(ascending=False, kind="stable").index[:k]


def smooth_selector(panel: Panel, cfg: MomentumConfig) -> Selector:
    mom = momentum_scores(panel.close, panel.mark, cfg.lookback, cfg.skip)
    fip = fip_scores(panel.close, panel.mark, cfg.lookback, cfg.skip)
    ok = eligibility(panel, cfg) & fip.notna()

    def select(i: int, held: set[str]) -> dict[str, float]:
        m = mom.iloc[i][ok.iloc[i]]
        pool = _top(m, cfg.buffer_mult * cfg.top_n)
        rank = _with_tiebreak(fip.iloc[[i]][pool], mom.iloc[[i]][pool]).iloc[0]
        names = _top(rank, cfg.top_n)
        return {t: 1.0 / cfg.top_n for t in names}

    return select


# --- 3. residual momentum ----------------------------------------------------


def residual_scores(
    panel: Panel,
    months: int = RESID_MONTHS,
    min_months: int = RESID_MIN_MONTHS,
    recent: int = RESID_RECENT,
) -> pd.DataFrame:
    """On each month end k: regress the last `months` monthly returns (to k)
    on SPY's; score = sum / std of the residuals of months k-`recent`..k-1.
    Needs `min_months` returns. NaN on every other day."""
    ends = panel.index[month_end_mask(panel.index)]
    stock = panel.mark.loc[ends].pct_change(fill_method=None).to_numpy()
    spy = panel.spy_close.reindex(panel.index).ffill().loc[ends]
    market = spy.pct_change(fill_method=None).to_numpy()
    out = np.full(stock.shape, np.nan)
    last = slice(-recent - 1, -1)  # months k-recent..k-1 of each window
    for k in range(recent + 1, len(ends)):
        window = slice(max(0, k - months + 1), k + 1)
        y, x = stock[window], market[window, None]
        ok = ~np.isnan(y) & ~np.isnan(x)
        n = ok.sum(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            xm = np.where(ok, x, 0.0).sum(axis=0) / n
            ym = np.where(ok, y, 0.0).sum(axis=0) / n
            dx, dy = np.where(ok, x - xm, 0.0), np.where(ok, y - ym, 0.0)
            beta = (dx * dy).sum(axis=0) / (dx * dx).sum(axis=0)
            alpha = ym - beta * xm
            resid = y[last] - alpha - beta * x[last]
            score = resid.sum(axis=0) / resid.std(axis=0, ddof=1)
        out[k] = np.where((n >= min_months) & np.isfinite(score), score, np.nan)
    frame = pd.DataFrame(out, index=ends, columns=panel.tickers)
    return frame.reindex(panel.index).where(panel.close.notna())


# --- 4. 52-week high ---------------------------------------------------------


def high_proximity(
    close: pd.DataFrame, mark: pd.DataFrame, days: int = HIGH_DAYS
) -> pd.DataFrame:
    """Close / highest close of the last `days` bars (incl. today)."""
    high = mark.rolling(days, min_periods=days).max()
    return (mark / high).where(close.notna())


# --- 5. inverse volatility ---------------------------------------------------


def inverse_vol_selector(
    panel: Panel, cfg: MomentumConfig, days: int = INV_VOL_DAYS
) -> Selector:
    base = momentum_selector(panel, cfg)
    vol = panel.mark.pct_change(fill_method=None).rolling(days, min_periods=days)
    vol = vol.std()

    def select(i: int, held: set[str]) -> dict[str, float]:
        names = list(base(i, held))
        if not names:
            return {}
        inv = (1.0 / vol.iloc[i][names]).replace(np.inf, np.nan)
        inv = inv.fillna(inv.mean()) if inv.notna().any() else inv.fillna(1.0)
        return (inv / inv.sum()).to_dict()

    return select


# --- all candidates and selection --------------------------------------------


def candidate_selectors(
    panel: Panel, cfg: MomentumConfig = BASELINE
) -> dict[str, Selector]:
    return {
        VOL_SCALED: vol_scaled_selector(panel, cfg, baseline_scale(panel, cfg)),
        SMOOTH: smooth_selector(panel, cfg),
        RESIDUAL: ranked_selector(panel, cfg, residual_scores(panel)),
        HIGH_52W: ranked_selector(panel, cfg, high_proximity(panel.close, panel.mark)),
        INVERSE_VOL: inverse_vol_selector(panel, cfg),
    }


def choose_by_sharpe(stats: dict[str, dict]) -> str:
    """Highest Sharpe; ties: shallower max drawdown, then name."""

    def key(name: str) -> tuple:
        s = stats[name]
        sharpe = s.get("sharpe")
        dd = s.get("max_drawdown_pct")
        return (
            -(sharpe if sharpe is not None else -math.inf),
            -(dd if dd is not None else -math.inf),
            name,
        )

    return min(stats, key=key)
