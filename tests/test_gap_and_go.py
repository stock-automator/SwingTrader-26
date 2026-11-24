"""Gap-and-Go day-trade backtest (cycle 03 add-on).

Synthetic bars only: no network, no `data/raw/`, files go to `tmp_path`.
Every fill below is worked out by hand from the rules in docs/gap-and-go.md.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.intraday.downloader import (
    RateLimiter,
    consolidate,
    download_dates,
    with_retry,
)
from backend.app.quant.intraday.gap_and_go import (
    GapParams,
    find_gap_candidates,
    regular_session,
    simulate_setup,
)
from backend.app.quant.intraday.portfolio import run_portfolio, summarize

NY = "America/New_York"
DAY = "2024-03-04"
SLIP = 0.0005
ROOT = Path(__file__).resolve().parent.parent


def session(
    day: str = DAY, base: float = 100.0, volume: float = 1_000.0
) -> pd.DataFrame:
    """78 flat 5-minute bars (09:30 ... 15:55 New York) at `base`."""
    idx = pd.date_range(f"{day} 09:30", periods=78, freq="5min", tz=NY)
    return pd.DataFrame(
        {
            "Open": base,
            "High": base + 0.1,
            "Low": base - 0.1,
            "Close": base,
            "Volume": volume,
        },
        index=idx,
    )


def put(df: pd.DataFrame, hhmm: str, o, h, l, c, v=None) -> None:  # noqa: E741
    ts = pd.Timestamp(f"{df.index[0].date()} {hhmm}", tz=NY)
    df.loc[ts, ["Open", "High", "Low", "Close"]] = [o, h, l, c]
    if v is not None:
        df.loc[ts, "Volume"] = v


# ADV 39_000 -> per-bar average 500, so a 2x confirmation needs >= 1_000.
ADV = 39_000.0


def long_day() -> pd.DataFrame:
    df = session(base=101.0)
    put(df, "09:30", 100.0, 102.0, 99.0, 101.5, v=5_000)  # OR 99-102
    return df


# ---------------------------------------------------------------- candidates


def daily_frame(opens, closes, volume=2_000_000) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=len(opens))
    return pd.DataFrame(
        {
            "Open": opens,
            "High": np.maximum(opens, closes) + 1,
            "Low": np.minimum(opens, closes) - 1,
            "Close": closes,
            "Volume": volume,
        },
        index=idx,
    )


def test_gap_candidates_use_threshold_direction_and_prior_volume():
    n = 25
    closes = [100.0] * n
    opens = [100.0] * n
    opens[21] = 102.5  # +2.5% gap up -> long
    opens[22] = 98.0  # -2.0% gap down -> short (exactly 2% counts)
    opens[23] = 101.9  # +1.9% -> below threshold
    daily = daily_frame(opens, closes)
    out = find_gap_candidates(daily, "XYZ", GapParams())
    assert list(out["date"]) == [daily.index[21], daily.index[22]]
    assert list(out["direction"]) == ["long", "short"]
    assert out["gap"].iloc[0] == pytest.approx(0.025)
    assert (out["ticker"] == "XYZ").all()
    assert out["adv20"].iloc[0] == pytest.approx(2_000_000)


def test_gap_candidates_need_20_prior_days_price_and_volume():
    n = 25
    closes = [100.0] * n
    opens = [100.0] * n
    opens[10] = 105.0  # only 10 prior days: no 20-day ADV yet
    daily = daily_frame(opens, closes)
    assert find_gap_candidates(daily, "X", GapParams()).empty

    # The gap day's own huge volume must not count toward its ADV (no look-ahead).
    vol = [900_000] * n
    vol[22] = 50_000_000
    opens = [100.0] * n
    opens[22] = 103.0
    daily = daily_frame(opens, closes, volume=vol)
    assert find_gap_candidates(daily, "X", GapParams()).empty

    cheap = daily_frame([4.0] * 21 + [4.5] + [4.0] * 3, [4.0] * 25)
    assert find_gap_candidates(cheap, "X", GapParams()).empty


# ------------------------------------------------------------------ session


def test_regular_session_converts_utc_and_drops_extended_hours():
    idx = pd.date_range("2024-03-04 13:00", "2024-03-04 21:30", freq="5min", tz="UTC")
    bars = pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1}, index=idx
    )
    out = regular_session(bars)
    assert str(out.index.tz) == NY
    assert out.index[0] == pd.Timestamp("2024-03-04 09:30", tz=NY)
    assert out.index[-1] == pd.Timestamp("2024-03-04 15:55", tz=NY)
    assert len(out) == 78


# ------------------------------------------------------------------- trades


def test_long_breakout_hits_2r_target():
    df = long_day()
    put(df, "09:45", 101.5, 102.5, 101.4, 102.4)  # breaks 102
    put(df, "10:30", 103.0, 108.5, 102.9, 108.0)  # reaches target
    res = simulate_setup(df, "long", ADV, 0.03, GapParams())
    entry = 102.0 * (1 + SLIP)
    risk = entry - 99.0
    target = entry + 2 * risk
    assert res["status"] == "traded"
    assert res["entry_time"] == pd.Timestamp(f"{DAY} 09:45", tz=NY)
    assert res["entry"] == pytest.approx(entry)
    assert res["stop"] == pytest.approx(99.0)
    assert res["target"] == pytest.approx(target)
    assert res["exit_reason"] == "target"
    assert res["exit"] == pytest.approx(target * (1 - SLIP))
    assert res["r_multiple"] == pytest.approx((target * (1 - SLIP) - entry) / risk)
    assert res["rank_score"] == pytest.approx(0.03 * 10.0)  # 5000 / (39000/78)


def test_long_gap_through_fills_at_open_and_stops_out():
    df = long_day()
    put(df, "09:35", 102.6, 103.0, 102.5, 102.8)  # opens above the OR high
    put(df, "10:00", 100.0, 100.2, 98.5, 98.7)  # through the stop
    res = simulate_setup(df, "long", ADV, 0.03, GapParams())
    assert res["entry"] == pytest.approx(102.6 * (1 + SLIP))
    assert res["exit_reason"] == "stop"
    assert res["exit"] == pytest.approx(99.0 * (1 - SLIP))
    assert res["r_multiple"] < -1  # slippage makes a full stop worse than -1R


def test_stop_fills_at_open_when_bar_gaps_below_stop():
    df = long_day()
    put(df, "09:45", 101.5, 102.5, 101.4, 102.4)
    put(df, "10:00", 98.0, 98.2, 97.5, 97.8)
    res = simulate_setup(df, "long", ADV, 0.03, GapParams())
    assert res["exit"] == pytest.approx(98.0 * (1 - SLIP))


def test_bar_touching_stop_and_target_counts_as_stop():
    df = long_day()
    put(df, "09:45", 101.5, 102.5, 101.4, 102.4)
    put(df, "10:00", 102.0, 109.0, 98.0, 105.0)
    res = simulate_setup(df, "long", ADV, 0.03, GapParams())
    assert res["exit_reason"] == "stop"


def test_no_stop_or_target_exits_at_1555_close():
    df = long_day()
    put(df, "09:45", 101.5, 102.5, 101.4, 102.4)
    put(df, "15:55", 103.0, 103.2, 102.9, 103.1)
    res = simulate_setup(df, "long", ADV, 0.03, GapParams())
    assert res["exit_reason"] == "eod"
    assert res["exit_time"] == pd.Timestamp(f"{DAY} 15:55", tz=NY)
    assert res["exit"] == pytest.approx(103.1 * (1 - SLIP))


def test_no_entry_at_or_after_1100():
    df = long_day()
    put(df, "11:00", 101.5, 102.5, 101.4, 102.4)
    res = simulate_setup(df, "long", ADV, 0.03, GapParams())
    assert res["status"] == "no_trigger"
    df = long_day()
    put(df, "10:55", 101.5, 102.5, 101.4, 102.4)
    assert simulate_setup(df, "long", ADV, 0.03, GapParams())["status"] == "traded"


def test_low_first_bar_volume_is_rejected():
    df = long_day()
    df.iloc[0, df.columns.get_loc("Volume")] = 999
    put(df, "09:45", 101.5, 102.5, 101.4, 102.4)
    assert simulate_setup(df, "long", ADV, 0.03, GapParams())["status"] == "low_volume"
    df.iloc[0, df.columns.get_loc("Volume")] = 1_000
    assert simulate_setup(df, "long", ADV, 0.03, GapParams())["status"] == "traded"


def test_tiny_opening_range_is_skipped():
    df = session(base=100.0)
    put(df, "09:30", 100.0, 100.08, 99.95, 100.05, v=5_000)  # 0.13% range
    put(df, "09:45", 100.0, 100.5, 99.99, 100.4)
    assert simulate_setup(df, "long", ADV, 0.03, GapParams())["status"] == "small_risk"


def test_open_mismatch_with_daily_cache_is_flagged():
    df = long_day()
    res = simulate_setup(df, "long", ADV, 0.03, GapParams(), daily_open=80.0)
    assert res["status"] == "data_mismatch"


def test_short_mirrors_long():
    df = session(base=99.0)
    put(df, "09:30", 100.0, 101.0, 98.0, 98.5, v=5_000)  # OR 98-101
    put(df, "09:50", 98.5, 98.6, 97.5, 97.6)  # breaks 98
    put(df, "11:30", 96.0, 96.1, 91.0, 91.5)  # reaches target
    res = simulate_setup(df, "short", ADV, -0.03, GapParams())
    entry = 98.0 * (1 - SLIP)
    risk = 101.0 - entry
    target = entry - 2 * risk
    assert res["status"] == "traded"
    assert res["entry"] == pytest.approx(entry)
    assert res["target"] == pytest.approx(target)
    assert res["exit_reason"] == "target"
    assert res["exit"] == pytest.approx(target * (1 + SLIP))
    assert res["r_multiple"] == pytest.approx((entry - target * (1 + SLIP)) / risk)
    assert res["rank_score"] == pytest.approx(0.03 * 10.0)


# ---------------------------------------------------------------- portfolio


def setup_row(
    day,
    ticker,
    rank,
    triggered=True,
    minute=40,
    entry=100.0,
    stop=99.0,
    exit_=102.0,
    d="long",
):
    t = pd.Timestamp(f"{day} 09:{minute:02d}", tz=NY)
    return {
        "date": pd.Timestamp(day),
        "ticker": ticker,
        "direction": d,
        "rank_score": rank,
        "status": "traded" if triggered else "no_trigger",
        "entry_time": t if triggered else pd.NaT,
        "entry": entry if triggered else np.nan,
        "stop": stop,
        "exit": exit_ if triggered else np.nan,
        "r_multiple": (
            (exit_ - entry) / (entry - stop)
            if d == "long"
            else (entry - exit_) / (stop - entry)
        ),
    }


def test_sizing_uses_1pct_risk_and_25pct_notional_cap():
    rows = [
        setup_row(
            "2024-03-04", "WIDE", 1.0, entry=100.0, stop=95.0, exit_=110.0
        ),  # risk $5 -> 20 sh
        setup_row(
            "2024-03-05", "TIGHT", 1.0, entry=100.0, stop=99.9, exit_=100.2
        ),  # cap -> 25 sh
    ]
    sessions = pd.DatetimeIndex(["2024-03-04", "2024-03-05"])
    res = run_portfolio(pd.DataFrame(rows), sessions)
    t = res.trades.set_index("ticker")
    assert t.loc["WIDE", "shares"] == 20
    assert t.loc["WIDE", "pnl"] == pytest.approx(200.0)
    # Day 2 sizes off 10_200: 1% risk -> 1020 shares, 25% cap -> 25 shares.
    assert t.loc["TIGHT", "shares"] == 25
    assert res.equity.iloc[-1] == pytest.approx(10_200 + 25 * 0.2)


def test_short_pnl_sign():
    rows = [
        setup_row(
            "2024-03-04", "S", 1.0, entry=100.0, stop=101.0, exit_=98.0, d="short"
        )
    ]
    res = run_portfolio(pd.DataFrame(rows), pd.DatetimeIndex(["2024-03-04"]))
    assert res.trades["pnl"].iloc[0] == pytest.approx(25 * 2.0)


def test_top3_by_rank_at_open_and_untriggered_orders_use_a_slot():
    day = "2024-03-04"
    rows = [
        setup_row(day, "A", 5.0, triggered=False),
        setup_row(day, "B", 4.0),
        setup_row(day, "C", 3.0),
        setup_row(day, "D", 2.0),  # 4th by rank: never ordered
    ]
    res = run_portfolio(pd.DataFrame(rows), pd.DatetimeIndex([day]))
    assert sorted(res.trades["ticker"]) == ["B", "C"]


def test_directions_filter():
    day = "2024-03-04"
    rows = [
        setup_row(day, "L", 1.0),
        setup_row(day, "S", 2.0, stop=101.0, exit_=99.0, d="short"),
    ]
    res = run_portfolio(
        pd.DataFrame(rows), pd.DatetimeIndex([day]), directions=("long",)
    )
    assert list(res.trades["ticker"]) == ["L"]


def test_pdt_limits_three_day_trades_per_rolling_five_sessions():
    sessions = pd.bdate_range("2024-03-04", periods=7)
    rows = []
    for i, d in enumerate(sessions):
        day = d.strftime("%Y-%m-%d")
        rows += [
            setup_row(day, f"T{i}a", 2.0, minute=40),
            setup_row(day, f"T{i}b", 1.0, minute=45),
        ]
    setups = pd.DataFrame(rows)
    free = run_portfolio(setups, sessions)
    assert len(free.trades) == 14
    pdt = run_portfolio(setups, sessions, pdt=True)
    dates = pd.DatetimeIndex(pdt.trades["date"])
    for i in range(len(sessions)):
        lo, hi = max(0, i - 4), i + 1
        window = sessions[lo:hi]
        assert dates.isin(window).sum() <= 3
    # Day 1 takes 2 (earliest fill first), day 2 takes 1, then nothing until day 1 rolls off.
    assert list(pdt.trades["ticker"][:3]) == ["T0a", "T0b", "T1a"]
    assert pdt.trades["date"].iloc[3] == sessions[5]


def test_summarize_metrics():
    sessions = pd.bdate_range("2024-01-01", periods=4)
    rows = [
        setup_row(
            "2024-01-01", "W1", 1.0, entry=100.0, stop=96.0, exit_=108.0
        ),  # 25 sh, +200
        setup_row(
            "2024-01-02", "L1", 1.0, entry=100.0, stop=96.0, exit_=96.0
        ),  # 25 sh, -100
        setup_row(
            "2024-01-03", "W2", 1.0, entry=100.0, stop=96.0, exit_=104.0
        ),  # 25 sh, +100
    ]
    res = run_portfolio(pd.DataFrame(rows), sessions)
    spy = pd.Series([400.0, 404.0, 408.0, 440.0], index=sessions)
    m = summarize(res, spy)
    assert m["trades"] == 3
    assert m["win_rate"] == pytest.approx(2 / 3)
    assert m["payoff"] == pytest.approx(150.0 / 100.0)
    assert m["profit_factor"] == pytest.approx(300.0 / 100.0)
    assert m["expectancy_r"] == pytest.approx((2 - 1 + 1) / 3)
    assert m["total_return"] == pytest.approx(0.02)
    assert m["max_drawdown"] == pytest.approx(-100 / 10_200)
    assert m["pct_positive_years"] == pytest.approx(1.0)
    assert m["spy_return"] == pytest.approx(0.10)
    assert np.isfinite(m["sharpe"]) and np.isfinite(m["cagr"])


def test_summarize_with_no_trades():
    sessions = pd.bdate_range("2024-01-01", periods=3)
    res = run_portfolio(
        pd.DataFrame(columns=list(setup_row("2024-01-01", "X", 1).keys())), sessions
    )
    m = summarize(res, pd.Series([1.0, 1.0, 1.0], index=sessions))
    assert m["trades"] == 0 and m["total_return"] == 0.0


# --------------------------------------------------------------- downloader


def test_rate_limiter_spaces_calls():
    now = [0.0]
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    lim = RateLimiter(120, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        lim.wait()
    assert sum(slept) == pytest.approx(1.0)  # 2 gaps of 0.5 s at 120/min


def test_with_retry_backs_off_then_raises():
    calls = []
    slept: list[float] = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("slow")
        return "ok"

    assert with_retry(flaky, attempts=4, base_delay=1.0, sleep=slept.append) == "ok"
    assert slept == [1.0, 2.0]

    def broken():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        with_retry(broken, attempts=3, base_delay=1.0, sleep=slept.append)


def fake_bars(symbols, day):
    idx = pd.date_range(f"{day} 13:00", f"{day} 21:00", freq="5min", tz="UTC")
    frames = []
    for s in symbols:
        frames.append(
            pd.DataFrame(
                {
                    "symbol": s,
                    "timestamp": idx,
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1.0,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_download_is_resumable_chunked_and_regular_session_only(tmp_path):
    calls = []

    def fetch(symbols, day):
        calls.append((tuple(symbols), day))
        return fake_bars(symbols, day)

    plan = {dt.date(2024, 3, 4): ["AAA", "BBB", "CCC"], dt.date(2024, 3, 5): ["AAA"]}
    staging = tmp_path / "staging"
    stats = download_dates(fetch, plan, staging, chunk=2, limiter=None)
    assert stats["fetched"] == 2 and stats["skipped"] == 0
    assert len(calls) == 3  # 2 chunks on day 1, 1 on day 2

    calls.clear()
    stats = download_dates(fetch, plan, staging, chunk=2, limiter=None)
    assert calls == [] and stats["skipped"] == 2

    out = tmp_path / "gap_bars"
    rows = consolidate(staging, out)
    assert rows == {"AAA": 156, "BBB": 78, "CCC": 78}
    aaa = pd.read_parquet(out / "AAA.parquet")
    assert list(aaa.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert aaa.index.min() == pd.Timestamp("2024-03-04 09:30", tz=NY)
    assert aaa.index.max() == pd.Timestamp("2024-03-05 15:55", tz=NY)


def test_failed_day_is_not_marked_done(tmp_path):
    def fetch(symbols, day):
        raise ConnectionError("down")

    plan = {dt.date(2024, 3, 4): ["AAA"]}
    stats = download_dates(
        fetch, plan, tmp_path, chunk=2, limiter=None, attempts=2, sleep=lambda s: None
    )
    assert stats["failed"] == 1
    assert list(tmp_path.glob("*.parquet")) == []


def test_intraday_bars_are_git_ignored():
    lines = (ROOT / ".gitignore").read_text().splitlines()
    assert "data/intraday/" in lines or "/data/intraday/" in lines
