#!/usr/bin/env python3
"""
Momentum variants against the 6-1 top-10 baseline on point-in-time S&P 500
membership (cycle 04c).

Runs the baseline, five published variants
(`backend/app/quant/momentum_variants.py`) and SPY buy and hold on the build
period (2016 to the split) and the test period (the split to the last cached
bar), each from $10,000. The winner is picked on build-period Sharpe (ties:
shallower max drawdown) before the test numbers are read. Writes
`docs/momentum-variants.md` and `data/reports/momentum_variants.json`.
Cached bars only.

    python scripts/run_momentum_variants.py
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
from backend.app.quant.momentum import (  # noqa: E402
    build_panel,
    buy_hold_result,
    eligibility,
    momentum_selector,
    month_end_mask,
    run_stats,
    simulate,
)
from backend.app.quant.momentum_variants import (  # noqa: E402
    BASELINE,
    CANDIDATES,
    VOL_SCALED,
    candidate_selectors,
    choose_by_sharpe,
    residual_scores,
)
from backend.app.quant.scorecard import BUILD_START, SPLIT_DATE  # noqa: E402
from scripts.run_momentum import load_members  # noqa: E402

SPY_BH = "SPY buy & hold"

COLUMNS = [
    ("CAGR %", "cagr_pct", 1),
    ("Max DD %", "max_drawdown_pct", 1),
    ("Sharpe", "sharpe", 2),
    ("Pos. years %", "positive_years_pct", 0),
    ("Turnover %/mo", "monthly_turnover_pct", 1),
    ("Avg invested %", "avg_invested_pct", 1),
]


def _fmt(value, digits: int) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def table(stats: dict[str, dict], winner: str) -> str:
    head = "| Run | " + " | ".join(c[0] for c in COLUMNS) + " |"
    lines = [head, "|---|" + "---:|" * len(COLUMNS)]
    for name, s in stats.items():
        label = f"**{name} (winner)**" if name == winner else name
        cells = [_fmt(s.get(key), digits) for _, key, digits in COLUMNS]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def years(stats: dict) -> str:
    out = stats.get("yearly_returns_pct") or {}
    return ", ".join(f"{y}: {_fmt(v, 1)}%" for y, v in out.items())


def residual_coverage(panel, start, end) -> dict[str, float]:
    """% of baseline-eligible month-end names with a residual score, by year."""
    days = panel.index[month_end_mask(panel.index)]
    days = days[(days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end))]
    ok = eligibility(panel, BASELINE).loc[days]
    has = residual_scores(panel).loc[days].notna() & ok
    by_year = has.sum(axis=1).groupby(days.year).sum()
    total = ok.sum(axis=1).groupby(days.year).sum()
    return {str(y): round(float(by_year[y] / total[y] * 100), 1) for y in total.index}


def verdict(report: dict) -> str:
    beats = [c for c, ok in report["beats_spy_sharpe_both"].items() if ok]
    if not beats:
        return (
            "No candidate beats SPY buy and hold on Sharpe in both periods, "
            "so none beats it on a risk-adjusted basis."
        )
    return "Beats SPY on Sharpe in both periods: " + ", ".join(beats) + "."


def markdown(report: dict) -> str:
    build, test, winner = report["build"], report["test"], report["winner"]
    base = BASELINE.name
    w_b, w_t = build[winner], test[winner]
    cov = report["residual_coverage_pct"]
    cov_text = ", ".join(f"{y} {v:.0f}%" for y, v in cov.items())
    scale = report["vol_scale_avg_invested"]
    return f"""# Momentum variants on point-in-time S&P 500 (cycle 04c)

Generated {report["generated_at"]} by `scripts/run_momentum_variants.py` in \
{report["runtime_seconds"]:.0f} s. Code: \
`backend/app/quant/momentum_variants.py`, tests: \
`tests/test_momentum_variants.py`. Data, universe, costs and timing are those \
of `docs/momentum.md` (cycle 04b): point-in-time S&P 500 membership, \
{report["tickers"]} priced tickers, decisions at the month-end close, fills \
at the next open, 0.05% slippage per side, fractional shares, $10,000 start.

## Candidates

The baseline is **{base}**: 6-1 return (126 to 21 bars), top 10, equal \
weight, held names kept while in the top 20, no market filter. Every \
candidate uses the baseline's eligibility (member that month, close >= $5, \
$20M 20-day dollar volume, full 6-1 history) and holds 10 names. Published \
parameters, no tuning: nothing was varied on the build period, so each \
candidate is one run.

1. **{CANDIDATES[0]}** (Barroso and Santa-Clara 2015): the baseline's \
weights times min(1, 15% / annualised realised vol of the unscaled \
baseline's daily returns over the last 126 days, known at the rebalance \
close); the rest in cash, no leverage. The reference run starts with the \
membership data (first fill 2016-01-04); before 21 days of returns the scale \
is 1.0. Average invested: build {scale["build"]:.1f}%, test \
{scale["test"]:.1f}%.
2. **{CANDIDATES[1]}** (Da, Gurun and Warachka 2014): among the top 20 by \
6-1 return, rank on sign(6-1 return) x (% up days - % down days) over the \
same 105 daily returns, hold the top 10 (no extra hold buffer: the top-20 \
pool is the filter).
3. **{CANDIDATES[2]}** (Blitz, Huij and Martens 2011): each month end, \
regress up to 36 monthly returns (to that month end) on SPY's; score = sum \
of the residuals of the 5 months from 6 to 1 months ago / their standard \
deviation. Needs 12 monthly returns (not 36: prices start 2015-01 for \
current members and 2016-01 for removed ones). Hold buffer as the baseline.
4. **{CANDIDATES[3]}** (George and Hwang 2004): close / highest close of the \
last 252 bars (adjusted closes), ties by 6-1 return. Hold buffer as the \
baseline.
5. **{CANDIDATES[4]}**: the baseline's 10 names weighted by 1 / standard \
deviation of the last 60 daily returns.

Turnover = max(buys, sells) at the open / equity, averaged over monthly \
rebalances; vol-scaled and inverse-vol turnover includes re-weighting.

## Selection

Winner on build-period Sharpe (ties: shallower max drawdown), chosen before \
reading the test period: **{winner}** (build Sharpe \
{_fmt(w_b["sharpe"], 2)}; baseline {_fmt(build[base]["sharpe"], 2)}).

## Build period ({report["build_window"][0]} to {report["build_window"][1]})

{table(build, winner)}

## Test period ({report["test_window"][0]} to {report["test_window"][1]}), \
reported once

{table(test, winner)}

Winner on the test period: CAGR {_fmt(w_t["cagr_pct"], 1)}%, max drawdown \
{_fmt(w_t["max_drawdown_pct"], 1)}%, Sharpe {_fmt(w_t["sharpe"], 2)}; \
baseline Sharpe {_fmt(test[base]["sharpe"], 2)}; SPY Sharpe \
{_fmt(test[SPY_BH]["sharpe"], 2)}. Calendar-year returns, winner: build \
{years(w_b)}; test {years(w_t)}.

{verdict(report)} Build-period Sharpe: SPY {_fmt(build[SPY_BH]["sharpe"], 2)}, \
best candidate {_fmt(w_b["sharpe"], 2)}.

## Caveats

- All caveats of `docs/momentum.md` apply (Wikipedia change log, removed \
members without bars before 2016-01-04, Alpaca symbol splices, delisted \
exits at the last close, slippage only).
- Residual momentum coverage, share of baseline-eligible member-months with \
a residual score: {cov_text}. Removed members need 12 monthly returns from \
2016-01, so in 2016 only names with 2015 prices (today's members) can be \
ranked: a survivorship tilt in the first build year.
- Six runs on one build/test split; the winner's build Sharpe is the best \
of five, so it is biased upward. The test period is under four years.
- Sharpe uses daily returns with a zero risk-free rate (`metrics.py`); cash \
in the vol-scaled run earns nothing, which understates it when rates are \
positive.
"""


def run(args: argparse.Namespace) -> dict:
    started = time.time()
    membership = pd.read_csv(args.membership, parse_dates=["date"])
    frames, missing = load_members(args.data_dir, args.pit_dir, membership)
    spy = load_prices("SPY", data_dir=args.data_dir, allow_download=False)
    panel = build_panel(frames, spy, membership=membership)
    split = pd.Timestamp(args.split_date)
    windows = {
        "build": (pd.Timestamp(args.start), split - pd.Timedelta(days=1)),
        "test": (split, panel.index[-1]),
    }
    selectors = {BASELINE.name: momentum_selector(panel, BASELINE)}
    selectors.update(candidate_selectors(panel))
    report: dict = {"build": {}, "test": {}, "vol_scale_avg_invested": {}}
    for period, (lo, hi) in windows.items():
        for name, selector in selectors.items():
            result = simulate(panel, selector, lo, hi)
            report[period][name] = run_stats(result)
            if name == VOL_SCALED:
                avg = float(result.invested.mean() * 100)
                report["vol_scale_avg_invested"][period] = round(avg, 1)
        report[period][SPY_BH] = run_stats(buy_hold_result(panel.spy_close, lo, hi))
    winner = choose_by_sharpe({c: report["build"][c] for c in CANDIDATES})
    spy_sharpe = {p: report[p][SPY_BH]["sharpe"] or 0.0 for p in windows}
    report.update(
        winner=winner,
        selection_rule="max build-period Sharpe; ties: shallower max drawdown",
        candidates=CANDIDATES,
        baseline=BASELINE.name,
        beats_spy_sharpe_both={
            c: all((report[p][c]["sharpe"] or 0.0) > spy_sharpe[p] for p in windows)
            for c in CANDIDATES
        },
        residual_coverage_pct=residual_coverage(panel, *windows["build"]),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        runtime_seconds=round(time.time() - started, 1),
        tickers=len(panel.tickers),
        missing_symbols=missing,
        build_window=[d.date().isoformat() for d in windows["build"]],
        test_window=[d.date().isoformat() for d in windows["test"]],
    )
    report["build_window"][1] = report["build"][BASELINE.name]["end"]
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
    parser.add_argument(
        "--markdown", type=Path, default=ROOT / "docs/momentum-variants.md"
    )
    parser.add_argument(
        "--json", type=Path, default=ROOT / "data/reports/momentum_variants.json"
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
    print(f"winner: {report['winner']}; wrote {args.markdown} and {args.json}")
    return report


if __name__ == "__main__":
    main()
