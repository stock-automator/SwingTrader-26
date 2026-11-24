"""Cycle 07a multi-asset rules, pre-registered in
docs/research/strategy-evidence-2026-11.md ("Cycle 07 pre-registered rules").

Synthetic data for every rule and metric, worked out by hand. The one
real-data test reads the gitignored ETF cache that `scripts/run_multi_asset.py`
writes and skips when the cache is absent (no network in tests).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.etf_strategies import month_end_dates, run_rebalanced
from backend.app.quant.multi_asset import (
    dual_momentum_weights,
    fixed_weights,
    gtaa_aggressive_weights,
    gtaa_weights,
    inverse_vol_weights,
    patch_bad_closes,
    period_metrics,
    return_agreement,
    sma_filter,
    splice,
    turnover,
    yearly_dates,
)

ETF_DIR = Path(__file__).resolve().parent.parent / "data" / "raw_etf"


def month_frame(cols: dict[str, list[float]]) -> pd.DataFrame:
    """One close per month-end, starting Jan 2020."""
    n = len(next(iter(cols.values())))
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    return pd.DataFrame(cols, index=idx, dtype=float)


# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------


def test_sma_filter_needs_ten_months_and_compares_with_mean_including_today():
    up = list(range(1, 13))  # 1..12
    down = list(range(12, 0, -1))
    m = month_frame({"A": up, "B": down})
    f = sma_filter(m, m.index)
    # Months 1-9 have no 10-month SMA: never held.
    assert not f.iloc[:9].any().any()
    # Month 10: A=10 > mean(1..10)=5.5; B=3 < mean(12..3)=7.5.
    assert f["A"].iloc[9] and not f["B"].iloc[9]
    assert f["A"].iloc[9:].all() and not f["B"].iloc[9:].any()


def test_gtaa_moves_each_failing_asset_slice_to_cash():
    up, down = list(range(1, 13)), list(range(12, 0, -1))
    m = month_frame({"A": up, "B": up, "C": down, "D": down, "E": down, "SHY": up})
    w = gtaa_weights(m, m.index, ["A", "B", "C", "D", "E"], cash="SHY")
    last = w.iloc[-1]
    assert last["A"] == pytest.approx(0.2) and last["B"] == pytest.approx(0.2)
    assert last[["C", "D", "E"]].sum() == 0
    assert last["SHY"] == pytest.approx(0.6)
    # Before the SMA exists everything sits in cash.
    assert w.iloc[0]["SHY"] == pytest.approx(1.0)
    assert np.allclose(w.sum(axis=1), 1.0)


def test_gtaa_aggressive_top3_by_mean_of_1_3_6_12m_returns_with_sma_gate():
    n = 14
    # Steady monthly growth rates; F is ranked 2nd but crashes in the last
    # month below its SMA, so its third goes to SHY.
    g = {"A": 1.05, "B": 1.04, "C": 1.03, "D": 1.02, "E": 1.01, "F": 1.045}
    cols = {k: [v**i for i in range(n)] for k, v in g.items()}
    cols["F"][-1] = 0.5
    cols["G"] = [1.0] * n
    cols["SHY"] = [1.0] * n
    m = month_frame(cols)
    w = gtaa_aggressive_weights(m, m.index, list("ABCDEFG"), cash="SHY")
    last = w.iloc[-1]
    # Scores at the last month: A > B > F? F's 1-month return is -50%, so
    # its mean score drops below C. Top 3 = A, B, C, all above SMA.
    assert last[["A", "B", "C"]].tolist() == pytest.approx([1 / 3] * 3)
    assert last[["D", "E", "F", "G", "SHY"]].sum() == pytest.approx(0)
    prev = w.iloc[-2]
    # One month earlier F ranks 2nd (A, F, B) and is above its SMA.
    assert prev[["A", "F", "B"]].tolist() == pytest.approx([1 / 3] * 3)
    # No 12-month return before month 13: all cash.
    assert w.iloc[11]["SHY"] == pytest.approx(1.0)


def test_gtaa_aggressive_top_asset_below_sma_goes_to_cash():
    n = 14
    cols = {
        "B": [1.03**i for i in range(n)],
        "C": [1.02**i for i in range(n)],
        "D": [1.01**i for i in range(n)],
        "SHY": [1.0] * n,
    }
    cols["A"] = [1.0] * 4 + [3.0] * 9 + [2.0]
    m = month_frame(cols)
    w = gtaa_aggressive_weights(m, m.index, ["A", "B", "C", "D"], cash="SHY")
    last = w.iloc[-1]
    # A: 1m -33%, 3m -33%, 6m -33%, 12m +100% -> mean +0.0%; B mean > C > D.
    # Rank: B, C, D (A's mean 0.0 < D's ~5.7%). All above SMA.
    assert last[["B", "C", "D"]].tolist() == pytest.approx([1 / 3] * 3)
    cols["A"] = [1.0] * 4 + [6.0] * 9 + [5.0]
    m = month_frame(cols)
    w = gtaa_aggressive_weights(m, m.index, ["A", "B", "C", "D"], cash="SHY")
    last = w.iloc[-1]
    # A now ranks 1st (12m +400%) but 5 < SMA 5.9: its third goes to SHY.
    assert last["A"] == 0 and last["SHY"] == pytest.approx(1 / 3)
    assert last[["B", "C"]].tolist() == pytest.approx([1 / 3] * 2)


def test_dual_momentum_three_branches():
    n = 14
    shy = [1.01**i for i in range(n)]
    cases = {
        # SPY beats SHY and EFA -> SPY
        "SPY": ([1.03**i for i in range(n)], [1.02**i for i in range(n)]),
        # SPY beats SHY, EFA beats SPY -> EFA
        "EFA": ([1.02**i for i in range(n)], [1.03**i for i in range(n)]),
        # SPY below SHY (even if EFA is strong) -> IEF
        "IEF": ([1.0] * n, [1.05**i for i in range(n)]),
    }
    for expected, (spy, efa) in cases.items():
        m = month_frame({"SPY": spy, "EFA": efa, "IEF": [1.0] * n, "SHY": shy})
        w = dual_momentum_weights(m, m.index)
        assert w.iloc[-1][expected] == 1.0
        assert w.iloc[-1].sum() == 1.0
    # Before 12 months exist it holds the bond fund (no signal = risk-off).
    assert w.iloc[0]["IEF"] == 1.0


def daily_frame(n: int, vols: dict[str, float], seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    data = {k: 100 * np.cumprod(1 + rng.normal(0.0005, v, n)) for k, v in vols.items()}
    return pd.DataFrame(data, index=idx)


def test_inverse_vol_weights_are_proportional_to_one_over_60d_std():
    closes = daily_frame(300, {"A": 0.01, "B": 0.02, "C": 0.005})
    closes["SHY"] = 100.0
    dates = month_end_dates(closes.index)
    w = inverse_vol_weights(closes, dates, ["A", "B", "C"])
    d = dates[-1]
    sd = closes[["A", "B", "C"]].pct_change().loc[:d].iloc[-60:].std()
    expected = (1 / sd) / (1 / sd).sum()
    assert w.loc[d, ["A", "B", "C"]].to_numpy() == pytest.approx(expected.to_numpy())
    assert w.loc[d].sum() == pytest.approx(1.0)
    # First month-end has fewer than 60 returns: all cash.
    assert w.iloc[0]["SHY"] == pytest.approx(1.0)


def test_inverse_vol_sma_variant_sends_failing_weight_to_cash():
    closes = daily_frame(300, {"A": 0.01, "B": 0.02})
    t = np.arange(len(closes))
    closes["A"] = 100 * np.exp(0.002 * t + 0.01 * np.sin(t))  # always above SMA
    closes["SHY"] = 100.0
    closes.loc[closes.index >= closes.index[-25], "B"] = (
        closes["B"].iloc[:-25].min() * 0.5
    )
    dates = month_end_dates(closes.index)
    plain = inverse_vol_weights(closes, dates, ["A", "B"])
    gated = inverse_vol_weights(closes, dates, ["A", "B"], sma_months=10)
    d = dates[-1]
    assert gated.loc[d, "B"] == 0
    assert gated.loc[d, "SHY"] == pytest.approx(plain.loc[d, "B"])
    assert gated.loc[d, "A"] == pytest.approx(plain.loc[d, "A"])


def test_fixed_weights_and_yearly_dates():
    idx = pd.bdate_range("2019-11-01", "2021-12-31")
    dates = month_end_dates(idx)
    yd = yearly_dates(dates, start=pd.Timestamp("2020-03-31"))
    assert list(yd.strftime("%Y-%m-%d")) == ["2020-03-31", "2020-12-31"]
    w = fixed_weights(yd, {"SPY": 0.25, "TLT": 0.25, "GLD": 0.25, "SHY": 0.25})
    assert w.shape == (2, 4) and np.allclose(w.sum(axis=1), 1.0)


# --------------------------------------------------------------------------
# Turnover and metrics
# --------------------------------------------------------------------------


def test_turnover_counts_buys_plus_sells_after_drift():
    idx = pd.bdate_range("2020-01-01", periods=6)
    opens = pd.DataFrame({"A": [1, 1, 1, 2, 2, 2], "B": [1, 1, 1, 1, 1, 1]}, idx)
    w = pd.DataFrame(
        {"A": [0.5, 0.5], "B": [0.5, 0.5]}, index=[idx[0], idx[3]], dtype=float
    )
    t = turnover(opens.astype(float), w)
    # From cash: buy 100%. Then A doubles -> 2/3 vs 1/3; back to 1/2: |1/6|*2.
    assert t.index.tolist() == [idx[1], idx[4]]
    assert t.tolist() == pytest.approx([1.0, 1 / 3])


def test_turnover_matches_cost_charged_by_run_rebalanced():
    closes = daily_frame(400, {"A": 0.01, "B": 0.02, "C": 0.015}, seed=3)
    opens = closes.shift(1).fillna(100.0) * 1.001
    dates = month_end_dates(closes.index)
    rng = np.random.default_rng(1)
    w = pd.DataFrame(rng.dirichlet([1, 1, 1], len(dates)), dates, closes.columns)
    cost = 0.0002
    r0 = run_rebalanced(opens, closes, w, 0.0)
    r1 = run_rebalanced(opens, closes, w, cost)
    t = turnover(opens, w)
    eq0, eq1 = (1 + r0).prod(), (1 + r1).prod()
    # Each charge removes cost*turnover of equity: eq1 ~ eq0 * prod(1 - c*t).
    assert eq1 == pytest.approx(eq0 * np.prod(1 - cost * t), rel=1e-9)


def test_period_metrics_hand_checked():
    idx = pd.bdate_range("2020-01-01", "2021-12-31")
    r = pd.Series(0.0, idx)
    r[idx == pd.Timestamp("2020-03-02")] = -0.20
    r[idx == pd.Timestamp("2021-06-01")] = 0.50
    turn = pd.Series([1.0, 0.5, 0.5], [idx[0], idx[100], idx[300]])
    m = period_metrics(r, turn)
    years = len(idx) / 252
    assert m["cagr"] == pytest.approx(1.2 ** (1 / years) - 1)
    assert m["max_drawdown"] == pytest.approx(-0.20)
    assert m["calmar"] == pytest.approx(m["cagr"] / 0.20)
    assert m["worst_year"] == pytest.approx(-0.20)
    assert m["worst_year_label"] == "2020"
    # 24 months, 1 up (Jun 2021), 1 down, 22 flat.
    assert m["pct_positive_months"] == pytest.approx(1 / 24)
    assert m["annual_turnover"] == pytest.approx(2.0 / years)


# --------------------------------------------------------------------------
# Data: splice and source agreement
# --------------------------------------------------------------------------


def ohlc(closes: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes}
    )


def test_splice_rescales_early_source_and_keeps_late_bars():
    idx = pd.bdate_range("2015-12-28", periods=8)
    early = ohlc(pd.Series([10, 11, 12, 13, 14, 15, 16, 17], idx, dtype=float))
    late = ohlc(pd.Series([26, 28, 30, 32], idx[4:], dtype=float) * 1.0)
    s = splice(early, late)
    assert (s.loc[s.index >= idx[4]] == late).all().all()
    # Early bars scaled by 26/14 so returns across the seam are the early
    # source's returns.
    assert s["close"].iloc[3] == pytest.approx(13 * 26 / 14)
    assert s["close"].pct_change().iloc[4] == pytest.approx(14 / 13 - 1)


def test_return_agreement_flags_unadjusted_distribution():
    idx = pd.bdate_range("2020-01-01", periods=300)
    a = daily_frame(300, {"X": 0.01}, seed=5)["X"]
    b = a.copy()
    b.loc[: idx[150]] *= 1.05  # vendor misses a 5% distribution at idx[151]
    corr, bad = return_agreement(a, b, tol=0.01)
    assert bad.index.tolist() == [idx[151]]
    assert corr > 0.9


def test_patch_bad_closes_repairs_one_day_prints_but_not_level_shifts():
    idx = pd.bdate_range("2020-03-02", periods=10)
    y = ohlc(pd.Series([100, 101, 90, 99, 98, 97, 96, 95, 94, 93], idx, dtype=float))
    a = y * 2.0
    a.loc[idx[2], "close"] = 2.0 * 95  # bad print, reverts next session
    a.loc[a.index >= idx[6], "close"] *= 1.014  # level shift: an adjustment difference
    fixed, days = patch_bad_closes(a, y, tol=0.01)
    assert days.tolist() == [idx[2]]
    assert fixed.loc[idx[2], "close"] == pytest.approx(180.0)
    assert fixed.loc[idx[2], "open"] == a.loc[idx[2], "open"]
    assert (fixed.loc[fixed.index != idx[2]] == a.loc[a.index != idx[2]]).all().all()


@pytest.mark.parametrize(
    "sym", ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "SHY", "GLD", "DBC", "VNQ"]
)
def test_real_sources_agree_where_they_overlap(sym):
    """Alpaca SIP (2016+) vs yfinance auto-adjusted daily returns, after the
    one-day bad Alpaca closes are repaired: correlation must be > 0.99."""
    alp, yfp = ETF_DIR / f"{sym}.parquet", ETF_DIR / "yf" / f"{sym}.parquet"
    if not (alp.exists() and yfp.exists()):
        pytest.skip("ETF cache absent; run scripts/run_multi_asset.py")
    a, y = pd.read_parquet(alp), pd.read_parquet(yfp)
    fixed, days = patch_bad_closes(a, y)
    corr, bad = return_agreement(fixed["close"], y["close"])
    assert corr > 0.99
    assert len(days) <= 5  # repairs stay rare
    # Nothing left that looks like a spliced or unadjusted bar.
    assert (bad[["a", "b"]].abs() < 0.40).all().all()
