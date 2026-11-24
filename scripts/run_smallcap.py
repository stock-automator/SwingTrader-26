#!/usr/bin/env python3
"""
Small/mid-cap backtest (cycle 07b), rules pre-registered in
`docs/research/strategy-evidence-2026-11.md`.

    python scripts/run_smallcap.py download [--limit N]   # Alpaca, resumable
    python scripts/run_smallcap.py classify               # EDGAR, cached
    python scripts/run_smallcap.py download --unadjusted  # traded prices
    python scripts/run_smallcap.py run                    # cached data only

`download` writes `data/raw_broad/` (gitignored): `_assets.parquet`, one
parquet per symbol and `_status.json`. `classify` writes
`data/raw_broad/_classified.csv` and `data/raw_broad/_events.parquet` (EDGAR
JSON cached under `data/edgar/`). `run` writes `docs/smallcap.md` and
`data/reports/smallcap.json`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backend.app.data import broad_universe as bu  # noqa: E402
from backend.app.data import edgar  # noqa: E402
from backend.app.quant import earnings_drift as ed  # noqa: E402
from backend.app.quant import smallcap as sc  # noqa: E402
from backend.app.quant.momentum import buy_hold_result, month_end_mask  # noqa: E402
from backend.app.quant.scorecard import SPLIT_DATE  # noqa: E402

RAW = ROOT / "data/raw_broad"
ETF = ROOT / "data/raw_etf"
EDGAR_CACHE = ROOT / "data/edgar"
CLASSIFIED_FILE = "_classified.csv"
UNADJUSTED_DIR = "unadjusted"
EVENTS_FILE = "_events.parquet"
REPORT_JSON = ROOT / "data/reports/smallcap.json"
DOC = ROOT / "docs/smallcap.md"
FIRST_RUN = ROOT / "data/reports/smallcap_first_run.json"
REVERSAL, MOMENTUM, EARNINGS, COMPOSITE = (
    "1m reversal (bottom decile)",
    "6-1 momentum (top decile)",
    "Earnings drift (EAR top decile)",
    "Composite (top 20)",
)
EW, IWM, SPY = "Equal-weight universe", "IWM buy & hold", "SPY buy & hold"
log = logging.getLogger("smallcap")


def _downloader(adjustment: str = "all") -> bu.BarsDownloader:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY missing from .env")
    return bu.BarsDownloader(key, secret, adjustment=adjustment)


def download_unadjusted(args: argparse.Namespace) -> int:
    """Unadjusted bars of the classified common stocks, for the $5 price
    test on the price that traded (adjusted prices of later reverse-split
    stocks are far above it)."""
    dl = _downloader("raw")
    classified = pd.read_csv(args.raw / CLASSIFIED_FILE)
    tickers = classified.loc[classified["common"].astype(bool), "ticker"]
    symbols = sorted(t.replace("-", ".") for t in tickers)
    status = dl.download(symbols, args.raw / UNADJUSTED_DIR)
    done = sum(s in status for s in symbols)
    log.info("%d / %d unadjusted done, %d requests", done, len(symbols), dl.requests)
    return 0 if done == len(symbols) else 1


def download(args: argparse.Namespace) -> int:
    if args.unadjusted:
        return download_unadjusted(args)
    dl = _downloader()
    assets_path = args.raw / bu.ASSETS_FILE
    if assets_path.exists():
        assets = pd.read_parquet(assets_path)
    else:
        assets = dl.assets()
        args.raw.mkdir(parents=True, exist_ok=True)
        keep = ["symbol", "name", "exchange", "status", "cusip"]
        assets[[c for c in keep if c in assets]].to_parquet(assets_path)
    cands = bu.candidates(assets)
    log.info(
        "%d assets, %d candidates (%d listed, %d OTC)",
        len(assets),
        len(cands),
        int(cands["listed"].sum()),
        int((~cands["listed"]).sum()),
    )
    symbols = sorted(cands["symbol"].unique())
    if args.limit:
        symbols = symbols[: args.limit]
    started, before = time.time(), dl.requests
    status = dl.download(symbols, args.raw)
    took = time.time() - started
    done = sum(s in status for s in symbols)
    log.info(
        "%d / %d symbols done, %d requests, %.0f s",
        done,
        len(symbols),
        dl.requests - before,
        took,
    )
    return 0 if done == len(symbols) else 1


def _bars(raw: Path, symbol: str) -> pd.DataFrame:
    return pd.read_parquet(raw / f"{bu.file_symbol(symbol)}.parquet")


def _ever_eligible(df: pd.DataFrame) -> bool:
    """A generous pre-screen: some session with close >= $5 and a 20-day
    dollar volume >= $5M (the exact monthly test runs later)."""
    adv = (df["Close"] * df["Volume"]).rolling(sc.ADV_DAYS).mean()
    return bool(((df["Close"] >= sc.MIN_PRICE) & (adv >= sc.MIN_ADV)).any())


def classify(args: argparse.Namespace) -> int:
    status = json.loads((args.raw / bu.STATUS_FILE).read_text())
    spans, screened_out = [], 0
    for symbol, entry in sorted(status.items()):
        if not entry["bars"]:
            continue
        if not _ever_eligible(_bars(args.raw, symbol)):
            screened_out += 1
            continue
        spans.append((bu.file_symbol(symbol), entry["first"], entry["last"]))
    spans = pd.DataFrame(spans, columns=["ticker", "first", "last"])
    spans[["first", "last"]] = spans[["first", "last"]].apply(pd.to_datetime)
    log.info(
        "%d symbols with bars ever pass $5 / $5M, %d never do",
        len(spans),
        screened_out,
    )
    client = edgar.EdgarClient(EDGAR_CACHE)
    classified = bu.classify(spans, client)
    classified = classified.merge(spans, on="ticker")
    classified.to_csv(args.raw / CLASSIFIED_FILE, index=False)
    log.info("classification: %s", bu.classification_summary(classified))
    common = classified[classified["common"].astype(bool)]
    resolved = common[["ticker", "cik"]].assign(until=pd.NaT)
    calendar = pd.DatetimeIndex(pd.read_parquet(ETF / "SPY.parquet").index)
    events = edgar.build_events(resolved, client, calendar)
    events.to_parquet(args.raw / EVENTS_FILE)
    log.info("%d earnings events, %d EDGAR requests", len(events), client.requests)
    return 0


def _etf(symbol: str) -> pd.DataFrame:
    return pd.read_parquet(ETF / f"{symbol}.parquet").rename(columns=str.capitalize)


def _traded(raw: Path, tickers: list[str]) -> tuple[dict | None, str]:
    """Unadjusted closes for the $5 test, or None (adjusted closes, logged
    and recorded in the report) when they were not downloaded."""
    folder = raw / UNADJUSTED_DIR
    if not folder.exists():
        log.warning("no %s: the $5 test uses adjusted closes", folder)
        return None, "adjusted closes (unadjusted bars missing)"
    out, missing = {}, 0
    for t in tickers:
        path = folder / f"{t}.parquet"
        if path.exists():
            out[t] = pd.read_parquet(path, columns=["Close"])["Close"]
        else:
            missing += 1
    if missing:
        log.warning("%d tickers have no unadjusted bars: adjusted close used", missing)
    return out, f"unadjusted closes ({len(out)} tickers, {missing} without)"


def _membership(elig: pd.DataFrame) -> pd.DataFrame:
    """Eligible names at each month end, dated the next session: the list
    used for that month's trading (earnings drift `member_at_day0`)."""
    index = elig.index
    ends = np.flatnonzero(month_end_mask(pd.DatetimeIndex(index)))
    ends = ends[ends + 1 < len(index)]
    rows = elig.iloc[ends]
    rows.index = index[ends + 1]
    stacked = rows.stack()
    stacked = stacked[stacked]
    return pd.DataFrame(
        {
            "date": stacked.index.get_level_values(0),
            "ticker": stacked.index.get_level_values(1),
        }
    )


class Signals:
    """Month-end selections, cached per rank day."""

    def __init__(self, panel, elig, r1, r61, ear):
        self.tickers = np.array(panel.tickers)
        self.elig, self.r1 = elig.to_numpy(), r1.to_numpy()
        self.r61, self.ear = r61.to_numpy(), ear.to_numpy()
        self._scores: dict[int, pd.Series] = {}

    def _row(self, i: int):
        names = self.tickers[self.elig[i]]
        cols = self.elig[i]
        e = pd.Series(True, index=names)
        return (
            e,
            pd.Series(self.r1[i, cols], index=names),
            pd.Series(self.r61[i, cols], index=names),
            pd.Series(self.ear[i, cols], index=names),
        )

    def reversal(self, i: int) -> dict[str, float]:
        e, r1, _, _ = self._row(i)
        return sc.lowest_decile(r1, e)

    def reversal_short(self, i: int) -> dict[str, float]:
        e, r1, _, _ = self._row(i)
        return sc.highest_decile(r1, e)

    def momentum(self, i: int) -> dict[str, float]:
        e, _, r61, _ = self._row(i)
        return sc.highest_decile(r61, e)

    def momentum_short(self, i: int) -> dict[str, float]:
        e, _, r61, _ = self._row(i)
        return sc.lowest_decile(r61, e)

    def score(self, i: int) -> pd.Series:
        if i not in self._scores:
            e, r1, r61, ear = self._row(i)
            self._scores[i] = sc.composite_score(r1, r61, ear, e)
        return self._scores[i]

    def composite(self, i: int) -> dict[str, float]:
        return sc.top_n(self.score(i), sc.TOP_COMPOSITE)

    def composite_short(self, i: int) -> dict[str, float]:
        return sc.top_n(-self.score(i), sc.TOP_COMPOSITE)

    def equal_weight(self, i: int) -> dict[str, float]:
        e, _, _, _ = self._row(i)
        return sc.equal_weight(e)


def _first_rank_day(index: pd.DatetimeIndex, signals: Signals) -> int:
    """The first month end where every signal (6-1 needs 126 sessions) can
    pick names: periods start at the next session."""
    for i in np.flatnonzero(month_end_mask(index)):
        if signals.momentum(i) and signals.reversal(i):
            return int(i)
    raise SystemExit("no month end with both signals")


def _spreads(panel, signals: Signals, windows: dict) -> dict:
    """Gross long-short spread per signal, assigned to the period its
    holding month starts in."""
    index = panel.index
    legs = {
        REVERSAL: (signals.reversal, signals.reversal_short),
        MOMENTUM: (signals.momentum, signals.momentum_short),
        COMPOSITE: (signals.composite, signals.composite_short),
    }
    out: dict = {p: {} for p in windows}
    for name, (long_sel, short_sel) in legs.items():
        spread = sc.spread_returns(
            panel,
            lambda i, f=long_sel: list(f(i)),
            lambda i, f=short_sel: list(f(i)),
            index[0],
            index[-1],
        )
        held_from = index[index.searchsorted(spread.index, side="right")]
        for period, (lo, hi) in windows.items():
            part = spread[(held_from >= lo) & (held_from <= hi)]
            out[period][name] = sc.spread_summary(part)
    return out


def run(args: argparse.Namespace) -> int:
    started = time.time()
    classified = pd.read_csv(args.raw / CLASSIFIED_FILE)
    common = classified[classified["common"].astype(bool)]
    frames = {t: pd.read_parquet(args.raw / f"{t}.parquet") for t in common["ticker"]}
    spy, iwm = _etf("SPY"), _etf("IWM")
    traded, price_test = _traded(args.raw, list(frames))
    panel = sc.build_universe_panel(frames, spy, traded=traded)
    del traded
    index = panel.index
    log.info("panel %d sessions x %d tickers", len(index), len(panel.tickers))
    del frames

    elig = sc.eligible(panel)
    counts_all = sc.monthly_counts(elig)
    segment_starts = int((panel.age == 1).to_numpy().sum())
    r1, r61 = sc.return_1m(panel), sc.return_6_1(panel)
    prices = ed.Prices(
        open=panel.open,
        close=panel.close,
        spy_open=spy["Open"].astype(float),
        spy_close=spy["Close"].astype(float),
    )
    events = pd.read_parquet(args.raw / EVENTS_FILE)
    table = ed.event_table(events, prices)
    membership = _membership(elig)
    table["member"] = ed.member_at_day0(table, membership)
    table["signal"] = ed.classify(table)
    ear = sc.latest_ear(table, index, panel.tickers)
    signals = Signals(panel, elig, r1, r61, ear)

    first = _first_rank_day(index, signals)
    split = int(index.searchsorted(pd.Timestamp(SPLIT_DATE)))
    counts = counts_all[counts_all.index >= index[first]]
    windows = {
        "build": (index[first + 1], index[split - 1]),
        "test": (index[split], index[-1]),
    }
    monthly = {
        REVERSAL: signals.reversal,
        MOMENTUM: signals.momentum,
        COMPOSITE: signals.composite,
        EW: signals.equal_weight,
    }
    report: dict = {"periods": {}}
    for period, (lo, hi) in windows.items():
        a, b = ed.window(index, lo, hi)
        rows: dict = {}
        for mult in (1.0, 2.0):
            rates = sc.cost_rates(panel.adv, mult)
            cost_rows: dict = {}
            for name, selector in monthly.items():
                res = sc.simulate_monthly(panel, selector, lo, hi, rates=rates)
                cost_rows[name] = sc.stats(res)
            res = ed.simulate(table, prices, a, b, costs=rates.to_numpy())
            cost_rows[EARNINGS] = sc.event_stats(res)
            rows[f"{mult:g}x"] = cost_rows
        rows["benchmarks"] = {
            IWM: sc.stats(buy_hold_result(iwm["Close"].reindex(index), lo, hi)),
            SPY: sc.stats(buy_hold_result(spy["Close"].reindex(index), lo, hi)),
        }
        ls = ed.simulate(
            table,
            prices,
            a,
            b,
            long_short=True,
            costs=sc.cost_rates(panel.adv).to_numpy(),
        )
        rows["earnings_long_short"] = sc.event_stats(ls)
        report["periods"][period] = rows
        log.info("%s done (%.0f s)", period, time.time() - started)

    report["spreads"] = _spreads(panel, signals, windows)
    report["event_study"] = {
        p: ed.event_study(table, lo, hi) for p, (lo, hi) in windows.items()
    }
    in_sample = table[pd.to_datetime(table["day0"]) >= windows["build"][0]]
    status = json.loads((args.raw / bu.STATUS_FILE).read_text())
    assets = pd.read_parquet(args.raw / bu.ASSETS_FILE)
    cands = bu.candidates(assets)
    report.update(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        runtime_seconds=round(time.time() - started, 1),
        windows={
            k: [v[0].date().isoformat(), v[1].date().isoformat()]
            for k, v in windows.items()
        },
        universe={
            "assets": int(len(assets)),
            "assets_by_status": {
                str(k): int(v) for k, v in assets["status"].value_counts().items()
            },
            "candidates": int(len(cands)),
            "candidates_listed": int(cands["listed"].sum()),
            "candidates_otc": int((~cands["listed"]).sum()),
            "with_bars": int(sum(1 for e in status.values() if e["bars"])),
            "prescreened": int(len(classified)),
            "classification": bu.classification_summary(classified),
            "classification_sources": {
                str(k): int(v) for k, v in classified["source"].value_counts().items()
            },
            "not_common_reasons": {
                str(k): int(v)
                for k, v in classified.loc[~classified["common"].astype(bool), "reason"]
                .value_counts()
                .items()
            },
            "price_test": price_test,
            "segment_breaks": segment_starts - len(panel.tickers),
            "spliced_dropped": len(panel.suspect),
            "spliced_examples": panel.suspect[:20],
            "panel_tickers": len(panel.tickers),
            "eligible_per_month": sc.count_summary(counts),
            "eligible_per_month_build": sc.count_summary(
                counts[counts.index < pd.Timestamp(SPLIT_DATE)]
            ),
            "eligible_per_month_test": sc.count_summary(
                counts[counts.index >= pd.Timestamp(SPLIT_DATE)]
            ),
            "delisted": sc.delisted_share(panel, elig),
        },
        earnings={
            "events": int(len(events)),
            "events_in_window": int(len(in_sample)),
            "member_events": int(in_sample["member"].sum()),
            "signal_counts": {
                str(k): int(v) for k, v in in_sample["signal"].value_counts().items()
            },
        },
    )
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=1, default=str))
    DOC.write_text(markdown(report))
    log.info("wrote %s and %s", DOC, REPORT_JSON)
    return 0


def _fmt(value, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "n/a"
    return f"{value:,.{digits}f}"


COLUMNS = [
    ("CAGR %", "cagr_pct", 1),
    ("Max DD %", "max_drawdown_pct", 1),
    ("Sharpe", "sharpe", 2),
    ("Calmar", "calmar", 2),
    ("Pos. years %", "positive_years_pct", 0),
    ("Turnover x/yr", "turnover_annual_x", 1),
    ("Avg holdings", "avg_holdings", 1),
]
ORDER = [REVERSAL, MOMENTUM, EARNINGS, COMPOSITE, EW]


def table(rows: dict[str, dict]) -> str:
    head = "| Run | " + " | ".join(c[0] for c in COLUMNS) + " |"
    lines = [head, "|---|" + "---:|" * len(COLUMNS)]
    for name, st in rows.items():
        cells = [_fmt(st.get(key), d) for _, key, d in COLUMNS]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _period(report: dict, period: str) -> str:
    rows = report["periods"][period]
    lo, hi = report["windows"][period]
    one = {k: rows["1x"][k] for k in ORDER}
    two = {k: rows["2x"][k] for k in ORDER}
    return f"""### {period.capitalize()} ({lo} to {hi})

At 1x costs (10bp / 20bp per side):

{table({**one, **rows["benchmarks"]})}

At 2x costs (20bp / 40bp per side):

{table(two)}
"""


def _spread_table(report: dict) -> str:
    lines = [
        "| Period | Signal | Months | Mean spread %/month | t | % months > 0 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for period, sigs in report["spreads"].items():
        for name, s in sigs.items():
            lines.append(
                f"| {period} | {name} | {s['months']} | {_fmt(s['mean_pct'], 2)} | "
                f"{_fmt(s['t'], 2)} | {_fmt(s['positive_pct'], 0)} |"
            )
    return "\n".join(lines)


def _study_table(report: dict) -> str:
    lines = [
        "| Period | Group | Events | Mean AR60 % | t (monthly) |",
        "|---|---|---:|---:|---:|",
    ]
    for period, groups in report["event_study"].items():
        for label in (ed.TOP, ed.BOTTOM, "all_classified"):
            g = groups[label]
            lines.append(
                f"| {period} | {label} | {g['n']:,} | {_fmt(g['mean_pct'], 2)} | "
                f"{_fmt(g['t_monthly'], 2)} |"
            )
    return "\n".join(lines)


def markdown(report: dict) -> str:
    u, e = report["universe"], report["earnings"]
    c = u["classification"]
    pm, d = u["eligible_per_month"], u["delisted"]
    ls = {p: report["periods"][p]["earnings_long_short"] for p in report["periods"]}
    reasons = ", ".join(f"{k} {v:,}" for k, v in u["not_common_reasons"].items())
    sources = ", ".join(f"{k} {v:,}" for k, v in u["classification_sources"].items())
    return f"""# Small and mid caps (cycle 07b)

Generated {report["generated_at"]} by `scripts/run_smallcap.py` in \
{report["runtime_seconds"]:.0f} s. Code: `backend/app/data/broad_universe.py`, \
`backend/app/quant/smallcap.py` (earnings drift reuses \
`backend/app/quant/earnings_drift.py`); tests: `tests/test_smallcap.py`. Rules \
are the pre-registered ones in `docs/research/strategy-evidence-2026-11.md` \
("Cycle 07b"), run once with no tuning.

## Universe

- Alpaca US-equity assets: {u["assets"]:,} \
({", ".join(f"{k} {v:,}" for k, v in u["assets_by_status"].items())}). \
Candidates: {u["candidates"]:,} ({u["candidates_listed"]:,} on NYSE / Nasdaq / \
AMEX, {u["candidates_otc"]:,} OTC). OTC symbols are kept because a delisted \
company keeps its exchange-era bars under its OTC symbol (BBBYQ holds Bed \
Bath & Beyond's Nasdaq history); without them the delisted losers would be \
missing. Warrants, rights, units, preferreds, depositary shares, ETFs and \
5-letter foreign / ADR suffixes were dropped by symbol and name first.
- Bars: Alpaca SIP daily, all adjustments, requested from 2015 (Alpaca's \
history starts 2016-01-04), {u["with_bars"]:,} symbols with bars, in \
`data/raw_broad/` (gitignored).
- Pre-screen: {u["prescreened"]:,} symbols ever had a session with close >= \
$5 and 20-day dollar volume >= $5M; only these were classified.
- EDGAR classification (User-Agent "{edgar.USER_AGENT}"): {c["resolved"]:,} \
of {c["symbols"]:,} resolved to a CIK ({sources}); **{c["unresolved"]:,} could \
not be classified and are excluded**. Common stocks (10-K / 10-Q filers, SIC \
not 6221 / 6722 / 6726): {c["common"]:,}. Not common: {reasons}.
- Spliced-bar filter (`portfolio.suspect_spike`): {u["spliced_dropped"]} \
tickers dropped. A bar gap over {sc.MAX_GAP} sessions or a one-day move \
beyond {sc.MAX_JUMP:g}x either way starts a new segment (reused symbols); \
returns never span one: {u.get("segment_breaks", 0):,} breaks.
- Eligible names per month end from the build start (traded price >= $5 on \
{u.get("price_test", "adjusted closes")}, 20-day dollar volume >= $5M, not \
in the top {sc.EXCLUDE_TOP} by dollar volume): min {pm["min"]:,}, \
median {pm["median"]:,.0f}, max {pm["max"]:,} (build \
{u["eligible_per_month_build"]["median"]:,.0f}, test \
{u["eligible_per_month_test"]["median"]:,.0f} median).
- Delisted: {d["delisted"]:,} of the {d["ever_eligible"]:,} names ever \
eligible ({d["pct"]:.1f}%) have no bar on the last session. This is low for \
ten years of small caps: Alpaca's inactive list misses delisted names whose \
symbol moved on, so some survivorship bias remains.
- Known exclusions: banks that file with the FDIC, not the SEC (OZK, \
TowneBank; FRC and SBNY in cycle 06), have no 10-K on EDGAR and are \
excluded, as are foreign filers (20-F / 40-F).
- Earnings events (8-K Item 2.02): {e["events"]:,}; from the build start \
{e["events_in_window"]:,}, of which {e["member_events"]:,} were in the \
eligible list of their month; signals \
{", ".join(f"{k} {v:,}" for k, v in e["signal_counts"].items())}.

## Rules

- Signals at the month-end close, traded at the next open, equal weight, \
long-only, held one month. Reversal: lowest decile of the 21-session return. \
Momentum: highest decile of the 126-to-21-session return. Composite: mean of \
the cross-sectional z-scores (eligible names with both returns) of -1m return, \
6-1 return and the latest EAR known within {sc.EAR_DAYS} sessions (else 0); \
top {sc.TOP_COMPOSITE}.
- Earnings drift: cycle 06's rules (day +2 open entry, top EAR decile against \
the trailing 252-session pool of eligible names' EARs, 60 sessions held, at \
most 20 positions), with the month's eligible list as members.
- Costs per side: 10bp where the previous close's 20-day dollar volume is \
above $20M, else 20bp; 2x doubles both. Fractional shares, cash earns 0, \
$10,000 at the start of each period. A stock with no bar is sold at its last \
close.
- The build period starts at the first month end where 6-1 momentum can rank \
(126 sessions after Alpaca's first bar), so every run and benchmark starts \
together. Turnover is one-way: traded notional / 2 / mean equity per year.
- Equal-weight universe: every eligible name, rebalanced monthly, same costs.

## Results

{_period(report, "build")}
{_period(report, "test")}
## Long-short diagnostic

Not a tradeable result (no borrow cost, gross of costs). Each month-end: \
equal-weight return of the long leg minus the short leg to the next \
month-end close. Reversal: lowest minus highest 1m decile; momentum: highest \
minus lowest 6-1 decile; composite: top 20 minus bottom 20.

{_spread_table(report)}

Earnings drift long-short (top minus bottom decile events, 1x costs, borrow \
not modelled): build CAGR {_fmt(ls["build"]["cagr_pct"])}%, Sharpe \
{_fmt(ls["build"]["sharpe"], 2)}; test CAGR {_fmt(ls["test"]["cagr_pct"])}%, \
Sharpe {_fmt(ls["test"]["sharpe"], 2)}.

Event study (abnormal return vs SPY from the day +2 open to the day +61 \
close, gross):

{_study_table(report)}

{_first_run(report)}"""


def _first_run(report: dict) -> str:
    """The first run, before the two data fixes, for the record."""
    if not FIRST_RUN.exists():
        return ""
    first = json.loads(FIRST_RUN.read_text())
    rows = []
    for period in ("build", "test"):
        for name in ORDER:
            a = first["periods"][period]["1x"][name]
            b = report["periods"][period]["1x"][name]
            rows.append(
                f"| {period} | {name} | {_fmt(a['cagr_pct'])} | {_fmt(b['cagr_pct'])} "
                f"| {_fmt(a['sharpe'], 2)} | {_fmt(b['sharpe'], 2)} |"
            )
    return f"""## Data fixes after the first run (disclosed)

The first run exposed two data errors, fixed in the data layer and not in \
the rules:

1. The $5 test used split-adjusted closes, so stocks that later reverse-split \
(CBIO, GDC) showed adjusted prices of $600 to $7,750 while trading for \
pennies. The rule means the price that traded, so the test now uses \
unadjusted SIP closes (dollar volume was already right: close x volume is \
unchanged by adjustment).
2. Alpaca chains a reused symbol with no gap: SN jumped 116x in a day \
(Sanchez Energy's OTC tail, then SharkNinja), LINE 448x, SPRB 124x, WW 159x \
(post-bankruptcy shares). Their 6-1 returns of +10,000% and more swamped the \
composite's unwinsorised z-score. A one-day move beyond {sc.MAX_JUMP:g}x now \
starts a new segment. The threshold was set from these data errors (the \
largest real move found was DFDV's 9.4x), not from strategy results.

Rules, signals and costs are unchanged. 1x costs, first run versus this run:

| Period | Run | CAGR % first | CAGR % now | Sharpe first | Sharpe now |
|---|---|---:|---:|---:|---:|
{chr(10).join(rows)}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("step", choices=["download", "classify", "run"])
    parser.add_argument("--raw", type=Path, default=RAW)
    parser.add_argument("--limit", type=int, default=0, help="first N symbols")
    parser.add_argument(
        "--unadjusted", action="store_true", help="raw bars of the common stocks"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    steps = {"download": download, "classify": classify, "run": run}
    return steps[args.step](args)


if __name__ == "__main__":
    raise SystemExit(main())
