#!/usr/bin/env python3
"""
Ranked cross-sectional momentum backtest over the cached universe (cycle 04).

Runs the 8 approved variants (12-1 / 6-1 lookback x top 10 / 20 x SPY 200-day
filter off / on), an equal-weight universe rebalanced monthly and SPY buy and
hold on the build period (2016 to the split) and the test period (the split to
the last cached bar), each from $10,000. The variant is chosen on build-period
CAGR / |max drawdown| only. Writes `docs/momentum.md` and
`data/reports/momentum.json`. Cached bars only: nothing is downloaded.

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

from backend.app.quant.momentum import (  # noqa: E402
    INITIAL_CAPITAL,
    SLIPPAGE_PCT,
    VARIANTS,
    build_panel,
    buy_hold_result,
    choose_variant,
    equal_weight_selector,
    mar,
    momentum_selector,
    run_stats,
    simulate,
)
from backend.app.quant.scorecard import BUILD_START, SPLIT_DATE  # noqa: E402
from scripts.run_scorecard import load_universe  # noqa: E402

EQUAL_WEIGHT = "Equal-weight universe"
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


def without_additions(frames, spy, added: list[str], cfg, windows) -> dict:
    """The chosen variant rerun without the names the latest universe sync
    added: a partial check on index-inclusion bias, not a selection input."""
    excluded = sorted(t for t in added if t in frames)
    panel = build_panel({t: f for t, f in frames.items() if t not in added}, spy)
    selector = momentum_selector(panel, cfg)
    out: dict = {"excluded": excluded}
    for period, (lo, hi) in windows.items():
        out[period] = run_stats(simulate(panel, selector, lo, hi))
    return out


def _fmt(value, digits: int) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def table(stats: dict[str, dict], chosen: str, mars: dict[str, float]) -> str:
    head = "| Run | " + " | ".join(c[0] for c in COLUMNS) + " | CAGR/MaxDD |"
    lines = [head, "|---|" + "---:|" * (len(COLUMNS) + 1)]
    for name, s in stats.items():
        label = f"**{name} (chosen)**" if name == chosen else name
        cells = [_fmt(s.get(key), digits) for _, key, digits in COLUMNS]
        ratio = _fmt(mars.get(name), 2) if name in mars else "n/a"
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {ratio} |")
    return "\n".join(lines)


def markdown(report: dict) -> str:
    build, test, chosen = report["build"], report["test"], report["chosen"]
    build_mar = {name: mar(build[name]) for name in report["variants"]}
    test_mar = {name: mar(test[name]) for name in report["variants"]}
    missing = ", ".join(report["missing_symbols"]) or "none"
    suspect = ", ".join(report["suspect_data"]) or "none"
    b, t = build[chosen], test[chosen]
    sens = report["sensitivity"]
    sens_rows = {
        f"{chosen}, build, without additions": sens["build"],
        f"{chosen}, test, without additions": sens["test"],
    }
    return f"""# Ranked cross-sectional momentum (cycle 04)

Generated {report["generated_at"]} by `scripts/run_momentum.py` in \
{report["runtime_seconds"]:.0f} s. Code: `backend/app/quant/momentum.py`, \
tests: `tests/test_momentum.py`.

## Data

- Daily cache `data/raw`, total-return adjusted (OHLC scaled by Adj Close / \
Close, as `load_prices` does), on SPY's trading calendar.
- Universe: {report["universe_size"]} symbols in `config/universe.json`; \
{report["tickers"]} used. Not cached: {missing}.
- Excluded as spliced data (`portfolio.suspect_spike`): {suspect}.
- Build {report["build_window"][0]} to {report["build_window"][1]}, test \
{report["test_window"][0]} to {report["test_window"][1]}. Each period starts \
at $10,000 at the close before its first day, and ranks use full history, so \
the test period starts warmed up.

## Rules

- Rebalance on the last trading day of each month using bars up to that \
close; trades fill at the next session's open. The unfinished last month \
is never a rebalance.
- Rank by total return from 252 (12-1) or 126 (6-1) bars ago to 21 bars ago.
- Eligible: a bar on the rank day, close >= $5, 20-day average dollar volume \
>= $20M, and the full lookback of history.
- Hold the top N (10 or 20) at 1/N of equity each. A holding still ranked in \
the top 2N is kept. Every holding is re-weighted to 1/N at each rebalance.
- Filter variant: SPY close below its 200-day SMA at the rebalance close \
means all cash until the next rebalance.
- Slippage {SLIPPAGE_PCT:.2%} per side on traded notional, no commission, \
fractional shares, cash earns 0, ${INITIAL_CAPITAL:,.0f} start.
- A holding with no bar on a day is sold at its last close less slippage \
("delisted exits").
- Equal-weight universe: every used ticker with a bar on the rebalance day, \
equal weight, same timing and costs. SPY buy and hold: adjusted close, \
no costs.
- Turnover per rebalance = max(buys, sells) at the open / equity at the \
open, averaged over rebalances (the first rebalance from cash counts 100%). \
Exposure = % of days holding anything; avg invested = long value / equity.

## Selection

Chosen on build-period CAGR / |max drawdown| (MAR), before looking at the \
test period: **{chosen}** (build MAR {mar(b):.2f}). The scorecard composite \
was not used: 70% of its weight is test-period data and most of its \
criteria are per-trade stats that do not fit a monthly rebalance.

## Build period ({report["build_window"][0]} to {report["build_window"][1]})

{table(build, chosen, build_mar)}

## Test period ({report["test_window"][0]} to {report["test_window"][1]})

{table(test, chosen, test_mar)}

Chosen variant on the test period: CAGR {_fmt(t["cagr_pct"], 1)}%, total \
{_fmt(t["total_return_pct"], 1)}%, max drawdown \
{_fmt(t["max_drawdown_pct"], 1)}%, Sharpe {_fmt(t["sharpe"], 2)} \
(SPY: CAGR {_fmt(test[SPY_BH]["cagr_pct"], 1)}%, max drawdown \
{_fmt(test[SPY_BH]["max_drawdown_pct"], 1)}%). Calendar-year returns, \
chosen variant: build {_years(b)}; test {_years(t)}.

The zscore row is copied from `data/reports/scorecard.json` (cycle 03): \
it ran under different rules (whole shares, 1% risk per trade, 6 positions, \
0.1% commission plus 0.05% spread per leg, FX), and its Sharpe, turnover and \
holdings were not recorded, so it is shown as reported.

## Chosen variant without the latest index additions

The universe is today's S&P 500 and Nasdaq-100. Names added to them recently \
were usually added because they had already risen, which is exactly what \
momentum buys. As a partial check, the chosen variant was rerun without the \
{len(sens["excluded"])} names the latest universe sync added \
({", ".join(sens["excluded"]) or "none"}). Earlier additions stay in, so \
this understates the bias. It is a check only, not a selection input.

{table(sens_rows, "", {})}

## Caveats

- Survivorship and index-inclusion bias: the universe is today's index \
members. Names that fell out, often after crashing, are missing; that hurts \
mean reversion (which buys falling names) more than momentum, which rarely \
holds a name through a long slide. But the opposite bias, names that joined \
the index after a big run, favours momentum directly: the check above \
removes only the latest additions. These results overstate what the rules \
would have earned by an unknown, probably large, amount. A point-in-time \
membership list is needed before trusting any of these numbers.
- Adjustment artefacts (one-off check, 2026-11-24): 10 cached series have a \
one-day jump of more than 10% in Adj Close / Close (spin-off adjustments, \
e.g. DHR 2016-07-05 +56%). No variant held a name across one, so none \
creates P&L, but each can distort that name's rank for up to a year.
- The $5 floor is checked on dividend-adjusted closes, which sit below the \
traded price early in the history, so it may have excluded a few names that \
a live run would have allowed.
- Only 0.05% slippage per side is charged: no commission, FX or market \
impact. Fractional shares make every position exact on a $10k account.
- The test period is under four years and dominated by a few themes (AI \
hardware and storage); one path per variant and one train/test split. \
Eight variants were run, so the chosen one's build result is the best of \
eight.
"""


def run(args: argparse.Namespace) -> dict:
    started = time.time()
    universe = json.loads(args.universe.read_text())
    symbols = universe["symbols"]
    frames, spy, missing = load_universe(args.data_dir, symbols)
    panel = build_panel(frames, spy)
    split = pd.Timestamp(args.split_date)
    last = panel.index[-1]
    windows = {
        "build": (pd.Timestamp(args.start), split - pd.Timedelta(days=1)),
        "test": (split, last),
    }
    selectors = {v.name: momentum_selector(panel, v) for v in VARIANTS}
    selectors[EQUAL_WEIGHT] = equal_weight_selector(panel)
    report: dict = {"build": {}, "test": {}}
    for period, (lo, hi) in windows.items():
        for name, selector in selectors.items():
            result = simulate(panel, selector, lo, hi)
            report[period][name] = run_stats(result)
        spy_result = buy_hold_result(panel.spy_close, lo, hi)
        report[period][SPY_BH] = run_stats(spy_result)
    for period, stats in zscore_rows(args.scorecard_json).items():
        report[period][ZSCORE] = {**stats, "sharpe": None}
    variants = [v.name for v in VARIANTS]
    chosen = choose_variant({n: report["build"][n] for n in variants})
    added = (universe.get("last_changes") or {}).get("added") or []
    report["sensitivity"] = without_additions(
        frames, spy, added, VARIANTS[variants.index(chosen)], windows
    )
    report.update(
        chosen=chosen,
        selection_rule="max build-period CAGR / |max drawdown|",
        variants=variants,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        runtime_seconds=round(time.time() - started, 1),
        universe_size=len(symbols),
        tickers=len(panel.tickers),
        missing_symbols=missing,
        suspect_data=panel.suspect,
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
