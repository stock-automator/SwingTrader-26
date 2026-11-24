"""Ranked cross-sectional momentum backtest (cycle 04)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.app.quant import momentum
from backend.app.quant.momentum import (
    MomentumConfig,
    build_panel,
    choose_variant,
    eligibility,
    equal_weight_selector,
    market_ok,
    momentum_scores,
    momentum_selector,
    month_end_mask,
    run_stats,
    select_holdings,
    simulate,
    spy_buy_hold,
)

ROOT = Path(__file__).resolve().parent.parent


def _frame(close, index, open_=None, volume=1e7) -> pd.DataFrame:
    close = pd.Series(close, index=index, dtype=float)
    open_ = close if open_ is None else pd.Series(open_, index=index, dtype=float)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close),
            "Low": np.minimum(open_, close),
            "Close": close,
            "Volume": float(volume),
        },
        index=index,
    )


def _calendar(n: int, start: str = "2020-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


SMALL = dict(lookback=10, skip=2, adv_days=3, sma_days=5, min_dollar_volume=1e6)


# --- calendar --------------------------------------------------------------


def test_month_end_mask_marks_last_trading_day_and_drops_partial_last_month():
    idx = pd.DatetimeIndex(["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-28"])
    idx = idx.append(pd.DatetimeIndex(["2020-03-02", "2020-03-03"]))
    mask = month_end_mask(idx)
    assert mask.tolist() == [False, True, False, True, False, False]


# --- ranking and eligibility ------------------------------------------------


def test_momentum_score_skips_recent_bars_and_needs_full_lookback():
    idx = _calendar(15)
    close = pd.DataFrame({"A": np.arange(1.0, 16.0)}, index=idx)
    scores = momentum_scores(close, close.ffill(), lookback=10, skip=2)
    # Bar 12: close[10] / close[2] - 1 = 11 / 3 - 1.
    assert scores["A"].iloc[12] == pytest.approx(11 / 3 - 1)
    assert scores["A"].iloc[:10].isna().all()
    assert scores["A"].iloc[10] == pytest.approx(9 / 1 - 1)


def test_momentum_score_is_nan_without_a_bar_on_the_rank_day():
    idx = _calendar(15)
    close = pd.DataFrame({"A": np.arange(1.0, 16.0)}, index=idx)
    close.iloc[12, 0] = np.nan
    scores = momentum_scores(close, close.ffill(), lookback=10, skip=2)
    assert np.isnan(scores["A"].iloc[12])


def test_eligibility_applies_price_and_dollar_volume_floors():
    idx = _calendar(20)
    frames = {
        "OK": _frame(np.linspace(10, 20, 20), idx, volume=1e6),
        "CHEAP": _frame(np.linspace(3, 4.9, 20), idx, volume=1e8),
        "THIN": _frame(np.linspace(10, 20, 20), idx, volume=1e3),
    }
    panel = build_panel(frames, _frame(np.full(20, 100.0), idx))
    cfg = MomentumConfig(**SMALL)
    ok = eligibility(panel, cfg)
    assert bool(ok["OK"].iloc[-1])
    assert not ok["CHEAP"].iloc[-1]
    assert not ok["THIN"].iloc[-1]
    assert not ok["OK"].iloc[9]  # not enough history for the lookback


def test_select_holdings_keeps_held_names_within_buffer():
    scores = pd.Series({t: float(10 - i) for i, t in enumerate("ABCDEFGHIJ")})
    ok = pd.Series(True, index=scores.index)
    # N=2: top-2 = A, B; buffer top-4 = A..D. H is held but rank 8 -> dropped.
    assert select_holdings(scores, ok, {"D", "H"}, top_n=2, buffer_mult=2) == [
        "D",
        "A",
    ]
    assert select_holdings(scores, ok, set(), top_n=2, buffer_mult=2) == ["A", "B"]


def test_select_holdings_ignores_ineligible_names():
    scores = pd.Series({"A": 3.0, "B": 2.0, "C": 1.0})
    ok = pd.Series({"A": False, "B": True, "C": True})
    assert select_holdings(scores, ok, {"A"}, top_n=1, buffer_mult=2) == ["B"]


def test_market_ok_is_false_below_sma_and_without_history():
    spy = pd.Series([10.0, 10, 10, 10, 12, 8], index=_calendar(6))
    ok = market_ok(spy, sma_days=3)
    assert ok.tolist() == [False, False, True, True, True, False]


# --- panel ------------------------------------------------------------------


def test_build_panel_excludes_spliced_tickers_with_the_portfolio_filter(monkeypatch):
    idx = _calendar(20)
    spliced = np.full(20, 100.0)
    spliced[10] = 5.0  # 100 -> 5 -> 100: a spliced bar
    frames = {"GOOD": _frame(np.full(20, 50.0), idx), "BAD": _frame(spliced, idx)}
    calls = []
    real = momentum.suspect_spike

    def spy_on(close):
        calls.append(close.name)
        return real(close)

    monkeypatch.setattr(momentum, "suspect_spike", spy_on)
    panel = build_panel(frames, _frame(np.full(20, 100.0), idx))
    assert panel.tickers == ["GOOD"]
    assert panel.suspect == ["BAD"]
    assert len(calls) == 2
    from backend.app.quant import portfolio

    assert real is portfolio.suspect_spike


def test_build_panel_uses_spy_calendar_and_leaves_missing_bars_nan():
    idx = _calendar(30)
    late = _frame(np.full(25, 20.0), idx[5:])
    panel = build_panel({"LATE": late}, _frame(np.full(30, 100.0), idx))
    assert list(panel.index) == list(idx)
    assert panel.close["LATE"].iloc[:5].isna().all()
    assert panel.adv["LATE"].iloc[-1] == pytest.approx(20.0 * 1e7)


# --- simulation -------------------------------------------------------------


def _two_stock_panel(n: int = 40):
    idx = _calendar(n, "2020-01-01")
    up = np.linspace(10, 30, n)
    down = np.linspace(30, 10, n)
    frames = {
        "UP": _frame(up, idx, open_=up * 1.01),
        "DOWN": _frame(down, idx, open_=down * 1.01),
    }
    return build_panel(frames, _frame(np.linspace(100, 120, n), idx)), idx


def test_simulate_trades_at_next_open_with_slippage_and_fractional_shares():
    panel, idx = _two_stock_panel()
    cfg = MomentumConfig(top_n=1, **SMALL)
    res = simulate(panel, momentum_selector(panel, cfg), idx[0], idx[-1])
    month_end = idx[month_end_mask(idx)][0]  # 2020-01-31
    trades = res.trades
    first = trades.iloc[0]
    assert first["ticker"] == "UP"
    assert first["date"] == idx[idx.get_loc(month_end) + 1]
    exec_open = panel.open.loc[first["date"], "UP"]
    assert first["price"] == pytest.approx(exec_open * 1.0005)
    assert first["cost"] == pytest.approx(first["shares"] * exec_open * 0.0005)
    assert first["shares"] != round(first["shares"])
    assert (res.cash >= -1e-9).all()
    # Before the first execution the account is all cash.
    assert res.equity.loc[:month_end].eq(10_000.0).all()


def test_simulate_starts_from_prior_month_end_signal_and_prepends_start_point():
    panel, idx = _two_stock_panel(60)
    cfg = MomentumConfig(top_n=1, **SMALL)
    start = pd.Timestamp("2020-02-03")  # day after the 2020-01-31 month end
    res = simulate(panel, momentum_selector(panel, cfg), start, idx[-1])
    assert res.equity.index[0] == pd.Timestamp("2020-01-31")
    assert res.equity.iloc[0] == 10_000.0
    assert res.trades.iloc[0]["date"] == start


def test_simulate_exits_delisted_holding_at_last_close():
    n = 40
    idx = _calendar(n)
    up = np.linspace(10, 30, n)
    gone = _frame(up[:30] * 2, idx[:30])  # best momentum, then no bars
    frames = {"GONE": gone, "SLOW": _frame(np.linspace(10, 11, n), idx)}
    panel = build_panel(frames, _frame(np.linspace(100, 120, n), idx))
    cfg = MomentumConfig(top_n=1, **SMALL)
    res = simulate(panel, momentum_selector(panel, cfg), idx[0], idx[-1])
    exits = res.trades[res.trades["reason"] == momentum.DELISTED]
    assert len(exits) == 1
    exit_ = exits.iloc[0]
    assert exit_["ticker"] == "GONE"
    assert exit_["date"] == idx[30]
    assert exit_["price"] == pytest.approx(gone["Close"].iloc[-1] * (1 - 0.0005))
    assert res.delisted_exits == 1
    after = idx[30]
    assert res.holdings.loc[after:].eq(0).all()


def test_simulate_market_filter_holds_cash():
    panel, idx = _two_stock_panel()
    panel.spy_close[:] = np.linspace(120, 100, len(idx))  # falling SPY
    cfg = MomentumConfig(top_n=1, market_filter=True, **SMALL)
    res = simulate(panel, momentum_selector(panel, cfg), idx[0], idx[-1])
    assert res.trades.empty
    assert res.equity.eq(10_000.0).all()


def test_selection_uses_no_data_after_the_rebalance_close():
    panel, idx = _two_stock_panel()
    cfg = MomentumConfig(top_n=1, **SMALL)
    base = simulate(panel, momentum_selector(panel, cfg), idx[0], idx[-1])
    month_end = idx[month_end_mask(idx)][0]
    k = idx.get_loc(month_end)
    # Make DOWN explode from the execution day on: selection must not see it.
    later, col = k + 1, panel.tickers.index("DOWN")
    panel.close.iloc[later:, col] *= 10
    panel.mark.iloc[later:, col] *= 10
    changed = simulate(panel, momentum_selector(panel, cfg), idx[0], idx[-1])
    assert base.trades.iloc[0]["ticker"] == changed.trades.iloc[0]["ticker"] == "UP"


def test_equal_weight_selector_holds_every_ticker_with_a_bar():
    panel, idx = _two_stock_panel()
    res = simulate(panel, equal_weight_selector(panel), idx[0], idx[-1])
    first_day = res.trades["date"].min()
    buys = res.trades[res.trades["date"] == first_day]
    assert sorted(buys["ticker"]) == ["DOWN", "UP"]
    notional = buys["shares"] * buys["price"]
    assert notional.iloc[0] == pytest.approx(notional.iloc[1])
    assert res.holdings.iloc[-1] == 2


# --- stats, benchmarks, selection -------------------------------------------


def test_run_stats_reports_every_metric():
    panel, idx = _two_stock_panel(70)
    cfg = MomentumConfig(top_n=1, **SMALL)
    res = simulate(panel, momentum_selector(panel, cfg), idx[0], idx[-1])
    stats = run_stats(res)
    for key in (
        "cagr_pct",
        "total_return_pct",
        "max_drawdown_pct",
        "sharpe",
        "positive_years_pct",
        "monthly_turnover_pct",
        "avg_holdings",
        "exposure_pct",
        "avg_invested_pct",
        "delisted_exits",
        "rebalances",
    ):
        assert key in stats
    assert stats["total_return_pct"] == pytest.approx(
        (res.equity.iloc[-1] / 10_000 - 1) * 100
    )
    # First rebalance buys 100% from cash: one-sided turnover 100%.
    assert res.turnover.iloc[0] == pytest.approx(1.0, rel=1e-3)
    assert 0 < stats["avg_holdings"] <= 1
    assert 0 < stats["exposure_pct"] < 100


def test_spy_buy_hold_starts_at_prior_close():
    idx = _calendar(5)
    spy = pd.Series([100.0, 110, 121, 110, 132], index=idx)
    eq = spy_buy_hold(spy, idx[1], idx[-1], 10_000.0)
    assert eq.index[0] == idx[0]
    assert eq.iloc[0] == 10_000.0
    assert eq.iloc[-1] == pytest.approx(13_200.0)


def test_choose_variant_uses_build_cagr_over_drawdown():
    build = {
        "a": {"cagr_pct": 20.0, "max_drawdown_pct": -40.0},  # 0.5
        "b": {"cagr_pct": 12.0, "max_drawdown_pct": -15.0},  # 0.8
        "c": {"cagr_pct": -5.0, "max_drawdown_pct": -10.0},
    }
    assert choose_variant(build) == "b"


def test_variants_are_the_eight_approved_combinations():
    names = {v.name for v in momentum.VARIANTS}
    assert len(momentum.VARIANTS) == 8 == len(names)
    assert {(v.lookback, v.top_n, v.market_filter) for v in momentum.VARIANTS} == {
        (lb, n, f) for lb in (252, 126) for n in (10, 20) for f in (False, True)
    }
    v = momentum.VARIANTS[0]
    assert (v.skip, v.buffer_mult, v.min_price, v.min_dollar_volume) == (
        21,
        2,
        5.0,
        20_000_000.0,
    )


# --- script -----------------------------------------------------------------


def test_run_momentum_script_writes_doc_and_json(tmp_path, monkeypatch):
    """Point-in-time run (cycle 04b): members only, removed names from the
    pit cache, selection on the build period, biased test numbers alongside."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_momentum

    idx = pd.bdate_range("2015-01-01", "2024-03-29")
    rng = np.random.default_rng(0)
    data, pit = tmp_path / "raw", tmp_path / "raw_pit"
    data.mkdir()
    pit.mkdir()
    symbols = [f"T{i}" for i in range(25)]

    def write(sym, i, where, days=idx):
        drift = 0.0002 * (i % 7)
        close = 50 * np.exp(np.cumsum(rng.normal(drift, 0.01, len(days))))
        df = _frame(close, days, volume=1e6)
        df["Adj Close"] = df["Close"]
        df.to_parquet(where / f"{sym}.parquet")

    for i, sym in enumerate(symbols + ["SPY"]):
        write(sym, i, data)
    write("GONE", 6, pit, idx[idx <= "2020-06-15"])  # bought out mid-2020
    months = pd.date_range("2015-12-31", "2024-03-31", freq="ME")
    rows = [(d, f"T{i}") for d in months for i in range(20) if i != 3]
    rows += [(d, "T3") for d in months if d >= pd.Timestamp("2021-01-31")]
    rows += [(d, "GONE") for d in months if d <= pd.Timestamp("2020-06-30")]
    rows += [(d, "NOPRICE") for d in months[:3]]
    membership = tmp_path / "membership.csv"
    pd.DataFrame(rows, columns=["date", "ticker"]).to_csv(
        membership, index=False, date_format="%Y-%m-%d"
    )
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"symbols": symbols + ["MISSING"]}))
    scorecard = tmp_path / "scorecard.json"
    scorecard.write_text(json.dumps({"rows": []}))
    md, js = tmp_path / "momentum.md", tmp_path / "momentum.json"
    run_momentum.main(
        [
            "--data-dir",
            str(data),
            "--pit-dir",
            str(pit),
            "--membership",
            str(membership),
            "--universe",
            str(universe),
            "--scorecard-json",
            str(scorecard),
            "--markdown",
            str(md),
            "--json",
            str(js),
        ]
    )
    out = json.loads(js.read_text())
    variants = {v.name for v in momentum.VARIANTS}
    assert out["chosen"] in variants
    assert out["tickers"] == 21  # T0-T19 and GONE; T20-T24 never members
    assert out["missing_symbols"] == ["NOPRICE"]
    assert out["coverage"]["never_priced"] == ["NOPRICE"]
    assert 90 < out["coverage"]["pct"] < 100
    assert set(out["build"]) >= variants
    assert set(out["test"]) >= variants | {"SPY buy & hold", "Equal-weight universe"}
    # Today's-universe (biased) runs, test period only, for comparison.
    assert set(out["biased_test"]) == variants
    assert out["biased_missing_symbols"] == ["MISSING"]
    # Share of member-months with a full lookback, per lookback.
    assert set(out["coverage"]["rankable_pct"]) == {"12-1", "6-1"}
    assert 0 < out["coverage"]["rankable_pct"]["12-1"] <= 100
    # Today's members without the membership rule: survivorship alone.
    assert set(out["today_members_test"]) == {"stats", "tickers"}
    assert out["today_members_test"]["tickers"] == 20  # T0-T19 incl. T3
    text = md.read_text()
    assert out["chosen"] in text
    assert "point-in-time" in text.lower()
    assert "today's universe" in text.lower()


# --- point-in-time membership (cycle 04b) -----------------------------------


def _members(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "ticker"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_membership_mask_uses_the_snapshot_of_each_days_month():
    idx = pd.DatetimeIndex(["2020-01-30", "2020-01-31", "2020-02-03", "2020-03-02"])
    m = _members([("2020-01-31", "A"), ("2020-02-29", "B"), ("2020-02-29", "X")])
    mask = momentum.membership_mask(m, idx, ["A", "B", "C"])
    assert list(mask.columns) == ["A", "B", "C"]
    assert mask["A"].tolist() == [True, True, False, False]
    assert mask["B"].tolist() == [False, False, True, False]
    assert not mask["C"].any()


def test_eligibility_and_selection_require_membership_on_the_rank_day():
    panel, idx = _two_stock_panel()
    month_end = idx[month_end_mask(idx)][0]
    # UP has the better momentum but is not a member in January.
    m = _members([(month_end, "DOWN"), (idx[-1], "UP"), (idx[-1], "DOWN")])
    panel = build_panel(
        {t: _frame(panel.close[t], idx, open_=panel.open[t]) for t in panel.tickers},
        _frame(panel.spy_close, idx),
        membership=m,
    )
    cfg = MomentumConfig(top_n=1, **SMALL)
    ok = eligibility(panel, cfg)
    assert not ok.loc[month_end, "UP"] and ok.loc[month_end, "DOWN"]
    res = simulate(panel, momentum_selector(panel, cfg), idx[0], idx[-1])
    assert res.trades.iloc[0]["ticker"] == "DOWN"
    ew = simulate(panel, equal_weight_selector(panel), idx[0], idx[-1])
    first = ew.trades[ew.trades["date"] == ew.trades["date"].min()]
    assert first["ticker"].tolist() == ["DOWN"]


def test_trim_to_membership_cuts_bars_after_the_last_snapshot_plus_buffer():
    idx = pd.bdate_range("2020-01-01", "2020-06-30")
    frames = {"GONE": _frame(np.full(len(idx), 10.0), idx)}
    frames["STAY"] = frames["GONE"].copy()
    frames["NEVER"] = frames["GONE"].copy()
    m = _members([("2020-02-29", "GONE"), ("2020-06-30", "STAY")])
    out = momentum.trim_to_membership(frames, m, days=45)
    assert set(out) == {"GONE", "STAY"}
    assert out["GONE"].index[-1] == pd.Timestamp("2020-04-14")
    assert out["STAY"].index[-1] == idx[-1]


def test_coverage_counts_member_months_with_a_bar_on_the_rebalance_day():
    idx = pd.bdate_range("2020-01-01", "2020-04-15")
    frames = {
        "A": _frame(np.full(len(idx), 10.0), idx),
        "B": _frame(np.full(40, 10.0), idx[:40]),  # ends in February
    }
    m = _members(
        [(d, t) for d in ("2020-01-31", "2020-02-29", "2020-03-31") for t in "AB"]
        + [("2020-03-31", "C")]
    )
    panel = build_panel(frames, _frame(np.full(len(idx), 100.0), idx), membership=m)
    cov = momentum.coverage(panel, m, idx[0], idx[-1])
    # Month ends: Jan 31, Feb 28, Mar 31. A: 3/3, B: Jan only, C: never.
    assert cov["member_months"] == 7
    assert cov["with_price"] == 4
    assert cov["pct"] == pytest.approx(400 / 7)
    assert cov["never_priced"] == ["C"]


def test_simulate_keeps_a_held_name_whose_open_is_missing_on_a_rebalance_day():
    """Reviewer NOTE (cycle 04): a held name with a close but no open on a
    rebalance day made gross/net NaN and sold it at a NaN price."""
    panel, idx = _two_stock_panel(70)
    cfg = MomentumConfig(top_n=1, **SMALL)
    ends = idx[month_end_mask(idx)]
    second_exec = idx[idx.get_loc(ends[1]) + 1]
    panel.open.loc[second_exec, "UP"] = np.nan
    res = simulate(panel, momentum_selector(panel, cfg), idx[0], idx[-1])
    assert np.isfinite(res.equity).all() and np.isfinite(res.cash).all()
    day = res.trades[res.trades["date"] == second_exec]
    assert "UP" not in set(day["ticker"])
    assert res.holdings.loc[second_exec] == 1
    assert res.trades["price"].notna().all()


def test_simulate_sizes_around_a_stuck_holding_without_borrowing():
    """A stuck holding that the selector drops stays held; the new names are
    sized from what is left (nothing here: all equity is in the stuck name),
    so cash never goes negative. It is sold at the next rebalance."""
    panel, idx = _two_stock_panel(70)
    ends = idx[month_end_mask(idx)]
    first_exec = idx[idx.get_loc(ends[0]) + 1]
    second_exec = idx[idx.get_loc(ends[1]) + 1]
    panel.open.loc[second_exec, "UP"] = np.nan

    def selector(i, held):
        return {"UP": 1.0} if idx[i] < ends[1] else {"DOWN": 1.0}

    res = simulate(panel, selector, first_exec, idx[-1])
    assert (res.cash >= -1e-9).all()
    day = res.trades[res.trades["date"] == second_exec]
    assert day.empty
    assert res.holdings.loc[second_exec] == 1
    assert res.no_open_skips == 1
    third_exec = idx[idx.get_loc(ends[2]) + 1]
    later = res.trades[res.trades["date"] == third_exec]
    assert set(later["ticker"]) == {"UP", "DOWN"}
