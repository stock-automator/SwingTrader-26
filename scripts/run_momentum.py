#!/usr/bin/env python3
"""
Ranked cross-sectional momentum backtest on point-in-time S&P 500
membership (cycles 04 and 04b).

Runs the 8 approved variants (12-1 / 6-1 lookback x top 10 / 20 x SPY 200-day
filter off / on), an equal-weight member basket rebalanced monthly and SPY buy
and hold on the build period (2016 to the split) and the test period (the
split to the last cached bar), each from $10,000. A ticker is eligible only in
months whose `config/sp500_membership.csv` snapshot lists it; removed members
come from `data/raw_pit` (`scripts/fetch_pit_prices.py`). The variant is
chosen on build-period CAGR / |max drawdown| only. The cycle 04 run on today's
universe is repeated on the test period for comparison. Writes
`docs/momentum.md` and `data/reports/momentum.json`. Cached bars only.

    python scripts/run_momentum.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.app.data.loader import load_prices  # noqa: E402
from backend.app.data.pit_prices import STATUS_FILE  # noqa: E402
from backend.app.quant.momentum import (  # noqa: E402
    DELISTED,
    INITIAL_CAPITAL,
    SLIPPAGE_PCT,
    VARIANTS,
    build_panel,
    buy_hold_result,
    choose_variant,
    coverage,
    equal_weight_selector,
    mar,
    momentum_scores,
    momentum_selector,
    month_end_mask,
    run_stats,
    simulate,
    trim_to_membership,
)
from backend.app.quant.scorecard import BUILD_START, SPLIT_DATE  # noqa: E402
from scripts.run_scorecard import load_universe  # noqa: E402

EQUAL_WEIGHT = "Equal-weight universe"
BIASED = "Today's universe CAGR %"
SPY_BH = "SPY buy & hold"
ZSCORE = "zscore_mean_reversion (cycle 03)"

COLUMNS = [
    ("CAGR %", "cagr_pct", 1),
    ("Total %", "total_return_pct", 1),
    ("Max DD %", "max_drawdown_pct", 1),
    ("Sharpe", "sharpe", 2),
    ("Pos. years %", "positive_years_pct", 0),
    ("Turnover %/mo", "monthly_turnover_pct", 1),
    ("Avg holdings", "avg_holdings", 1),
    ("Exposure %", "exposure_pct", 1),
    ("Avg invested %", "avg_invested_pct", 1),
    ("Delisted exits", "delisted_exits", 0),
]


def zscore_rows(path: Path) -> dict[str, dict]:
    """Cycle 03's best scorecard row (long-only zscore), if the report exists."""
    if not path.exists():
        return {}
    rows = json.loads(path.read_text()).get("rows", [])
    for row in rows:
        if row["strategy"] == "zscore_mean_reversion" and row["variant"] == "long":
            return {"build": row["build"], "test": row["test"]}
    return {}


def _years(stats: dict) -> str:
    years = stats.get("yearly_returns_pct") or {}
    return ", ".join(f"{y}: {_fmt(v, 1)}%" for y, v in years.items())


def load_members(
    data_dir: Path, pit_dir: Path, membership: pd.DataFrame
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Every member's bars, from the main cache or else the pit cache,
    trimmed 45 days after it left; plus members with no bars anywhere."""
    frames, missing = {}, []
    for ticker in sorted(membership["ticker"].unique()):
        for where in (data_dir, pit_dir):
            if (where / f"{ticker}.parquet").exists():
                frames[ticker] = load_prices(
                    ticker, data_dir=where, allow_download=False
                )
                break
        else:
            missing.append(ticker)
    return trim_to_membership(frames, membership), missing


def rankable(panel, start, end) -> dict[str, float]:
    """% of member-months (month-end rebalance days) with a momentum score."""
    days = panel.index[month_end_mask(panel.index)]
    days = days[(days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end))]
    member = panel.member.loc[days]
    out = {}
    for label, lookback in (("12-1", 252), ("6-1", 126)):
        scores = momentum_scores(panel.close, panel.mark, lookback, 21).loc[days]
        out[label] = float((scores.notna() & member).to_numpy().sum())
        out[label] = out[label] / max(int(member.to_numpy().sum()), 1) * 100
    return out


def pit_sources(pit_dir: Path) -> dict[str, int]:
    """Source counts from the pit cache's status file (empty if none)."""
    path = pit_dir / STATUS_FILE
    if not path.exists():
        return {}
    status = json.loads(path.read_text())
    counts: dict[str, int] = {}
    for entry in status.values():
        name = str(entry.get("source"))
        counts[name] = counts.get(name, 0) + 1
    return counts


def delisted_names(result) -> list[str]:
    trades = result.trades
    return sorted(set(trades.loc[trades["reason"] == DELISTED, "ticker"]))


def _fmt(value, digits: int) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def table(
    stats: dict[str, dict],
    chosen: str,
    mars: dict[str, float],
    biased: dict[str, dict] | None = None,
) -> str:
    extra = f" {BIASED} |" if biased is not None else ""
    head = "| Run | " + " | ".join(c[0] for c in COLUMNS) + " | CAGR/MaxDD |"
    lines = [head + extra, "|---|" + "---:|" * (len(COLUMNS) + 1 + bool(extra))]
    for name, s in stats.items():
        label = f"**{name} (chosen)**" if name == chosen else name
        cells = [_fmt(s.get(key), digits) for _, key, digits in COLUMNS]
        ratio = _fmt(mars.get(name), 2) if name in mars else "n/a"
        row = f"| {label} | " + " | ".join(cells) + f" | {ratio} |"
        if biased is not None:
            old = biased.get(name, {}).get("cagr_pct")
            row += f" {_fmt(old, 1) if name in biased else ''} |"
        lines.append(row)
    return "\n".join(lines)


def _names(names: list[str]) -> str:
    return ", ".join(names) or "none"


def markdown(report: dict) -> str:
    build, test, chosen = report["build"], report["test"], report["chosen"]
    build_mar = {name: mar(build[name]) for name in report["variants"]}
    test_mar = {name: mar(test[name]) for name in report["variants"]}
    cov, biased = report["coverage"], report["biased_test"]
    b, t = build[chosen], test[chosen]
    today = report["today_members_test"]
    sources = ", ".join(f"{k} {v}" for k, v in report["pit_sources"].items())
    exits = report["delisted_names"]
    return f"""# Ranked cross-sectional momentum (cycles 04 and 04b)

Generated {report["generated_at"]} by `scripts/run_momentum.py` in \
{report["runtime_seconds"]:.0f} s. Code: `backend/app/quant/momentum.py`, \
tests: `tests/test_momentum.py`. Cycle 04b replaced today's universe with \
point-in-time S&P 500 membership; the cycle 04 numbers are kept in the test \
table as "{BIASED}".

## Data

- Membership: `config/sp500_membership.csv`, one snapshot per month end from \
2015-12 plus the build day, {report["members"]} distinct tickers, built by \
`scripts/build_sp500_membership.py`. It starts from today's Wikipedia "List \
of S&P 500 companies" and walks back through the change log, which in 2026 \
moved from that page to "Historical components of the S&P 500". Joiners \
renamed later keep today's symbol (FLT is CPAY, CDAY is DAY); one change the \
log omits (SW for WRK, 2024-07-08) is added by hand. Replaying the log \
forward from 2015-12 gives today's list exactly.
- Universe: S&P 500 members only. Nasdaq-100 history was skipped, so the \
Nasdaq-100-only names of cycle 04 (ASML, MELI, PDD, SHOP and others) are out.
- Prices: current members from `data/raw` (yfinance, total-return adjusted \
via Adj Close / Close); removed members from `data/raw_pit` \
(`scripts/fetch_pit_prices.py`; {sources}). Alpaca bars are split and \
dividend adjusted and start 2016-01-04. Each member's bars are cut 45 days \
after it left. Spliced data excluded (`portfolio.suspect_spike`): \
{_names(report["suspect_data"])}. Members with no bars: \
{_names(report["missing_symbols"])}.
- Coverage: {cov["with_price"]:,} of {cov["member_months"]:,} member-months \
({cov["pct"]:.2f}%) have a bar on the rebalance day. With the full lookback: \
6-1 {cov["rankable_pct"]["6-1"]:.1f}%, 12-1 {cov["rankable_pct"]["12-1"]:.1f}%.
- Build {report["build_window"][0]} to {report["build_window"][1]}, test \
{report["test_window"][0]} to {report["test_window"][1]}. Each period starts \
at $10,000 at the close before its first day, and ranks use full history, so \
the test period starts warmed up.

## Rules

- Rebalance on the last trading day of each month using bars up to that \
close; trades fill at the next session's open. The unfinished last month \
is never a rebalance.
- Eligible: an S&P 500 member in that month's snapshot, a bar on the rank \
day, close >= $5, 20-day average dollar volume >= $20M, and the full lookback \
of history.
- Rank by total return from 252 (12-1) or 126 (6-1) bars ago to 21 bars ago.
- Hold the top N (10 or 20) at 1/N of equity each. A holding still eligible \
and ranked in the top 2N is kept; one that left the index is sold. Every \
holding is re-weighted to 1/N at each rebalance.
- Filter variant: SPY close below its 200-day SMA at the rebalance close \
means all cash until the next rebalance.
- Slippage {SLIPPAGE_PCT:.2%} per side on traded notional, no commission, \
fractional shares, cash earns 0, ${INITIAL_CAPITAL:,.0f} start.
- A holding with no bar on a day (buyout, merger, delisting) is sold at its \
last close less slippage ("delisted exits"). A holding with a close but no \
open on a rebalance day is kept at its last close until the next rebalance.
- Equal-weight universe: every member with a bar on the rebalance day, equal \
weight, same timing and costs. SPY buy and hold: adjusted close, no costs.
- Turnover per rebalance = max(buys, sells) at the open / equity at the \
open, averaged over rebalances (the first rebalance from cash counts 100%). \
Exposure = % of days holding anything; avg invested = long value / equity.

## Selection

Chosen on build-period CAGR / |max drawdown| (MAR), before looking at the \
test period: **{chosen}** (build MAR {mar(b):.2f}).

## Build period ({report["build_window"][0]} to {report["build_window"][1]})

{table(build, chosen, build_mar)}

## Test period ({report["test_window"][0]} to {report["test_window"][1]})

{table(test, chosen, test_mar, biased)}

Chosen variant on the test period: CAGR {_fmt(t["cagr_pct"], 1)}% \
(today's universe, cycle 04: {_fmt(biased[chosen]["cagr_pct"], 1)}%), total \
{_fmt(t["total_return_pct"], 1)}%, max drawdown \
{_fmt(t["max_drawdown_pct"], 1)}%, Sharpe {_fmt(t["sharpe"], 2)}; SPY: CAGR \
{_fmt(test[SPY_BH]["cagr_pct"], 1)}%, max drawdown \
{_fmt(test[SPY_BH]["max_drawdown_pct"], 1)}%. Calendar-year returns, chosen \
variant: build {_years(b)}; test {_years(t)}.

Where the gap comes from: on the test period the chosen variant made \
{_fmt(biased[chosen]["cagr_pct"], 1)}% a year on today's universe, \
{_fmt(today["stats"]["cagr_pct"], 1)}% on today's {today["tickers"]} S&P 500 \
members without the membership rule (Nasdaq-100-only names out, \
survivorship in) and {_fmt(t["cagr_pct"], 1)}% point-in-time.

Delisted exits, chosen variant: build \
{_names(exits.get(f"build {chosen}", []))}; test \
{_names(exits.get(f"test {chosen}", []))}.

The zscore row is copied from `data/reports/scorecard.json` (cycle 03, \
today's universe, so still survivorship-biased): it ran under different \
rules (whole shares, 1% risk per trade, 6 positions, 0.1% commission plus \
0.05% spread per leg, FX), and its Sharpe, turnover and holdings were not \
recorded, so it is shown as reported.

## Caveats

- The change log is Wikipedia's "selected changes", checked only for \
internal consistency (every walk step fits, the replay gives today's list, \
{report["membership_sizes"][0]}-{report["membership_sizes"][1]} names every \
month). A change it misses would leave a name in or out \
for the wrong months.
- Removed members have no bars before 2016-01-04 (Alpaca's start), so in \
2016 some of them could not be ranked while survivors could: a small \
survivorship bias left in the first build year, visible in the lookback \
coverage above.
- Alpaca follows a symbol to whoever keeps it. Splices found by scanning for \
one-day moves over 40% are cut (AA 2016-11, ARNC 2020-04, DWDP 2019-06, WRK \
before 2016-05-16, 21st Century Fox after 2019-03-19); smaller splices or \
spin-off adjustments could remain. XL has no bars before its 2016-07 \
redomicile.
- A delisted exit sells at the last close; cash or shares paid in a merger \
after that close are not modelled.
- Adjustment artefacts in `data/raw` (cycle 04 check): 10 cached series \
have a one-day jump of more than 10% in Adj Close / Close (spin-off \
adjustments, e.g. DHR 2016-07-05). One-off check (2026-11-24): MRNA has a \
+177% close on 2026-08-19 that was not verified; 6-1 N20 (with or without \
the filter) held it.
- The $5 floor is checked on adjusted closes, which sit below the traded \
price early in the history.
- Only 0.05% slippage per side is charged: no commission, FX or market \
impact. Fractional shares make every position exact on a $10k account.
- The test period is under four years; one path per variant and one \
train/test split. Eight variants were run, so the chosen one's build result \
is the best of eight.
"""


def run(args: argparse.Namespace) -> dict:
    started = time.time()
    membership = pd.read_csv(args.membership, parse_dates=["date"])
    frames, missing = load_members(args.data_dir, args.pit_dir, membership)
    spy = load_prices("SPY", data_dir=args.data_dir, allow_download=False)
    panel = build_panel(frames, spy, membership=membership)
    split = pd.Timestamp(args.split_date)
    last = panel.index[-1]
    windows = {
        "build": (pd.Timestamp(args.start), split - pd.Timedelta(days=1)),
        "test": (split, last),
    }
    selectors = {v.name: momentum_selector(panel, v) for v in VARIANTS}
    selectors[EQUAL_WEIGHT] = equal_weight_selector(panel)
    report: dict = {"build": {}, "test": {}, "delisted_names": {}}
    for period, (lo, hi) in windows.items():
        for name, selector in selectors.items():
            result = simulate(panel, selector, lo, hi)
            report[period][name] = run_stats(result)
            if name != EQUAL_WEIGHT:
                report["delisted_names"][f"{period} {name}"] = delisted_names(result)
        spy_result = buy_hold_result(panel.spy_close, lo, hi)
        report[period][SPY_BH] = run_stats(spy_result)
    for period, stats in zscore_rows(args.scorecard_json).items():
        report[period][ZSCORE] = {**stats, "sharpe": None}
    variants = [v.name for v in VARIANTS]
    chosen = choose_variant({n: report["build"][n] for n in variants})

    # Cycle 04's biased run: today's universe, no membership, test period.
    universe = json.loads(args.universe.read_text())
    old_frames, _, old_missing = load_universe(args.data_dir, universe["symbols"])
    old_panel = build_panel(old_frames, spy)
    lo, hi = windows["test"]
    report["biased_test"] = {
        v.name: run_stats(simulate(old_panel, momentum_selector(old_panel, v), lo, hi))
        for v in VARIANTS
    }
    sizes = membership.groupby("date").size()
    latest = membership.loc[membership["date"] == membership["date"].max(), "ticker"]
    today_frames = {t: old_frames[t] for t in latest if t in old_frames}
    today_frames.update({t: frames[t] for t in latest if t in frames})
    today_panel = build_panel(today_frames, spy)
    cfg = VARIANTS[variants.index(chosen)]
    report["today_members_test"] = {
        "tickers": len(today_panel.tickers),
        "stats": run_stats(
            simulate(today_panel, momentum_selector(today_panel, cfg), lo, hi)
        ),
    }
    report.update(
        chosen=chosen,
        selection_rule="max build-period CAGR / |max drawdown|",
        variants=variants,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        runtime_seconds=round(time.time() - started, 1),
        members=int(membership["ticker"].nunique()),
        membership_sizes=[int(sizes.min()), int(sizes.max())],
        tickers=len(panel.tickers),
        missing_symbols=missing,
        suspect_data=panel.suspect,
        coverage={
            **coverage(panel, membership, windows["build"][0], last),
            "rankable_pct": rankable(panel, windows["build"][0], last),
        },
        pit_sources=pit_sources(args.pit_dir),
        biased_universe_size=len(universe["symbols"]),
        biased_missing_symbols=old_missing,
        build_window=[d.date().isoformat() for d in windows["build"]],
        test_window=[d.date().isoformat() for d in windows["test"]],
    )
    report["build_window"][1] = report["build"][variants[0]]["end"]
    return report


def _parse(argv: list[str] | None) -> argparse.Namespace:
    from backend.app.config import get_settings

    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--data-dir", type=Path, default=settings.data_dir)
    parser.add_argument("--pit-dir", type=Path, default=ROOT / "data/raw_pit")
    parser.add_argument(
        "--membership", type=Path, default=ROOT / "config/sp500_membership.csv"
    )
    parser.add_argument("--universe", type=Path, default=settings.universe_path)
    parser.add_argument(
        "--scorecard-json", type=Path, default=ROOT / "data/reports/scorecard.json"
    )
    parser.add_argument("--markdown", type=Path, default=ROOT / "docs/momentum.md")
    parser.add_argument(
        "--json", type=Path, default=ROOT / "data/reports/momentum.json"
    )
    parser.add_argument("--start", default=BUILD_START)
    parser.add_argument("--split-date", default=SPLIT_DATE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> dict:
    args = _parse(argv)
    report = run(args)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2))
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown(report))
    print(f"chosen: {report['chosen']}; wrote {args.markdown} and {args.json}")
    return report


if __name__ == "__main__":
    main()
