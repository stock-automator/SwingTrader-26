"""Cycle 03 steps 1-2: portfolio backtest engine and strategy scorecard.

Synthetic bars only (no network, no `data/raw/`). A scripted strategy reads
its signals from extra frame columns, so every entry, stop and target is
hand-placed and the expected fills can be worked out by hand.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.portfolio import PortfolioConfig, run_portfolio_backtest
from backend.app.quant.scorecard import (
    composite_rank,
    curve_stats,
    param_variants,
    score_flags,
    score_strategy,
    trade_stats,
)
from backend.app.quant.strategies.base import BaseStrategy

START = "2020-01-01"


class Scripted(BaseStrategy):
    """Signals from the frame's own `sig`/`sl`/`tp` columns (PERCENTAGE)."""

    def __init__(self):
        super().__init__(name="Scripted")

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        sig = df.get("sig", pd.Series(0, index=df.index)).fillna(0).astype(int)
        active = sig != 0
        out = pd.DataFrame(index=df.index)
        out["signal"] = sig
        out["sl_type"] = np.where(active, "PERCENTAGE", None)
        out["sl_value"] = np.where(active, df.get("sl", 0.05), np.nan)
        out["tp_type"] = np.where(active, "PERCENTAGE", None)
        out["tp_value"] = np.where(active, df.get("tp", 0.10), np.nan)
        return out


def frame(n: int = 120, price: float = 100.0, start: str = START) -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n)
    close = np.full(n, price)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.001,
            "Low": close * 0.999,
            "Close": close,
            "Volume": 1_000_000.0,
            "sig": 0,
            "sl": 0.05,
            "tp": 0.10,
        },
        index=idx,
    )


def signal_on(df: pd.DataFrame, day: int, sig: int = 1, sl=0.05, tp=0.10):
    df.iloc[day, df.columns.get_loc("sig")] = sig
    df.iloc[day, df.columns.get_loc("sl")] = sl
    df.iloc[day, df.columns.get_loc("tp")] = tp
    return df


def free(**kwargs) -> PortfolioConfig:
    """No costs, so dollar outcomes are hand-computable."""
    base = dict(commission=0.0, slippage_pct=0.0, fx_cost_pct=0.0)
    base.update(kwargs)
    return PortfolioConfig(**base)


def run(frames, config=None, spy=None, regime=None, start=None, end=None):
    spy = spy if spy is not None else frame(len(next(iter(frames.values()))))
    return run_portfolio_backtest(
        Scripted(),
        frames,
        spy,
        config or free(),
        start=start or spy.index[0],
        end=end or spy.index[-1],
        regime=regime,
    )


# --------------------------------------------------------------------------
# Portfolio engine
# --------------------------------------------------------------------------


def test_defaults_match_gate1():
    c = PortfolioConfig()
    assert c.initial_capital == 10_000
    assert c.risk_per_trade_pct == 0.01
    assert c.max_position_pct == 0.25
    assert c.max_positions == 6
    assert c.max_heat_pct == 0.06
    assert c.time_stop_days == 40
    assert c.shorts_require_non_bull is True


def test_entry_fills_next_open_with_signal_bar_levels():
    df = signal_on(frame(), 5)
    df.iloc[6, df.columns.get_loc("Open")] = 101.0  # fill day open differs
    res = run({"AAA": df})
    t = res.trades.iloc[0]
    assert t["entry_date"] == df.index[6]
    assert t["entry_price"] == pytest.approx(101.0)
    # Stop/target come from the signal bar's close (100), not the fill.
    assert t["stop"] == pytest.approx(95.0)
    assert t["target"] == pytest.approx(110.0)


def test_signal_on_last_bar_is_not_filled():
    df = signal_on(frame(), 119)
    assert run({"AAA": df}).trades.empty


def test_future_bars_do_not_change_entry():
    a = signal_on(frame(), 5)
    b = a.copy()
    b.iloc[30:, b.columns.get_loc("Close")] = 100.5  # changes after entry
    ta, tb = run({"AAA": a}).trades.iloc[0], run({"AAA": b}).trades.iloc[0]
    assert (ta["entry_date"], ta["entry_price"], ta["shares"]) == (
        tb["entry_date"],
        tb["entry_price"],
        tb["shares"],
    )


def test_sizing_risks_one_percent_of_equity():
    # $100 risk / $5 stop distance = 20 shares ($2,000 < 25% cap).
    res = run({"AAA": signal_on(frame(), 5, sl=0.05)})
    assert res.trades.iloc[0]["shares"] == 20


def test_position_capped_at_25pct_of_equity():
    # 1% stop -> 100 shares ($10,000) uncapped; 25% cap -> 25 shares.
    res = run({"AAA": signal_on(frame(), 5, sl=0.01, tp=0.03)})
    assert res.trades.iloc[0]["shares"] == 25
    assert res.skipped.get("size capped to 25% of equity") == 1


def test_max_open_positions():
    frames = {f"T{i}": signal_on(frame(), 5, sl=0.10, tp=0.20) for i in range(8)}
    res = run(frames)
    assert len(res.trades) == 6
    assert res.skipped["max positions"] == 2


def test_heat_cap_limits_total_open_risk():
    # Each trade risks exactly 1% ($100 = 8 sh x $12.50); 6% heat allows 6.
    frames = {f"T{i}": signal_on(frame(), 5, sl=0.125, tp=0.25) for i in range(7)}
    res = run(frames, free(max_positions=10))
    assert len(res.trades) == 6
    assert res.skipped["heat cap"] == 1


def test_cash_never_negative_and_shortfall_counted():
    frames = {f"T{i}": signal_on(frame(), 5, sl=0.01, tp=0.03) for i in range(5)}
    cfg = free(
        max_positions=10, max_heat_pct=1.0, risk_per_trade_pct=0.5, max_position_pct=1
    )
    res = run(frames, cfg)
    assert (res.cash >= -1e-9).all()
    assert res.skipped.get("insufficient cash", 0) >= 1
    assert res.trades["shares"].mul(res.trades["entry_price"]).sum() <= 10_000 + 1e-6


def test_one_position_per_ticker():
    df = frame()
    for d in (5, 6, 7, 8):
        signal_on(df, d)
    res = run({"AAA": df})
    assert len(res.trades) == 1


def test_time_stop_after_40_trading_days():
    res = run({"AAA": signal_on(frame(), 5, sl=0.10, tp=0.20)})
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "time"
    assert t["holding_days"] == 40
    assert t["exit_date"] == res.equity.index[6 + 40]


def test_stop_checked_before_target_on_same_bar():
    df = signal_on(frame(), 5)
    df.iloc[10, df.columns.get_loc("Low")] = 90.0
    df.iloc[10, df.columns.get_loc("High")] = 115.0
    t = run({"AAA": df}).trades.iloc[0]
    assert t["exit_reason"] == "stop"
    assert t["exit_price"] == pytest.approx(95.0)
    assert t["r_multiple"] == pytest.approx(-1.0)


def test_target_exit_r_multiple():
    df = signal_on(frame(), 5)
    df.iloc[10, df.columns.get_loc("High")] = 111.0
    t = run({"AAA": df}).trades.iloc[0]
    assert t["exit_reason"] == "target"
    assert t["pnl"] == pytest.approx(20 * 10.0)
    assert t["r_multiple"] == pytest.approx(2.0)


def test_end_of_window_closes_open_positions():
    df = signal_on(frame(30), 5)
    t = run({"AAA": df}).trades.iloc[0]
    assert t["exit_reason"] == "end"


def neutral(index):
    return pd.Series("NEUTRAL", index=index)


def test_short_profits_when_price_falls():
    df = signal_on(frame(), 5, sig=-1, sl=0.05, tp=0.10)
    df.iloc[10, df.columns.get_loc("Low")] = 89.0
    res = run({"AAA": df}, free(allow_shorts=True), regime=neutral(df.index))
    t = res.trades.iloc[0]
    assert t["side"] == "short"
    assert t["stop"] == pytest.approx(105.0)
    assert t["exit_reason"] == "target"
    assert t["pnl"] == pytest.approx(20 * 10.0)
    assert t["r_multiple"] == pytest.approx(2.0)
    assert res.equity.iloc[-1] == pytest.approx(10_200.0)


def test_short_loses_when_price_rises():
    df = signal_on(frame(), 5, sig=-1)
    df.iloc[10, df.columns.get_loc("High")] = 106.0
    res = run({"AAA": df}, free(allow_shorts=True), regime=neutral(df.index))
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "stop" and t["pnl"] < 0
    assert t["r_multiple"] == pytest.approx(-1.0)


def test_shorts_ignored_when_not_allowed():
    df = signal_on(frame(), 5, sig=-1)
    res = run({"AAA": df}, free(allow_shorts=False), regime=neutral(df.index))
    assert res.trades.empty


def test_short_gate_uses_signal_day_regime_not_fill_day():
    df = signal_on(frame(), 5, sig=-1)
    cfg = free(allow_shorts=True, shorts_require_non_bull=True)
    # Bull on the signal day (t-1), neutral on the fill day: blocked.
    regime = neutral(df.index)
    regime.iloc[5] = "BULL_TRENDING"
    res = run({"AAA": df}, cfg, regime=regime)
    assert res.trades.empty
    assert res.skipped["short blocked by bull regime"] == 1
    # Neutral on the signal day, bull on the fill day: taken.
    regime = neutral(df.index)
    regime.iloc[6] = "BULL_TRENDING"
    assert len(run({"AAA": df}, cfg, regime=regime).trades) == 1


def test_default_regime_from_spy_blocks_shorts_in_bull_market():
    n = 300
    spy = frame(n)
    rising = np.linspace(100, 160, n)
    for col in ("Open", "High", "Low", "Close"):
        spy[col] = rising
    df = signal_on(frame(n), 280, sig=-1)
    res = run({"AAA": df}, free(allow_shorts=True), spy=spy)
    assert res.trades.empty
    assert res.skipped["short blocked by bull regime"] == 1


def test_ranking_by_reward_to_risk_when_slots_full():
    low = signal_on(frame(), 5, sl=0.05, tp=0.10)  # 2R
    high = signal_on(frame(), 5, sl=0.05, tp=0.15)  # 3R
    res = run({"LOW": low, "HIGH": high}, free(max_positions=1))
    assert list(res.trades["ticker"]) == ["HIGH"]
    assert res.skipped["max positions"] == 1


def test_costs_reduce_pnl():
    df = signal_on(frame(), 5)
    df.iloc[10, df.columns.get_loc("High")] = 111.0
    gross = run({"AAA": df}).trades.iloc[0]["pnl"]
    net = run(
        {"AAA": df}, PortfolioConfig(commission=0.001, slippage_pct=0.0005)
    ).trades.iloc[0]["pnl"]
    assert net < gross


def test_exposure_and_invested():
    res = run({"AAA": signal_on(frame(), 5, sl=0.05)})
    # Invested at the closes of days 6-45; flat after the day-46 time stop.
    assert res.exposure_pct == pytest.approx(40 / 120 * 100)
    # 20 shares x $100 = 20% of $10,000 on held days.
    assert res.avg_invested_pct == pytest.approx(40 / 120 * 20, rel=1e-3)


def test_trade_columns():
    t = run({"AAA": signal_on(frame(), 5)}).trades
    for col in (
        "ticker",
        "side",
        "strategy",
        "entry_date",
        "entry_price",
        "exit_date",
        "exit_price",
        "shares",
        "pnl",
        "r_multiple",
        "holding_days",
        "exit_reason",
    ):
        assert col in t.columns
    assert t.iloc[0]["strategy"] == "Scripted"


# --------------------------------------------------------------------------
# Scorecard
# --------------------------------------------------------------------------


def test_trade_stats_hand_computed():
    trades = pd.DataFrame({"pnl": [100.0, -50.0, 200.0, -50.0]})
    s = trade_stats(trades)
    assert s["trades"] == 4
    assert s["profit_factor"] == pytest.approx(3.0)
    assert s["win_rate_pct"] == pytest.approx(50.0)
    assert s["payoff"] == pytest.approx(3.0)


def test_trade_stats_empty_and_no_losses():
    assert trade_stats(pd.DataFrame({"pnl": []}))["trades"] == 0
    s = trade_stats(pd.DataFrame({"pnl": [10.0]}))
    assert s["profit_factor"] is None and s["payoff"] is None


def test_curve_stats_hand_computed():
    idx = pd.to_datetime(["2021-01-01", "2021-06-30", "2021-12-31", "2022-12-31"])
    equity = pd.Series([100.0, 80.0, 110.0, 121.0], index=idx)
    spy = pd.Series([100.0, 100.0, 100.0, 100.0], index=idx)
    invested = pd.Series(0.5, index=idx)
    s = curve_stats(equity, spy, invested)
    assert s["total_return_pct"] == pytest.approx(21.0)
    assert s["max_drawdown_pct"] == pytest.approx(-20.0)
    assert s["yearly_returns_pct"] == {
        2021: pytest.approx(10.0),
        2022: pytest.approx(10.0),
    }
    assert s["spy_matched_return_pct"] == pytest.approx(0.0)
    assert s["vs_spy_pct"] == pytest.approx(21.0)
    years = (idx[-1] - idx[0]).days / 365.25
    assert s["cagr_pct"] == pytest.approx((1.21 ** (1 / years) - 1) * 100)


def test_curve_stats_regime_returns():
    idx = pd.bdate_range("2021-01-01", periods=4)
    equity = pd.Series([100.0, 110.0, 99.0, 99.0], index=idx)
    regime = pd.Series(["BULL_TRENDING", "BULL_TRENDING", "NEUTRAL", "NEUTRAL"], idx)
    s = curve_stats(equity, equity, pd.Series(1.0, index=idx), regime=regime)
    # Day returns grouped by the prior close's regime.
    assert s["regime_returns_pct"]["BULL_TRENDING"] == pytest.approx(-1.0, abs=1e-9)
    assert s["regime_returns_pct"]["NEUTRAL"] == pytest.approx(0.0)


def scripted_universe():
    """Two tickers, 2021-2024, a winning trade every ~25 bars."""
    n = 1000
    frames = {}
    for name in ("AAA", "BBB"):
        df = frame(n, start="2021-01-01")
        for d in range(10, n - 5, 25):
            signal_on(df, d)
            df.iloc[d + 3, df.columns.get_loc("High")] = 111.0
        frames[name] = df
    return frames, frame(n, start="2021-01-01")


def test_score_strategy_build_test_split():
    frames, spy = scripted_universe()
    score = score_strategy(
        "scripted", frames, spy, free(), split_date="2023-01-01", strategy=Scripted()
    )
    long_ = score["variants"]["long"]
    assert long_["build"]["end"] < "2023-01-01" <= long_["test"]["start"]
    assert long_["build"]["trades"] > 0 and long_["test"]["trades"] > 0
    assert long_["test"]["profit_factor"] is None  # no losers
    # No short signals: long+short is not run as a separate row.
    assert score["variants"].get("long_short") is None
    assert score["robustness"] is None  # Scripted exposes no params


def test_param_variants_plus_minus_20pct():
    variants = param_variants("donchian_breakout")
    params = {(v["param"], v["value"]) for v in variants}
    assert ("breakout_period", 16) in params and ("breakout_period", 24) in params
    sl = sorted(v for p, v in params if p == "sl_atr_multiplier")
    assert math.isclose(sl[0], 1.2) and math.isclose(sl[1], 1.8)
    assert all(isinstance(v["value"], int) for v in variants if "period" in v["param"])
    assert len(variants) == 2 * 7


def test_flags():
    build = {"cagr_pct": 20.0, "trades": 100}
    test = {"cagr_pct": 2.0, "trades": 12}
    flags = score_flags(build, test, short_trades=3)
    assert "too few trades" in flags
    assert "test much worse than build" in flags
    assert "short-side unmodelled borrow" in flags
    ok = score_flags({"cagr_pct": 10.0, "trades": 50}, {"cagr_pct": 9.0, "trades": 40})
    assert ok == []


def row(name, vs, pf, dd, cons, worst, trades=100):
    return {
        "strategy": name,
        "variant": "long",
        "test": {
            "vs_spy_pct": vs,
            "profit_factor": pf,
            "max_drawdown_pct": dd,
            "positive_years_pct": cons,
            "trades": trades,
        },
        "build": {
            "vs_spy_pct": vs,
            "profit_factor": pf,
            "max_drawdown_pct": dd,
            "positive_years_pct": cons,
            "trades": trades,
        },
        "robustness_worst_cagr_pct": worst,
        "flags": [] if trades >= 30 else ["too few trades"],
    }


def test_composite_rank_order():
    rows = [
        row("mid", 5.0, 1.3, -15.0, 60.0, 2.0),
        row("worst", -10.0, 0.8, -40.0, 20.0, -5.0),
        row("best", 20.0, 2.0, -8.0, 100.0, 8.0),
    ]
    ranked = composite_rank(rows)
    assert [r["strategy"] for r in ranked] == ["best", "mid", "worst"]
    assert [r["rank"] for r in ranked] == [1, 2, 3]
    assert ranked[0]["score"] > ranked[1]["score"] > ranked[2]["score"]


def test_composite_rank_penalises_too_few_trades():
    rows = [
        row("thin", 20.0, 2.0, -8.0, 100.0, 8.0, trades=10),
        row("solid", 19.0, 1.9, -9.0, 90.0, 7.0),
    ]
    assert composite_rank(rows)[0]["strategy"] == "solid"


def test_composite_rank_handles_missing_metrics():
    rows = [
        row("none", None, None, None, None, None),
        row("some", 1.0, 1.1, -5.0, 50.0, 0.0),
    ]
    assert composite_rank(rows)[0]["strategy"] == "some"


# --------------------------------------------------------------------------
# scripts/run_scorecard.py
# --------------------------------------------------------------------------


def test_run_scorecard_script_writes_outputs(tmp_path, capsys):
    from scripts.run_scorecard import main

    frames, spy = scripted_universe()
    raw = tmp_path / "raw"
    raw.mkdir()
    for name, df in {**frames, "SPY": spy}.items():
        rng = np.random.default_rng(len(name))
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        walk = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, len(df)))
        df["Close"] = walk
        df["Open"] = walk
        df["High"] = walk * 1.01
        df["Low"] = walk * 0.99
        df.index.name = "Date"
        df.to_parquet(raw / f"{name}.parquet")
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"symbols": ["AAA", "BBB", "MISSING"]}))
    md, js = tmp_path / "scorecard.md", tmp_path / "scorecard.json"

    main(
        [
            "--data-dir",
            str(raw),
            "--universe",
            str(universe),
            "--markdown",
            str(md),
            "--json",
            str(js),
            "--strategies",
            "donchian_breakout,moving_average_cross",
            "--processes",
            "1",
            "--no-robustness",
        ]
    )
    text = md.read_text()
    assert "| Rank |" in text and "donchian_breakout" in text
    payload = json.loads(js.read_text())
    assert {r["strategy"] for r in payload["rows"]} <= {
        "donchian_breakout",
        "moving_average_cross",
    }
    assert payload["missing_symbols"] == ["MISSING"]
    out = capsys.readouterr()
    assert "MISSING" in out.out + out.err
