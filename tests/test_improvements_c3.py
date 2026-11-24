"""Cycle 03 step 3: tighter suspect-data filter, clamped int variants and
the improvement-experiment options (regime and trend entry filters)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.portfolio import PortfolioConfig, suspect_spike
from backend.app.quant.regime import MACRO_BLOCKED_REGIMES
from backend.app.quant.scorecard import param_variants
from backend.app.quant.strategies.base import BaseStrategy
from tests.test_portfolio_scorecard_c3 import frame, free, neutral, run, signal_on


def closes(seq: list[float], pad: int = 30) -> pd.Series:
    """Flat at the first close, then `seq`, then flat at its last close."""
    values = [seq[0]] * pad + list(seq) + [seq[-1]] * pad
    return pd.Series(values, index=pd.bdate_range("2020-01-01", periods=len(values)))


# Closes around real >40% one-day moves in the cache (pre-jump close first).
SPLICED = {
    "HCA": [425.0, 16.77, 429.0],
    "FER": [19.2, 27.5, 19.2, 19.2, 19.2],
    "two spliced bars": [100.0, 4.0, 4.0, 100.0],
}
REAL = {
    "HOOD": [46.8, 70.39, 50.97, 55.01, 56.83, 53.94, 51.19],
    "BIIB": [247.01, 355.63, 328.9, 328.9, 236.26, 236.34, 244.03],
    "CVNA": [7.96, 11.16, 9.35, 9.12, 9.25, 8.82, 8.75],
    "NCLH": [9.67, 13.75, 16.96, 15.71, 12.02, 10.68, 10.96],
    "CRWV": [37.08, 52.57, 61.36, 53.8, 47.82, 49.85, 43.61],
}


@pytest.mark.parametrize("name", sorted(SPLICED))
def test_spliced_bar_is_suspect(name):
    assert suspect_spike(closes(SPLICED[name]))


@pytest.mark.parametrize("name", sorted(REAL))
def test_real_event_that_partly_retraces_is_not_suspect(name):
    assert not suspect_spike(closes(REAL[name]))


def test_return_after_more_than_five_bars_is_not_suspect():
    assert not suspect_spike(closes([100.0] + [4.0] * 6 + [100.0]))


def test_scorecard_header_lists_suspect_tickers_once():
    from scripts.run_scorecard import suspect_line

    line = suspect_line(["FER", "HCA"])
    assert "FER, HCA" in line and "suspect data:" not in line.split("excluded")[1]
    assert suspect_line([]).endswith("none.")


class IntDefaults(BaseStrategy):
    def __init__(self, offset: int = 0, lookback: int = 1, period: int = 2):
        super().__init__(name="IntDefaults")

    def generate_signals(self, df):  # pragma: no cover - never run
        raise NotImplementedError


def test_param_variants_keep_positive_int_params_at_one_or_more():
    variants = param_variants("ints", IntDefaults)
    lookback = sorted(v["value"] for v in variants if v["param"] == "lookback")
    period = sorted(v["value"] for v in variants if v["param"] == "period")
    assert lookback == [2]  # 0 would be invalid; 1 is the default itself
    assert period == [1, 3]
    assert all(v["value"] >= 1 for v in variants if v["param"] != "offset")


# --------------------------------------------------------------------------
# Long entry filters in PortfolioConfig (regime and trend)
# --------------------------------------------------------------------------


def test_long_filters_off_by_default():
    c = PortfolioConfig()
    assert c.long_blocked_regimes == frozenset()
    assert c.long_trend_sma is None


@pytest.mark.parametrize("blocked", sorted(MACRO_BLOCKED_REGIMES))
def test_long_blocked_by_signal_day_regime(blocked):
    df = signal_on(frame(), 5)
    cfg = free(long_blocked_regimes=MACRO_BLOCKED_REGIMES)
    regime = neutral(df.index)
    regime.iloc[5] = blocked  # the signal bar's close, the fill day's t-1
    res = run({"AAA": df}, cfg, regime=regime)
    assert res.trades.empty
    assert res.skipped["long blocked by regime"] == 1


def test_long_regime_gate_ignores_fill_day_regime():
    df = signal_on(frame(), 5)
    cfg = free(long_blocked_regimes=MACRO_BLOCKED_REGIMES)
    regime = neutral(df.index)
    regime.iloc[6] = "BEAR_TRENDING"
    assert len(run({"AAA": df}, cfg, regime=regime).trades) == 1


def test_long_regime_gate_leaves_shorts_alone():
    df = signal_on(frame(), 5, sig=-1)
    cfg = free(allow_shorts=True, long_blocked_regimes=MACRO_BLOCKED_REGIMES)
    regime = neutral(df.index)
    regime.iloc[5] = "BEAR_TRENDING"
    assert len(run({"AAA": df}, cfg, regime=regime).trades) == 1


def trending(n: int, start: float, end: float):
    df = frame(n)
    line = np.linspace(start, end, n)
    for col in ("Open", "Close"):
        df[col] = line
    df["High"], df["Low"] = line * 1.001, line * 0.999
    return df


def test_long_needs_close_above_trend_sma():
    up = signal_on(trending(60, 50.0, 100.0), 30)
    down = signal_on(trending(60, 100.0, 50.0), 30)
    cfg = free(long_trend_sma=20)
    res = run({"UP": up, "DOWN": down}, cfg, regime=neutral(up.index))
    assert list(res.trades["ticker"]) == ["UP"]
    assert res.skipped["long below trend SMA"] == 1


def test_trend_sma_needs_full_window_of_history():
    df = signal_on(trending(60, 50.0, 100.0), 10)  # 11 bars < 20-day SMA
    res = run({"AAA": df}, free(long_trend_sma=20), regime=neutral(df.index))
    assert res.trades.empty


def test_trend_sma_warms_up_before_the_window():
    df = signal_on(trending(60, 50.0, 100.0), 30)
    res = run(
        {"AAA": df},
        free(long_trend_sma=20),
        regime=neutral(df.index),
        start=df.index[25],
    )
    assert len(res.trades) == 1


# --------------------------------------------------------------------------
# scripts/run_improvements.py: build-only choice, keep rule, smoke run
# --------------------------------------------------------------------------


def stats(vs=0.0, pf=1.0, dd=-20.0, **extra):
    base = {
        "vs_spy_pct": vs,
        "profit_factor": pf,
        "win_rate_pct": 40.0,
        "payoff": 1.5,
        "max_drawdown_pct": dd,
        "trades": 100,
        "positive_years_pct": 50.0,
        "positive_regimes_pct": 50.0,
    }
    return {**base, **extra}


def cand(label, build, test):
    return {"label": label, "build": build, "test": test}


def test_build_composite_ignores_test_period():
    from scripts.run_improvements import build_composite

    a = cand("a", stats(vs=10.0, pf=1.3), stats(vs=-50.0))
    b = cand("b", stats(vs=0.0, pf=1.1), stats(vs=50.0))
    scores = build_composite([a, b])
    assert scores[0] > scores[1]
    a["test"], b["test"] = b["test"], a["test"]
    assert build_composite([a, b]) == scores


def test_decide_keeps_build_winner_only_if_test_holds():
    from scripts.run_improvements import decide

    base = cand("baseline", stats(vs=0.0), stats(vs=-30.0, dd=-20.0))
    better = cand("x", stats(vs=10.0, pf=1.3), stats(vs=-25.0, dd=-18.0))
    worse = cand("y", stats(vs=-5.0, pf=0.9), stats(vs=99.0, dd=-1.0))
    out = decide([base, better, worse])
    assert out["chosen"] == "x" and out["kept"] is True
    # The build winner is chosen even when another candidate tests better.
    better["test"] = stats(vs=-31.0, dd=-18.0)
    out = decide([base, better, worse])
    assert out["chosen"] == "x" and out["kept"] is False
    better["test"] = stats(vs=-25.0, dd=-21.0)  # deeper test drawdown
    assert decide([base, better, worse])["kept"] is False


def test_decide_keeps_nothing_when_baseline_wins_build():
    from scripts.run_improvements import decide

    base = cand("baseline", stats(vs=10.0, pf=1.3), stats())
    other = cand("x", stats(vs=0.0, pf=1.0), stats(vs=50.0))
    out = decide([base, other])
    assert out["chosen"] is None and out["kept"] is False


def test_candidates_are_long_only_and_at_most_four_each():
    from scripts.run_improvements import CANDIDATES

    assert set(CANDIDATES) >= {"zscore_mean_reversion", "supertrend_psar"}
    for name, cands in CANDIDATES.items():
        assert 1 <= len(cands) <= 4
        for c in cands:
            assert not c.get("config", {}).get("allow_shorts", False)


def test_run_improvements_script_smoke(tmp_path):
    from scripts.run_improvements import main
    from tests.test_portfolio_scorecard_c3 import random_walk_universe

    raw = random_walk_universe(tmp_path)
    universe = tmp_path / "universe.json"
    universe.write_text('{"symbols": ["AAA", "BBB"]}')
    md, js = tmp_path / "imp.md", tmp_path / "imp.json"
    payload = main(
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
            "supertrend_psar",
            "--processes",
            "1",
        ]
    )
    rows = payload["strategies"]["supertrend_psar"]["rows"]
    assert rows[0]["label"] == "baseline" and len(rows) >= 2
    assert all("build_composite" in r for r in rows)
    text = md.read_text()
    assert "### supertrend_psar" in text and "\n\n| Candidate |" in text
