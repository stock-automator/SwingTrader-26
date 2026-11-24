"""Cycle 06 backtests 1 (SPY intraday momentum) and 3 (sector-ETF momentum).

Synthetic data only: no network, no `data/raw/`, cache files go to `tmp_path`.
Every expected number is worked out by hand from the pre-registered rules in
docs/research/strategy-evidence-2026-11.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.etf_strategies import (
    abs_momentum_weights,
    break_even_cost,
    cached_frame,
    equal_weights,
    intraday_positions,
    last_complete_end,
    month_end_dates,
    perf_metrics,
    run_rebalanced,
    session_legs,
    strategy_returns,
    top_n_weights,
)

NY = "America/New_York"


# --------------------------------------------------------------------------
# 1. SPY intraday momentum
# --------------------------------------------------------------------------


def day_bars(day: str, close_hm: str, prices: dict[str, tuple[float, float]]):
    """30-minute bars 04:00..19:30 NY at 100, with `prices` overriding
    (open, close) of the bars starting at the given HH:MM."""
    idx = pd.date_range(f"{day} 04:00", f"{day} 19:30", freq="30min", tz=NY)
    df = pd.DataFrame({"open": 100.0, "close": 100.0}, index=idx)
    for hm, (o, c) in prices.items():
        df.loc[pd.Timestamp(f"{day} {hm}", tz=NY), ["open", "close"]] = [o, c]
    return df


def calendar(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {"close": [pd.Timestamp(f"{d} {c}") for d, c in rows]},
        index=pd.DatetimeIndex([pd.Timestamp(d) for d, _ in rows]),
    )


def test_session_legs_signal_trade_bar_and_half_day():
    bars = pd.concat(
        [
            # Day 1: last regular bar 15:30 closes at 100; the after-hours
            # 16:00 bar (closing auction / extended) must not be the close.
            day_bars("2024-11-27", "16:00", {"15:30": (99.0, 100.0), "16:00": (1, 1)}),
            # Day 2 is a half day closing 13:00: 10:00 price is the 09:30 bar
            # close; the trade bar is 12:30-13:00, not 15:30.
            day_bars(
                "2024-11-29",
                "13:00",
                {"09:30": (100.0, 102.0), "12:30": (101.0, 103.02), "15:30": (5, 5)},
            ),
            # Day 3: regular day after the half day; prev close = 103.02.
            day_bars(
                "2024-12-02",
                "16:00",
                {"09:30": (103.0, 100.0), "15:30": (100.0, 99.0)},
            ),
        ]
    )
    cal = calendar(
        [("2024-11-27", "16:00"), ("2024-11-29", "13:00"), ("2024-12-02", "16:00")]
    )
    legs = session_legs(bars, cal)
    # Day 1 has no previous close, so it is dropped.
    assert list(legs.index) == [pd.Timestamp("2024-11-29"), pd.Timestamp("2024-12-02")]
    d2, d3 = legs.iloc[0], legs.iloc[1]
    assert d2["prev_close"] == pytest.approx(100.0)
    assert d2["signal"] == pytest.approx(0.02)
    assert d2["r_last"] == pytest.approx(0.02)
    assert bool(d2["early_close"]) is True
    assert d3["prev_close"] == pytest.approx(103.02)
    assert d3["signal"] == pytest.approx(100.0 / 103.02 - 1)
    assert d3["r_last"] == pytest.approx(-0.01)
    assert bool(d3["early_close"]) is False


def test_session_legs_drops_day_with_missing_bar():
    b1 = day_bars("2024-03-04", "16:00", {})
    b2 = day_bars("2024-03-05", "16:00", {}).drop(
        pd.Timestamp("2024-03-05 09:30", tz=NY)
    )
    b3 = day_bars("2024-03-06", "16:00", {"09:30": (100, 101)})
    cal = calendar([(d, "16:00") for d in ("2024-03-04", "2024-03-05", "2024-03-06")])
    legs = session_legs(pd.concat([b1, b2, b3]), cal)
    assert list(legs.index) == [pd.Timestamp("2024-03-06")]
    # The previous close still comes from 03-05's 15:30 bar.
    assert legs["signal"].iloc[0] == pytest.approx(0.01)


def test_positions_every_day_and_long_only():
    sig = pd.Series([0.01, -0.02, 0.0, 0.003])
    assert intraday_positions(sig).tolist() == [1, -1, 0, 1]
    assert intraday_positions(sig, long_only=True).tolist() == [1, 0, 0, 1]


def test_pdt_threshold_uses_only_previous_days():
    # Previous 4 |signals| = 1,2,3,4 -> 80th pct = 3.4, so 3.5 trades. If
    # today were in the window ([2,3,4,3.5] -> 3.7) it would not.
    sig = pd.Series([1.0, 2.0, 3.0, 4.0, 3.5])
    pos = intraday_positions(sig, pdt=True, window=4)
    assert pos.tolist() == [0, 0, 0, 0, 1]
    # Below the threshold: no trade.
    sig = pd.Series([5.0, 5.0, 5.0, 5.0, -4.9])
    assert intraday_positions(sig, pdt=True, window=4).tolist() == [0] * 5
    # At the threshold counts ("at or above"); sign kept for shorts.
    sig = pd.Series([5.0, 5.0, 5.0, 5.0, -5.0])
    assert intraday_positions(sig, pdt=True, window=4).tolist() == [0, 0, 0, 0, -1]


def test_pdt_cap_three_trades_in_any_five_sessions():
    # Strictly rising |signal|: every day from index 4 clears the threshold.
    sig = pd.Series(np.arange(1.0, 17.0))
    pos = intraday_positions(sig, pdt=True, window=4)
    taken = pos.iloc[4:].tolist()
    assert taken == [1, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1]
    rolling = pos.abs().rolling(5).sum().max()
    assert rolling <= 3


def test_pdt_cap_counts_only_trades_taken_long_only():
    # Alternating signs, all above threshold. Long-only drops the shorts,
    # so the cap never binds on the longs.
    vals = np.arange(1.0, 17.0) * np.where(np.arange(16) % 2 == 0, 1, -1)
    pos = intraday_positions(pd.Series(vals), pdt=True, long_only=True, window=4)
    assert pos.iloc[4:].tolist() == [1, 0] * 6


def test_strategy_returns_apply_round_trip_cost():
    pos = pd.Series([1, -1, 0])
    r_last = pd.Series([0.002, 0.003, 0.01])
    out = strategy_returns(pos, r_last, cost=0.0001)
    assert out.tolist() == pytest.approx([0.0018, -0.0032, 0.0])


def test_break_even_cost_is_half_mean_gross_trade():
    assert break_even_cost(pd.Series([0.0006, 0.0002])) == pytest.approx(0.0002)


def test_perf_metrics_hand_computed():
    idx = pd.to_datetime(["2023-12-28", "2023-12-29", "2024-01-02"])
    daily = pd.Series([0.1, -0.5, 0.2], index=idx)
    m = perf_metrics(daily, trades=pd.Series([0.1, -0.5, 0.2]))
    assert m["total_return"] == pytest.approx(1.1 * 0.5 * 1.2 - 1)
    assert m["max_drawdown"] == pytest.approx(-0.5)
    assert m["cagr"] == pytest.approx((1.1 * 0.5 * 1.2) ** (252 / 3) - 1)
    r = daily.to_numpy()
    assert m["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * np.sqrt(252))
    assert m["hit_rate"] == pytest.approx(2 / 3)
    assert m["avg_bp"] == pytest.approx((0.1 - 0.5 + 0.2) / 3 * 1e4)
    assert m["trades"] == 3
    # 2023: 1.1*0.5 - 1 < 0; 2024: +0.2 > 0.
    assert m["pct_positive_years"] == pytest.approx(0.5)


def test_perf_metrics_drawdown_counts_from_starting_equity():
    daily = pd.Series([-0.1, 0.05], index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    m = perf_metrics(daily)
    assert m["max_drawdown"] == pytest.approx(-0.1)
    assert m["trades"] is None


# --------------------------------------------------------------------------
# 3. Sector-ETF momentum
# --------------------------------------------------------------------------


def test_month_end_dates_excludes_unfinished_last_month():
    idx = pd.to_datetime(
        [
            "2024-01-30",
            "2024-01-31",
            "2024-02-01",
            "2024-02-29",
            "2024-03-01",
            "2024-03-04",
        ]
    )
    assert list(month_end_dates(idx)) == list(
        pd.to_datetime(["2024-01-31", "2024-02-29"])
    )


def frame(cols: dict[str, list[float]], start: str = "2024-01-29") -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    return pd.DataFrame(cols, index=pd.bdate_range(start, periods=n))


def test_top_n_weights_ranks_lookback_return_and_skips_short_history():
    closes = frame(
        {
            "A": [100, 100, 130],  # +30%
            "B": [100, 100, 120],  # +20%
            "C": [100, 100, 110],  # +10%
            "D": [np.nan, 100, 200],  # +100% but only 2 bars: not eligible
        }
    )
    w = top_n_weights(closes, closes.index[[2]], lookback=2, n=2)
    assert w.loc[closes.index[2]].to_dict() == {"A": 0.5, "B": 0.5, "C": 0.0, "D": 0.0}


def test_abs_momentum_weights_positive_only_else_cash():
    closes = frame({"A": [100, 100, 101], "B": [100, 100, 99], "C": [100, 100, 102]})
    w = abs_momentum_weights(closes, closes.index[[2]], lookback=2)
    assert w.iloc[0].to_dict() == {"A": 0.5, "B": 0.0, "C": 0.5}
    down = frame({"A": [100, 100, 90], "B": [100, 100, 100]})
    w = abs_momentum_weights(down, down.index[[2]], lookback=2)
    assert w.iloc[0].sum() == 0.0  # not above 0 -> cash


def test_equal_weights_includes_only_listed_sectors():
    closes = frame({"A": [1.0, 1.0], "B": [np.nan, 1.0]})
    w = equal_weights(closes, closes.index)
    assert w.iloc[0].to_dict() == {"A": 1.0, "B": 0.0}
    assert w.iloc[1].to_dict() == {"A": 0.5, "B": 0.5}


def test_run_rebalanced_trades_next_open_with_costs_and_drift():
    #        d0     d1     d2     d3
    # A open  -    110    100    100   close 100, 121, 110, 110
    # B open  -    100    100     50   close 100, 100, 100, 100
    idx = pd.bdate_range("2024-01-29", periods=4)
    opens = pd.DataFrame(
        {"A": [100, 110, 100, 100], "B": [100, 100, 100, 50]}, index=idx
    )
    closes = pd.DataFrame(
        {"A": [100, 121, 110, 110], "B": [100, 100, 100, 100]}, index=idx
    )
    weights = pd.DataFrame({"A": [1.0, 0.5], "B": [0.0, 0.5]}, index=idx[[0, 2]])
    c = 0.001
    r = run_rebalanced(opens, closes, weights, cost=c)
    # d0: decision only, still cash.
    assert r.iloc[0] == 0.0
    # d1: buy A at the 110 open (the 100 -> 110 gap is missed), pay c on 1.0.
    v1 = (1 - c) * 121 / 110
    assert r.iloc[1] == pytest.approx(v1 - 1)
    # d2: held A close-to-close.
    v2 = v1 * 110 / 121
    assert r.iloc[2] == pytest.approx(v2 / v1 - 1)
    # d3: open at A 100, rebalance to 50/50, turnover |0.5V - V| + 0.5V = V.
    v_open = v2 * 100 / 110
    v_after = v_open * (1 - c)
    v3 = v_after * 0.5 * 110 / 100 + v_after * 0.5 * 100 / 50
    assert r.iloc[3] == pytest.approx(v3 / v2 - 1)


def test_run_rebalanced_weights_drift_within_month():
    idx = pd.bdate_range("2024-01-29", periods=3)
    opens = pd.DataFrame({"A": [1.0, 1.0, 2.0], "B": [1.0, 1.0, 1.0]}, index=idx)
    closes = pd.DataFrame({"A": [1.0, 2.0, 4.0], "B": [1.0, 1.0, 1.0]}, index=idx)
    weights = pd.DataFrame({"A": [0.5], "B": [0.5]}, index=idx[[0]])
    r = run_rebalanced(opens, closes, weights, cost=0.0)
    eq = (1 + r).cumprod()
    # Buy-and-hold inside the month: 0.5*4 + 0.5*1, not daily rebalanced.
    assert eq.iloc[-1] == pytest.approx(2.5)


# --------------------------------------------------------------------------
# Caches and data hygiene
# --------------------------------------------------------------------------


def test_cached_frame_fetches_once_then_reads_cache(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return pd.DataFrame({"x": [1.0]}, index=pd.to_datetime(["2024-01-02"]))

    path = tmp_path / "raw_etf" / "XLK.parquet"
    a = cached_frame(path, fetch)
    b = cached_frame(path, fetch)
    assert path.exists() and len(calls) == 1
    pd.testing.assert_frame_equal(a, b, check_freq=False)
    cached_frame(path, fetch, refresh=True)
    assert len(calls) == 2


def test_cached_frame_does_not_write_empty_fetch(tmp_path):
    path = tmp_path / "SPY.parquet"
    with pytest.raises(ValueError):
        cached_frame(path, lambda: pd.DataFrame())
    assert not path.exists()


def test_last_complete_end_never_includes_today():
    now = pd.Timestamp("2026-11-24 12:43", tz=NY)  # mid-session
    assert last_complete_end(now) == pd.Timestamp("2026-11-24 00:00", tz=NY)
    # A London evening is still the same NY day.
    london = pd.Timestamp("2026-11-24 23:30", tz="Europe/London")
    assert last_complete_end(london) == pd.Timestamp("2026-11-24 00:00", tz=NY)
