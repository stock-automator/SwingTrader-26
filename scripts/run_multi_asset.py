#!/usr/bin/env python3
"""
Cycle 07a: backtest the pre-registered multi-asset rules
(docs/research/strategy-evidence-2026-11.md, "Cycle 07 pre-registered rules").

    python scripts/run_multi_asset.py [--refresh]

Data, all cached in gitignored `data/raw_etf/`:
- Alpaca SIP daily bars, all-adjusted, from 2016 (`<SYM>.parquet`);
- yfinance auto-adjusted daily bars from 2005 (`yf/<SYM>.parquet`), used only
  before Alpaca's first session, scaled to join it;
- CBOE PutWrite (PUT) and BuyWrite (BXM) index closes (`cboe/<IDX>.parquet`).
Alpaca closes that are wrong for one session (March 2020 prints that
revert next day) are repaired from yfinance; the two ETF sources must then
agree (daily-return correlation > 0.99) and every remaining session where
they differ by more than 1% is printed. Signals at month-end
closes, trades at the next open, 2bp per side, fractional shares. Prints the
tables used in docs/multi-asset.md and saves them to
`data/reports/multi_asset.json`. Alpaca keys come from `.env` and are never
printed. `--refresh` refetches every cached series.
"""

from __future__ import annotations

import argparse
import functools
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.app.quant.etf_strategies import (  # noqa: E402
    NY,
    cached_frame,
    last_complete_end,
    month_end_dates,
    run_rebalanced,
)
from backend.app.quant.multi_asset import (  # noqa: E402
    dual_momentum_weights,
    fixed_weights,
    gtaa_aggressive_weights,
    gtaa_weights,
    inverse_vol_weights,
    patch_bad_closes,
    period_metrics,
    return_agreement,
    splice,
    turnover,
    yearly_dates,
)
from scripts.run_etf_strategies import alpaca  # noqa: E402

ETF_DIR = ROOT / "data" / "raw_etf"
REPORT = ROOT / "data" / "reports" / "multi_asset.json"
ALPACA_START = pd.Timestamp("2016-01-01", tz=NY)
YF_START = "2005-01-01"
SPLIT = pd.Timestamp("2023-01-01")
UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "SHY", "GLD", "DBC"]
UNIVERSE += ["VNQ"]
GTAA = ["SPY", "EFA", "IEF", "DBC", "VNQ"]
RISK_PARITY = ["SPY", "TLT", "GLD", "DBC"]
COST = 0.0002
MIN_CORR = 0.99
CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{}_History.csv"
CBOE = ["PUT", "BXM"]
STRESS_YEARS = (2008, 2020, 2022)


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


def yf_bars(symbol: str, end: pd.Timestamp) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(
        symbol,
        start=YF_START,
        end=str(end.date()),
        auto_adjust=True,
        progress=False,
        multi_level_index=False,
    )
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    return df[["open", "high", "low", "close", "volume"]]


def cboe_index(name: str, end: pd.Timestamp) -> pd.DataFrame:
    with urllib.request.urlopen(CBOE_URL.format(name), timeout=60) as resp:
        text = resp.read().decode()
    df = pd.read_csv(io.StringIO(text))
    df.index = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
    df = df[[name]].rename(columns={name: "close"}).astype(float)
    return df[df.index < end.tz_localize(None)]


def load_data(refresh: bool) -> tuple[dict, dict, dict]:
    bars, _ = alpaca()
    end = last_complete_end(pd.Timestamp.now(tz=NY))
    alp, yfd = {}, {}
    for s in UNIVERSE:
        alp[s] = cached_frame(
            ETF_DIR / f"{s}.parquet",
            functools.partial(bars, s, None, ALPACA_START, end),
            refresh=refresh,
        )
        yfd[s] = cached_frame(
            ETF_DIR / "yf" / f"{s}.parquet",
            functools.partial(yf_bars, s, end),
            refresh=refresh,
        )
    check, spliced = {}, {}
    for s in UNIVERSE:
        raw_corr, raw_bad = return_agreement(alp[s]["close"], yfd[s]["close"])
        fixed, repaired = patch_bad_closes(alp[s], yfd[s])
        corr, bad = return_agreement(fixed["close"], yfd[s]["close"])
        check[s] = {
            "overlap": [str(alp[s].index[0].date()), str(alp[s].index[-1].date())],
            "raw_corr": raw_corr,
            "raw_days_over_1pct": int(len(raw_bad)),
            "repaired_closes": [str(d.date()) for d in repaired],
            "corr": corr,
            "days_over_1pct": {str(d.date()): [a, b] for d, (a, b) in bad.iterrows()},
        }
        if corr <= MIN_CORR:
            sys.exit(f"{s}: Alpaca vs yfinance return correlation {corr:.4f}")
        spliced[s] = splice(yfd[s], fixed)
    indices = {
        n: cached_frame(
            ETF_DIR / "cboe" / f"{n}.parquet",
            functools.partial(cboe_index, n, end),
            refresh=refresh,
        )
        for n in CBOE
    }
    return spliced, indices, check


# --------------------------------------------------------------------------
# Backtests
# --------------------------------------------------------------------------


def run(spliced: dict, indices: dict) -> dict:
    closes = pd.DataFrame({s: spliced[s]["close"] for s in UNIVERSE}).sort_index()
    opens = pd.DataFrame({s: spliced[s]["open"] for s in UNIVERSE})
    # Common last session, so no ETF is missing at the end.
    last = min(spliced[s].index[-1] for s in UNIVERSE)
    closes = closes.loc[:last]
    opens = opens.reindex(closes.index)
    dates = month_end_dates(closes.index)

    # First signal date at which every rule has its full lookback: 12
    # month-end returns and 60 daily returns for every ETF.
    m = closes.loc[dates]
    vol_ok = closes.pct_change(fill_method=None).rolling(60).std().notna()
    ready = m.shift(12).notna().all(axis=1) & vol_ok.loc[dates].all(axis=1)
    first = ready[ready].index[0]
    dates = dates[dates >= first]

    weights = {
        "GTAA (Faber)": gtaa_weights(closes, dates, GTAA),
        "GTAA aggressive top-3": gtaa_aggressive_weights(
            closes, dates, GTAA + ["GLD", "TLT"]
        ),
        "Dual momentum (GEM)": dual_momentum_weights(closes, dates),
        "Risk parity inv-vol": inverse_vol_weights(closes, dates, RISK_PARITY),
        "Risk parity inv-vol + SMA": inverse_vol_weights(
            closes, dates, RISK_PARITY, sma_months=10
        ),
        "Permanent portfolio": fixed_weights(
            yearly_dates(dates, first),
            {"SPY": 0.25, "TLT": 0.25, "GLD": 0.25, "SHY": 0.25},
        ),
        "SPY buy-and-hold": fixed_weights(dates[:1], {"SPY": 1.0}),
        "60/40 SPY/IEF monthly": fixed_weights(dates, {"SPY": 0.6, "IEF": 0.4}),
    }
    returns, turns = {}, {}
    for name, w in weights.items():
        w = w.reindex(columns=closes.columns, fill_value=0.0)
        weights[name] = w
        returns[name] = run_rebalanced(opens, closes, w, COST)
        turns[name] = turnover(opens, w)
    for n, df in indices.items():
        lvl = df["close"].reindex(closes.index).ffill()
        returns[f"CBOE {n} index (no costs)"] = lvl.pct_change(fill_method=None)
        turns[f"CBOE {n} index (no costs)"] = None

    start = closes.index[closes.index.get_loc(first) + 1]
    idx = closes.index
    periods = {
        "build": idx[(idx >= ALPACA_START.tz_localize(None)) & (idx < SPLIT)],
        "test": idx[idx >= SPLIT],
        "full": idx[idx >= start],
    }
    rows = []
    for period, days in periods.items():
        for name, r in returns.items():
            r = r.loc[days]
            if r.isna().any():
                raise ValueError(f"{name}: missing returns in {period}")
            row = period_metrics(r, turns[name])
            row.update(period=period, row=name)
            rows.append(row)
    full = returns_in(returns, periods["full"])
    stress = {
        name: {str(y): float(v) for y, v in yearly(r).items() if y in STRESS_YEARS}
        for name, r in full.items()
    }
    held = {
        k: float((w.drop(columns="SHY", errors="ignore") > 0).sum(axis=1).mean())
        for k, w in weights.items()
    }
    cash = {
        k: float(w.get("SHY", pd.Series(0.0, w.index)).mean())
        for k, w in weights.items()
    }
    return {
        "first_signal": str(first.date()),
        "first_trade": str(start.date()),
        "last_session": str(last.date()),
        "avg_risk_assets_held": held,
        "avg_shy_weight": cash,
        "stress_years": stress,
        "rows": rows,
    }


def returns_in(returns: dict, days: pd.DatetimeIndex) -> dict:
    return {k: r.loc[days] for k, r in returns.items()}


def yearly(r: pd.Series) -> pd.Series:
    return (1 + r).groupby(r.index.year).prod() - 1


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _num(x, d: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{d}f}"


def markdown(res: dict) -> str:
    lines = []
    for period in ("build", "test", "full"):
        rows = [r for r in res["rows"] if r["period"] == period]
        lines += [
            f"\n### {period} ({rows[0]['start']} to {rows[0]['end']})\n",
            "| Rule | CAGR | Max DD | Sharpe | Calmar | Worst year | "
            "Pos. months | Turnover/yr |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            cells = [
                r["row"],
                _pct(r["cagr"]),
                _pct(r["max_drawdown"]),
                _num(r["sharpe"]),
                _num(r["calmar"]),
                f"{_pct(r['worst_year'])} ({r['worst_year_label']})",
                _pct(r["pct_positive_months"]),
                _pct(r["annual_turnover"]),
            ]
            lines.append("| " + " | ".join(cells) + " |")
    years = [str(y) for y in STRESS_YEARS]
    lines += [
        "\n### Calendar-year returns in stress years\n",
        "| Rule | " + " | ".join(years) + " |",
        "|---|" + "---|" * len(years),
    ]
    for name, ys in res["stress_years"].items():
        lines.append(f"| {name} | " + " | ".join(_pct(ys[y]) for y in years) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true", help="refetch every series")
    args = ap.parse_args()
    spliced, indices, check = load_data(args.refresh)
    res = run(spliced, indices)
    res["source_check"] = check
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(res, indent=2))
    print(json.dumps(check, indent=1))
    meta = {k: v for k, v in res.items() if k not in ("rows", "source_check")}
    print(json.dumps(meta, indent=1))
    print(markdown(res))


if __name__ == "__main__":
    main()
