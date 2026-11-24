"""Momentum variants on point-in-time S&P 500 membership (cycle 04c)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.momentum import (
    MomentumConfig,
    build_panel,
    momentum_selector,
    month_end_mask,
    simulate,
)
from backend.app.quant.momentum_variants import (
    BASELINE,
    CANDIDATES,
    choose_by_sharpe,
    fip_scores,
    high_proximity,
    inverse_vol_selector,
    residual_scores,
    smooth_selector,
    strategy_returns,
    vol_scale,
    vol_scaled_selector,
)

ROOT = Path(__file__).resolve().parent.parent
SMALL = dict(lookback=10, skip=2, adv_days=3, min_dollar_volume=1e6)


def _frame(close, index, volume=1e7) -> pd.DataFrame:
    close = pd.Series(close, index=index, dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": float(volume),
        },
        index=index,
    )


def _panel(closes: dict[str, np.ndarray], n: int, spy=None):
    idx = pd.bdate_range("2020-01-01", periods=n)
    frames = {t: _frame(c, idx) for t, c in closes.items()}
    spy = np.full(n, 100.0) if spy is None else spy
    return build_panel(frames, _frame(spy, idx)), idx


def _trend(n: int, daily: float, start: float = 10.0) -> np.ndarray:
    return start * (1 + daily) ** np.arange(n)


# --- setup ------------------------------------------------------------------


def test_baseline_is_6_1_top_10_and_five_candidates_are_named():
    assert BASELINE.lookback == 126 and BASELINE.skip == 21
    assert BASELINE.top_n == 10 and not BASELINE.market_filter
    assert len(CANDIDATES) == 5
    assert len(set(CANDIDATES)) == 5


# --- 1. volatility scaling ---------------------------------------------------


def test_vol_scale_targets_annual_vol_and_caps_at_one():
    idx = pd.bdate_range("2020-01-01", periods=60)
    alt = np.where(np.arange(60) % 2 == 0, 0.02, -0.02)
    returns = pd.Series(alt, index=idx)
    scale = vol_scale(returns, target=0.15, days=40, min_days=20)
    vol = returns.iloc[20:60].std() * np.sqrt(252)
    assert scale.iloc[-1] == pytest.approx(0.15 / vol)
    assert scale.iloc[-1] < 1
    # Before min_days of history: unscaled (1.0). Calm returns: capped at 1.0.
    assert scale.iloc[:19].eq(1.0).all()
    calm = vol_scale(returns * 0.01, target=0.15, days=40, min_days=20)
    assert calm.iloc[-1] == 1.0


def test_strategy_returns_skip_the_flat_cash_period_before_first_trade():
    idx = pd.bdate_range("2020-01-01", periods=6)
    equity = pd.Series([100.0, 100, 100, 101, 99, 100], index=idx)
    holdings = pd.Series([0, 0, 1, 1, 1, 1], index=idx)
    out = strategy_returns(equity, holdings)
    assert out.index[0] == idx[2]
    assert out.iloc[1] == pytest.approx(0.01)


def test_vol_scaled_selector_scales_the_baseline_weights():
    n = 80
    closes = {"UP": _trend(n, 0.01), "MID": _trend(n, 0.005), "DOWN": _trend(n, 0)}
    panel, idx = _panel(closes, n)
    cfg = MomentumConfig(top_n=2, **SMALL)
    scale = pd.Series(0.5, index=idx)
    i = int(np.flatnonzero(month_end_mask(idx))[1])
    base = momentum_selector(panel, cfg)(i, set())
    scaled = vol_scaled_selector(panel, cfg, scale)(i, set())
    assert set(base) == set(scaled) == {"UP", "MID"}
    assert all(scaled[t] == pytest.approx(base[t] * 0.5) for t in base)


def test_vol_scaled_run_leaves_the_rest_in_cash():
    n = 80
    closes = {"UP": _trend(n, 0.01), "DOWN": _trend(n, 0)}
    panel, idx = _panel(closes, n)
    cfg = MomentumConfig(top_n=1, **SMALL)
    scale = pd.Series(0.4, index=idx)
    res = simulate(panel, vol_scaled_selector(panel, cfg, scale), idx[0], idx[-1])
    assert res.invested.iloc[-1] == pytest.approx(0.4, abs=0.05)


# --- 2. smooth momentum (frog in the pan) ------------------------------------


def test_fip_scores_reward_steady_gains_over_jumps():
    n = 30
    steady = _trend(n, 0.01)
    # Same overall gain, earned in one jump with small daily losses otherwise.
    jumpy = np.full(n, 10.0)
    jumpy[15:] = 14.0
    jumpy = jumpy * (1 - 0.0005) ** np.arange(n)
    falling = _trend(n, -0.01)
    idx = pd.bdate_range("2020-01-01", periods=n)
    mark = pd.DataFrame({"S": steady, "J": jumpy, "F": falling}, index=idx)
    fip = fip_scores(mark, mark, lookback=20, skip=2)
    last = fip.iloc[-1]
    assert last["S"] == pytest.approx(1.0)  # every day up
    assert last["J"] < 0  # mostly down days, positive total return
    assert last["F"] == pytest.approx(1.0)  # sign(-) x (0% - 100%)
    assert fip.iloc[:20].isna().all().all()


def test_smooth_selector_prefers_consistent_names_within_top_2n():
    n = 60
    steady = _trend(n, 0.004)
    jumpy = np.full(n, 10.0)
    jumpy[45:] = 20.0
    jumpy = jumpy * (1 - 0.001) ** np.arange(n)
    closes = {"STEADY": steady, "JUMPY": jumpy, "FLAT": _trend(n, 0.0001)}
    closes["LOSER"] = _trend(n, -0.01)
    panel, idx = _panel(closes, n)
    cfg = MomentumConfig(lookback=20, skip=2, top_n=1, adv_days=3)
    i = n - 3
    # JUMPY has the best 6-1 return, STEADY the smoother path; LOSER is out
    # of the top 2N even though its path is "consistent".
    assert smooth_selector(panel, cfg)(i, set()) == {"STEADY": 1.0}


# --- 3. residual momentum ----------------------------------------------------


def test_residual_scores_rank_idiosyncratic_recent_gains():
    n = 21 * 30
    rng = np.random.default_rng(1)
    spy = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n)))
    spy_ret = np.diff(np.log(spy), prepend=np.log(spy[0]))
    noise = rng.normal(0, 0.002, (n, 2))
    late = np.arange(n) > n - 140
    good = np.exp(np.cumsum(2 * spy_ret + noise[:, 0] + 0.003 * late))
    bad = np.exp(np.cumsum(0.5 * spy_ret + noise[:, 1] - 0.003 * late))
    panel, idx = _panel({"GOOD": 10 * good, "BAD": 10 * bad}, n, spy=spy)
    scores = residual_scores(panel, months=36, min_months=12, recent=5)
    ends = idx[month_end_mask(idx)]
    last = scores.loc[ends[-1]]
    assert last["GOOD"] > 0 > last["BAD"]
    # Too few monthly returns early on: no score. Only month ends are scored.
    assert scores.loc[ends[5]].isna().all()
    assert scores.drop(index=ends).isna().all().all()


def test_residual_scores_use_no_data_after_the_rank_day():
    n = 21 * 20
    rng = np.random.default_rng(2)
    spy = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    a = 10 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))
    panel, idx = _panel({"A": a}, n, spy=spy)
    ends = idx[month_end_mask(idx)]
    before = residual_scores(panel, months=36, min_months=12, recent=5)
    later = idx.get_loc(ends[-2]) + 1
    panel.close.iloc[later:] *= 3
    panel.mark.iloc[later:] *= 3
    panel.spy_close.iloc[later:] *= 0.5
    after = residual_scores(panel, months=36, min_months=12, recent=5)
    assert after.loc[ends[-2], "A"] == pytest.approx(before.loc[ends[-2], "A"])


# --- 4. 52-week-high proximity -----------------------------------------------


def test_high_proximity_is_close_over_rolling_high():
    idx = pd.bdate_range("2020-01-01", periods=6)
    close = pd.DataFrame({"A": [5.0, 10, 8, 6, 9, 12]}, index=idx)
    out = high_proximity(close, close, days=3)
    assert out["A"].iloc[:2].isna().all()
    assert out["A"].iloc[2] == pytest.approx(0.8)
    assert out["A"].iloc[3] == pytest.approx(0.6)
    assert out["A"].iloc[4] == pytest.approx(1.0)
    assert out["A"].iloc[5] == pytest.approx(1.0)


# --- 5. inverse-volatility weights -------------------------------------------


def test_inverse_vol_selector_weights_baseline_names_by_inverse_vol():
    n = 80
    calm = _trend(n, 0.01) * (1 + 0.01 * (np.arange(n) % 2))
    wild = _trend(n, 0.01) * (1 + 0.03 * (np.arange(n) % 2))
    closes = {"CALM": calm, "WILD": wild, "DOWN": _trend(n, -0.01)}
    panel, idx = _panel(closes, n)
    cfg = MomentumConfig(top_n=2, **SMALL)
    i = int(np.flatnonzero(month_end_mask(idx))[1])
    w = inverse_vol_selector(panel, cfg, days=20)(i, set())
    assert set(w) == set(momentum_selector(panel, cfg)(i, set()))
    assert sum(w.values()) == pytest.approx(1.0)
    vol = panel.mark.pct_change().rolling(20).std().iloc[i]
    assert w["CALM"] / w["WILD"] == pytest.approx(vol["WILD"] / vol["CALM"])


# --- selection ----------------------------------------------------------------


def test_choose_by_sharpe_breaks_ties_on_drawdown():
    stats = {
        "a": {"sharpe": 0.5, "max_drawdown_pct": -30.0},
        "b": {"sharpe": 0.7, "max_drawdown_pct": -40.0},
        "c": {"sharpe": 0.7, "max_drawdown_pct": -20.0},
        "d": {"sharpe": None, "max_drawdown_pct": -1.0},
    }
    assert choose_by_sharpe(stats) == "c"


# --- script -----------------------------------------------------------------


def test_run_momentum_variants_script_writes_doc_and_json(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_momentum_variants

    idx = pd.bdate_range("2015-01-01", "2024-03-29")
    rng = np.random.default_rng(0)
    data, pit = tmp_path / "raw", tmp_path / "raw_pit"
    data.mkdir()
    pit.mkdir()

    def write(sym, i, where, days=idx):
        drift = 0.0002 * (i % 7)
        close = 50 * np.exp(np.cumsum(rng.normal(drift, 0.01, len(days))))
        df = _frame(close, days, volume=1e6)
        df["Adj Close"] = df["Close"]
        df.to_parquet(where / f"{sym}.parquet")

    symbols = [f"T{i}" for i in range(25)]
    for i, sym in enumerate(symbols + ["SPY"]):
        write(sym, i, data)
    write("GONE", 6, pit, idx[idx <= "2020-06-15"])
    months = pd.date_range("2015-12-31", "2024-03-31", freq="ME")
    rows = [(d, t) for d in months for t in symbols]
    rows += [(d, "GONE") for d in months if d <= pd.Timestamp("2020-06-30")]
    membership = tmp_path / "membership.csv"
    pd.DataFrame(rows, columns=["date", "ticker"]).to_csv(
        membership, index=False, date_format="%Y-%m-%d"
    )
    md, js = tmp_path / "variants.md", tmp_path / "variants.json"
    run_momentum_variants.main(
        [
            "--data-dir",
            str(data),
            "--pit-dir",
            str(pit),
            "--membership",
            str(membership),
            "--markdown",
            str(md),
            "--json",
            str(js),
        ]
    )
    out = json.loads(js.read_text())
    runs = set(CANDIDATES) | {BASELINE.name, "SPY buy & hold"}
    assert set(out["build"]) == set(out["test"]) == runs
    assert out["winner"] in CANDIDATES
    assert out["winner"] == choose_by_sharpe({c: out["build"][c] for c in CANDIDATES})
    assert set(out["beats_spy_sharpe_both"]) == set(CANDIDATES)
    for period in ("build", "test"):
        for stats in out[period].values():
            for key in ("cagr_pct", "max_drawdown_pct", "sharpe"):
                assert key in stats
            assert "positive_years_pct" in stats
            assert "monthly_turnover_pct" in stats
    text = md.read_text()
    assert out["winner"] in text
    assert all(name in text for name in runs)
