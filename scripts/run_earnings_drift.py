#!/usr/bin/env python3
"""
Earnings-announcement-return drift backtest (cycle 06, backtest 2).

Step 1 (`--fetch`, network): map every point-in-time S&P 500 member to its
SEC CIK(s), read their 8-K Item 2.02 filings from EDGAR and write
`data/edgar/events.parquet` and `data/edgar/ciks.csv` (cached JSON under
`data/edgar/`, gitignored). Without `--fetch` the cache is used offline.

Step 2: EAR deciles, the long-only and long-short strategies, the event study
and benchmarks on the build (2016-2022) and test (2023 on) periods. Writes
`docs/earnings-drift.md` and `data/reports/earnings_drift.json`. Cached bars
only.

    python scripts/run_earnings_drift.py --fetch
    python scripts/run_earnings_drift.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backend.app.data import edgar  # noqa: E402
from backend.app.data.loader import load_prices  # noqa: E402
from backend.app.quant import earnings_drift as ed  # noqa: E402
from backend.app.quant.momentum import (  # noqa: E402
    TRADE_COLUMNS,
    MomentumResult,
    build_panel,
    buy_hold_result,
    run_stats,
    trim_to_membership,
)
from backend.app.quant.scorecard import BUILD_START, SPLIT_DATE  # noqa: E402

#: Bars kept after a member's last monthly snapshot: a 60-session hold
#: entered in its last member month ends within about 120 days. Later bars
#: are dropped (the symbol may be reused by another company).
TRIM_DAYS = 120
LONG_ONLY, LONG_SHORT = "Long-only top decile", "Long-short top/bottom decile"
SPY_BH, SPY_MATCHED = "SPY buy & hold", "SPY exposure-matched"

log = logging.getLogger("earnings_drift")


def fetch_events(
    membership: pd.DataFrame, calendar: pd.DatetimeIndex, cache_dir: Path, offline: bool
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    client = edgar.EdgarClient(cache_dir, offline=offline)
    resolved = edgar.resolve_ciks(membership, client)
    events = edgar.build_events(resolved, client, calendar)
    resolved.to_csv(cache_dir / "ciks.csv", index=False)
    events.to_parquet(cache_dir / "events.parquet")
    return resolved, events, client.requests


def load_members(
    data_dir: Path, pit_dir: Path, membership: pd.DataFrame
) -> tuple[dict[str, pd.DataFrame], list[str]]:
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
    return trim_to_membership(frames, membership, days=TRIM_DAYS), missing


def matched_stats(spy_close: pd.Series, result: ed.EventResult) -> dict:
    """SPY at the strategy's net invested fraction, as `run_stats`."""
    equity = result.equity
    invested = result.invested.reindex(equity.index).fillna(0.0)
    curve = ed.exposure_matched(spy_close.reindex(equity.index), invested, 10_000.0)
    return run_stats(
        MomentumResult(
            equity=curve,
            cash=curve * 0,
            invested=invested.iloc[1:],
            holdings=(invested.iloc[1:] != 0).astype(float),
            trades=pd.DataFrame(columns=TRADE_COLUMNS),
            turnover=pd.Series(dtype=float),
        )
    )


def per_event(table: pd.DataFrame, result: ed.EventResult) -> dict:
    """Entered events: count, and mean 60-session abnormal return vs SPY
    (day +2 open to day +61 close, full windows) per side."""
    rows = table.loc[result.entered]
    out = {"events": int(len(rows))}
    for label, side in ((ed.TOP, "long"), (ed.BOTTOM, "short")):
        done = rows[(rows["signal"] == label) & rows["complete"]]["bhar"]
        out[f"{side}_events"] = int((rows["signal"] == label).sum())
        out[f"{side}_abnormal_pct"] = float(done.mean() * 100) if len(done) else None
    long_ar = out["long_abnormal_pct"]
    # Per-side cost at which a long trade's mean abnormal return is zero.
    out["breakeven_bp_per_side"] = long_ar * 100 / 2 if long_ar is not None else None
    return out


def run(args: argparse.Namespace, events: pd.DataFrame, membership) -> dict:
    started = time.time()
    frames, missing = load_members(args.data_dir, args.pit_dir, membership)
    spy = load_prices("SPY", data_dir=args.data_dir, allow_download=False)
    panel = build_panel(frames, spy)
    prices = ed.Prices(
        open=panel.open,
        close=panel.close,
        spy_open=spy["Open"].astype(float),
        spy_close=spy["Close"].astype(float),
    )
    table = ed.event_table(events, prices)
    table["member"] = ed.member_at_day0(table, membership)
    table["signal"] = ed.classify(table)
    index = panel.index
    windows = {
        "build": (
            pd.Timestamp(BUILD_START),
            pd.Timestamp(SPLIT_DATE) - pd.Timedelta(days=1),
        ),
        "test": (pd.Timestamp(SPLIT_DATE), index[-1]),
    }
    report: dict = {"periods": {}, "event_study": {}}
    for period, (lo, hi) in windows.items():
        a, b = ed.window(index, lo, hi)
        rows: dict[str, dict] = {}
        for name, long_short in ((LONG_ONLY, False), (LONG_SHORT, True)):
            result = ed.simulate(table, prices, a, b, long_short=long_short)
            rows[name] = {**ed.period_stats(result), **per_event(table, result)}
            if not long_short:
                rows[SPY_MATCHED] = matched_stats(prices.spy_close, result)
            else:
                rows[f"{SPY_MATCHED} (long-short, net)"] = matched_stats(
                    prices.spy_close, result
                )
        rows[SPY_BH] = run_stats(buy_hold_result(prices.spy_close, lo, hi))
        report["periods"][period] = rows
        report["event_study"][period] = ed.event_study(table, lo, hi)
    report["event_study"]["all"] = ed.event_study(table, windows["build"][0], index[-1])

    in_sample = table[pd.to_datetime(table["day0"]) >= windows["build"][0]]
    last_quarter_end = (index[-1] + pd.Timedelta(days=1)).to_period("Q") - 1
    report.update(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        runtime_seconds=round(time.time() - started, 1),
        windows={
            k: [v[0].date().isoformat(), v[1].date().isoformat()]
            for k, v in windows.items()
        },
        events_total=int(len(events)),
        events_member=int(in_sample["member"].sum()),
        events_with_ear=int((in_sample["member"] & in_sample["ear"].notna()).sum()),
        events_classified=int(in_sample["signal"].notna().sum()),
        signal_counts={
            k: int(v) for k, v in in_sample["signal"].value_counts().items()
        },
        delisted_events=int(
            (in_sample["delisted"] & in_sample["signal"].notna()).sum()
        ),
        missing_prices=missing,
        suspect_data=panel.suspect,
        coverage=edgar.member_quarter_coverage(
            events, membership, BUILD_START, last_quarter_end.end_time.normalize()
        ),
        first_classified=str(
            pd.to_datetime(table.loc[table["signal"].notna(), "day0"]).min().date()
        ),
    )
    return report


def _fmt(value, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "n/a"
    return f"{value:.{digits}f}"


STRATEGY_COLUMNS = [
    ("CAGR %", "cagr_pct", 1),
    ("Max DD %", "max_drawdown_pct", 1),
    ("Sharpe", "sharpe", 2),
    ("Pos. years %", "positive_years_pct", 0),
    ("Events", "events", 0),
    ("Long AR60 %", "long_abnormal_pct", 2),
    ("Short AR60 %", "short_abnormal_pct", 2),
    ("Avg holdings", "avg_holdings", 1),
    ("Avg gross invested %", "avg_invested_pct", 1),
    ("Delisted exits", "delisted_exits", 0),
]


def strategy_table(rows: dict[str, dict]) -> str:
    head = "| Run | " + " | ".join(c[0] for c in STRATEGY_COLUMNS) + " |"
    lines = [head, "|---|" + "---:|" * len(STRATEGY_COLUMNS)]
    for name, s in rows.items():
        cells = [_fmt(s.get(key), d) for _, key, d in STRATEGY_COLUMNS]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def study_table(study: dict) -> str:
    lines = [
        "| Period | Group | Events | Mean AR % | t | Months | t (monthly) | % > 0 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for period, groups in study.items():
        for label in (ed.TOP, ed.BOTTOM, "all_classified"):
            g = groups[label]
            lines.append(
                f"| {period} | {label} | {g['n']} | {_fmt(g['mean_pct'], 2)} | "
                f"{_fmt(g['t'], 2)} | {g['months']} | {_fmt(g['t_monthly'], 2)} | "
                f"{_fmt(g['positive_pct'], 0)} |"
            )
    return "\n".join(lines)


def _gap(groups: dict, label: str) -> str:
    a, b = groups[label]["mean_pct"], groups["all_classified"]["mean_pct"]
    return _fmt(a - b, 2) if a is not None and b is not None else "n/a"


def _years(stats: dict) -> str:
    years = stats.get("yearly_returns_pct") or {}
    return ", ".join(f"{y}: {_fmt(v)}%" for y, v in years.items())


def markdown(report: dict, resolved: pd.DataFrame) -> str:
    cov = report["coverage"]
    build, test = report["periods"]["build"], report["periods"]["test"]
    sources = resolved["source"].value_counts().to_dict()
    unresolved = sorted(resolved.loc[resolved["cik"].isna(), "ticker"])
    worst = sorted(cov["missing"].items(), key=lambda kv: -kv[1])[:15]
    bw, tw = report["windows"]["build"], report["windows"]["test"]
    study = report["event_study"]
    return f"""# Earnings-announcement-return drift (cycle 06, backtest 2)

Generated {report["generated_at"]} by `scripts/run_earnings_drift.py` in \
{report["runtime_seconds"]:.0f} s. Code: `backend/app/data/edgar.py`, \
`backend/app/quant/earnings_drift.py`; tests: `tests/test_earnings_drift.py`. \
Rules are the pre-registered ones in \
`docs/research/strategy-evidence-2026-11.md`, run once with no tuning.

## Data

- Earnings dates: SEC EDGAR 8-K filings listing Item 2.02 (original 8-Ks, \
not 8-K/A), from the submissions JSON and its paged older files, cached in \
`data/edgar/` (gitignored). User-Agent "{edgar.USER_AGENT}", at most 8 \
requests a second.
- Tickers to CIKs ({", ".join(f"{k} {v}" for k, v in sources.items())}): \
current members from `company_tickers.json`; removed members by EDGAR \
full-text search for "NYSE: XYZ" / "NASDAQ: XYZ" in 8-Ks filed during their \
index years, taking the CIK that filed most of the Item 2.02 hits (at least 2 \
and half of them). A CIK that first reports more than 120 days after it is \
needed gets a predecessor found the same way (holding-company \
reorganisations). Search misses were filled by hand, each checked against \
the EDGAR name and former names: `edgar.CIK_OVERRIDES` (14 removed members, \
mostly second share classes) and `edgar.PREDECESSORS` (XOM, LIN, VTRS, \
SKYD, whose old CIK or symbol differs). A removed member search cannot \
resolve uses today's owner of the symbol if it filed an Item 2.02 during the \
member span ("current-verified"). Unresolved: \
{", ".join(unresolved) or "none"} (FRC and SBNY were banks filing with the \
FDIC, not the SEC; AMTM was a member for two months).
- Day 0: `acceptanceDateTime` (UTC) on New York time; at or after 16:00, or \
not a session, the next session. One event per ticker and day 0. \
{report["events_total"]:,} events from 2015.
- Coverage: {cov["with_event"]:,} of {cov["member_quarters"]:,} \
member-quarters ({cov["pct"]:.1f}%) have at least one 8-K Item 2.02 with \
day 0 in the quarter (members in the quarter's last snapshot, \
{bw[0]} to the last full quarter). Most quarters missing: \
{", ".join(f"{t} {n}" for t, n in worst) or "none"}.
- Prices as in the momentum backtest: `data/raw` (total-return adjusted) and \
`data/raw_pit` for removed members, each cut {TRIM_DAYS} days after its last \
monthly snapshot. Spliced series excluded: \
{", ".join(report["suspect_data"]) or "none"}. No bars: \
{", ".join(report["missing_prices"]) or "none"}.
- From {bw[0]}: {report["events_member"]:,} member events, \
{report["events_with_ear"]:,} with an EAR, signals \
{", ".join(f"{k} {v:,}" for k, v in report["signal_counts"].items())}.

## Rules (pre-registered)

- EAR = the product over days -1, 0, +1 of (1 + stock return - SPY return), \
minus 1, on closes.
- An event counts only if the ticker is in the S&P 500 snapshot of day 0's \
month.
- At the day +2 open each EAR is compared with the member EARs whose day +1 \
fell in the previous 252 sessions: above their 90th percentile is the top \
decile, below the 10th the bottom. A pool under {ed.MIN_POOL} EARs \
classifies nothing; 2015 events use the 2015-12-31 list and only fill the \
pool, so the first classified day 0 is {report["first_classified"]}.
- Long-only: buy top-decile events at the day +2 open, 1/20 of the previous \
close's equity each, at most 20 held, largest EAR first when slots are short, \
a ticker already held skipped. Long-short: also short bottom-decile events \
(at most 20 shorts). **Borrow cost is not modelled** for the shorts.
- Exit at the close of day +61 (60 sessions held). A stock with no bar \
(delisted, acquired) exits at its last close.
- {ed.COST_PER_SIDE * 1e4:.0f}bp per side, fractional shares, cash earns 0, \
$10,000 at the start of each period.
- AR60: buy-and-hold abnormal return from the day +2 open to the day +61 close \
minus SPY's over the same span (gross of costs), averaged over the strategy's \
entered events with a full window. Break-even cost = long AR60 / 2 per side.
- Exposure-matched SPY holds SPY at the strategy's net invested fraction \
(long minus short value over equity) at the prior close.

## Event study (all classified member events, not just those traded)

{study_table(report["event_study"])}

`t` treats events as independent; their 60-session windows overlap, so \
`t (monthly)` (the t-stat of monthly cohort means) is the more honest one. \
Abnormal returns are against cap-weighted SPY, so they include the \
equal-weight member gap (all classified events: build \
{_fmt(study["build"]["all_classified"]["mean_pct"], 2)}%, test \
{_fmt(study["test"]["all_classified"]["mean_pct"], 2)}%). Top minus all \
classified: build {_gap(study["build"], ed.TOP)}pp, test \
{_gap(study["test"], ed.TOP)}pp; bottom minus all: build \
{_gap(study["build"], ed.BOTTOM)}pp, test {_gap(study["test"], ed.BOTTOM)}pp.

## Build period ({bw[0]} to {bw[1]})

{strategy_table(build)}

## Test period ({tw[0]} to {tw[1]})

{strategy_table(test)}

Break-even cost per side (long leg): build \
{_fmt(build[LONG_ONLY]["breakeven_bp_per_side"])}bp, test \
{_fmt(test[LONG_ONLY]["breakeven_bp_per_side"])}bp. Calendar years, \
long-only: build {_years(build[LONG_ONLY])}; test {_years(test[LONG_ONLY])}. \
Long-short: build {_years(build[LONG_SHORT])}; test {_years(test[LONG_SHORT])}.

## Caveats

- Martineau (2022) finds the drift is zero in large caps since 2006; this is \
the hardest universe for it.
- An 8-K Item 2.02 is usually the quarterly release but can be a preliminary \
result or guidance update; all are kept as events. Companies with two share \
classes in the index (GOOGL/GOOG, FOXA/FOX, NWSA/NWS) count once per class.
- Ticker-to-CIK matching by full-text search was checked only by its rule \
(share of earnings hits), not company by company.
- A missing bar mid-hold also counts as a delisted exit at the last close; \
merger consideration after the last close is not modelled.
- The short leg needs borrow; hard-to-borrow names are likely \
over-represented in the bottom decile.
- Removed members have no bars before 2016-01-04, so the 2015 events that \
fill the first trailing pool are survivors only.
- One path, one split; the test period is under four years.
"""


def _parse(argv: list[str] | None) -> argparse.Namespace:
    from backend.app.config import get_settings

    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--data-dir", type=Path, default=settings.data_dir)
    parser.add_argument("--pit-dir", type=Path, default=ROOT / "data/raw_pit")
    parser.add_argument("--edgar-dir", type=Path, default=ROOT / "data/edgar")
    parser.add_argument(
        "--membership", type=Path, default=ROOT / "config/sp500_membership.csv"
    )
    parser.add_argument("--fetch", action="store_true", help="query EDGAR")
    parser.add_argument("--doc", type=Path, default=ROOT / "docs/earnings-drift.md")
    parser.add_argument(
        "--report-json", type=Path, default=ROOT / "data/reports/earnings_drift.json"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse(argv)
    membership = pd.read_csv(args.membership, parse_dates=["date"])
    spy = load_prices("SPY", data_dir=args.data_dir, allow_download=False)
    calendar = pd.DatetimeIndex(spy.index)
    args.edgar_dir.mkdir(parents=True, exist_ok=True)
    resolved, events, n = fetch_events(
        membership, calendar, args.edgar_dir, offline=not args.fetch
    )
    log.info("EDGAR requests %d, events %d", n, len(events))
    report = run(args, events, membership)
    report["cik_sources"] = resolved["source"].value_counts().to_dict()
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, indent=1, default=str))
    args.doc.write_text(markdown(report, resolved))
    log.info("wrote %s and %s", args.doc, args.report_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
