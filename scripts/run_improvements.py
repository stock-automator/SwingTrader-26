#!/usr/bin/env python3
"""
Improvement experiments for the top scorecard strategies (cycle 03 step 3).

Each strategy runs long-only through the portfolio engine as the baseline
(scorecard defaults) and with at most four single changes, fixed before
any run (`CANDIDATES`). The candidate with the best build-period composite
(2016-2022 only) is chosen; it is kept only if that composite beats the
baseline's AND, on the held-out test period, its return vs exposure-matched
SPY and its max drawdown are no worse than the baseline's. Test numbers of
the other candidates are shown for information only; they choose nothing.

    python scripts/run_improvements.py [--processes 8] [--strategies a,b]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.app.quant.portfolio import (  # noqa: E402
    PortfolioConfig,
    macro_regime,
    prepare_signals,
    run_portfolio_backtest,
)
from backend.app.quant.regime import MACRO_BLOCKED_REGIMES  # noqa: E402
from backend.app.quant.scorecard import (  # noqa: E402
    BUILD_START,
    COMPOSITE_WEIGHTS,
    SPLIT_DATE,
    _fmt,
    _pct_rank,
    make_strategy,
    period_stats,
)
from scripts.run_scorecard import load_universe, suspect_line  # noqa: E402

BASELINE = "baseline"
_REGIME = {
    "label": "regime",
    "change": "longs only when the SPY regime at the signal close is not "
    "BEAR_TRENDING or HIGH_VOLATILITY_CHOP",
    "config": {"long_blocked_regimes": MACRO_BLOCKED_REGIMES},
}
_TREND = {
    "label": "trend200",
    "change": "longs only when the close is above its 200-day SMA",
    "config": {"long_trend_sma": 200},
}
_TIME20 = {
    "label": "time20",
    "change": "20-day time stop instead of 40",
    "config": {"time_stop_days": 20},
}
#: Fixed before any run: three shared filters plus one stop/target change
#: that fits each strategy's style.
CANDIDATES: dict[str, list[dict]] = {
    "zscore_mean_reversion": [
        _REGIME,
        _TREND,
        _TIME20,
        {
            "label": "tp2",
            "change": "target 2.0 ATR instead of 3.0 (take the reversion sooner)",
            "params": {"tp_atr_multiplier": 2.0},
        },
    ],
    "supertrend_psar": [
        _REGIME,
        _TREND,
        _TIME20,
        {
            "label": "sl2",
            "change": "stop 2.0 ATR instead of 1.5 (room for the trend)",
            "params": {"sl_atr_multiplier": 2.0},
        },
    ],
    "bollinger_keltner_squeeze": [
        _REGIME,
        _TREND,
        _TIME20,
        {
            "label": "sl2",
            "change": "stop 2.0 ATR instead of 1.5 (room after the breakout)",
            "params": {"sl_atr_multiplier": 2.0},
        },
    ],
}
#: Build-composite weights: the scorecard's, without robustness, rescaled.
BUILD_WEIGHTS = {k: w for k, w in COMPOSITE_WEIGHTS.items() if k != "robustness"}

_CTX: dict = {}


def build_composite(rows: list[dict]) -> list[float]:
    """Weighted percentile ranks of each row's build stats among `rows`."""
    total = sum(BUILD_WEIGHTS.values())
    score = pd.Series(0.0, index=range(len(rows)))
    for key, weight in BUILD_WEIGHTS.items():
        score += weight / total * _pct_rank([r["build"].get(key) for r in rows])
    return [round(float(s), 4) for s in score]


def _not_worse(new, old) -> bool:
    return new is not None and old is not None and new >= old


def decide(rows: list[dict]) -> dict:
    """Choose the best build-composite candidate (rows[0] is the baseline)
    and keep it only if the test period holds up."""
    scores = build_composite(rows)
    best = max(range(1, len(rows)), key=lambda i: scores[i], default=None)
    if best is None or scores[best] <= scores[0]:
        return {"chosen": None, "kept": False, "reason": "baseline wins build"}
    base, pick = rows[0]["test"], rows[best]["test"]
    holds_spy = _not_worse(pick.get("vs_spy_pct"), base.get("vs_spy_pct"))
    holds_dd = _not_worse(pick.get("max_drawdown_pct"), base.get("max_drawdown_pct"))
    reasons = [
        msg
        for ok, msg in (
            (holds_spy, "test vs SPY worse"),
            (holds_dd, "test max drawdown worse"),
        )
        if not ok
    ]
    return {
        "chosen": rows[best]["label"],
        "kept": not reasons,
        "reason": ", ".join(reasons) or "build better, test holds",
    }


def _init(data_dir: str, symbols: list[str], options: dict) -> None:
    frames, spy, missing = load_universe(Path(data_dir), symbols)
    _CTX.update(frames=frames, spy=spy, missing=missing, **options)


def _task(task: tuple[str, dict]) -> tuple:
    name, cand = task
    frames, spy = _CTX["frames"], _CTX["spy"]
    config = replace(PortfolioConfig(), allow_shorts=False, **cand.get("config", {}))
    strategy = make_strategy(name, spy, cand.get("params"))
    regime = macro_regime(spy)
    prepared = prepare_signals(strategy, frames, spy.index)
    split = pd.Timestamp(_CTX["split_date"])

    def stats(lo, hi):
        result = run_portfolio_backtest(
            strategy, frames, spy, config, lo, hi, regime=regime, prepared=prepared
        )
        return period_stats(result, spy, regime)

    row = {
        "label": cand["label"],
        "change": cand.get("change", "scorecard defaults"),
        "build": stats(_CTX["start"], split - pd.Timedelta(days=1)),
        "test": stats(split, None),
    }
    return name, row, list(prepared.suspect)


def _table(rows: list[dict], result: dict) -> list[str]:
    lines = [
        "| Candidate | Change | Build composite | Build CAGR % | Build vs SPY pp "
        "| Build trades | Test return % | Test exp-matched SPY % | Test vs SPY pp "
        "| Test PF | Test max DD % | Test trades |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        b, t = r["build"], r["test"]
        label = r["label"]
        if label == result["chosen"]:
            label += " (kept)" if result["kept"] else " (chosen, not kept)"
        cells = [
            label,
            r["change"],
            _fmt(r["build_composite"], 3),
            _fmt(b.get("cagr_pct")),
            _fmt(b.get("vs_spy_pct")),
            str(b.get("trades", 0)),
            _fmt(t.get("total_return_pct")),
            _fmt(t.get("spy_matched_return_pct")),
            _fmt(t.get("vs_spy_pct")),
            _fmt(t.get("profit_factor"), 2),
            _fmt(t.get("max_drawdown_pct")),
            str(t.get("trades", 0)),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _parse(argv: list[str] | None) -> argparse.Namespace:
    from backend.app.config import get_settings

    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--data-dir", type=Path, default=settings.data_dir)
    parser.add_argument("--universe", type=Path, default=settings.universe_path)
    parser.add_argument(
        "--markdown", type=Path, default=ROOT / "docs/strategy-improvements.md"
    )
    parser.add_argument(
        "--json", type=Path, default=ROOT / "data/reports/improvements.json"
    )
    parser.add_argument("--strategies", default=",".join(CANDIDATES))
    parser.add_argument("--processes", type=int, default=max(1, mp.cpu_count() - 1))
    parser.add_argument("--split-date", default=SPLIT_DATE)
    parser.add_argument("--start", default=BUILD_START)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> dict:
    args = _parse(argv)
    began = time.time()
    names = [n.strip() for n in args.strategies.split(",") if n.strip()]
    unknown = [n for n in names if n not in CANDIDATES]
    if unknown:
        raise SystemExit(f"No candidates for: {unknown}")
    symbols = json.loads(args.universe.read_text())["symbols"]
    options = {"split_date": args.split_date, "start": args.start}
    tasks = [
        (n, c)
        for n in names
        for c in [{"label": BASELINE, "change": "scorecard defaults"}] + CANDIDATES[n]
    ]

    _init(str(args.data_dir), symbols, options)
    print(f"{len(_CTX['frames'])} tickers, {len(tasks)} runs", flush=True)
    found: dict[str, dict[str, dict]] = {n: {} for n in names}
    suspect: set[str] = set()

    def collect(results) -> None:
        for done, (name, row, bad) in enumerate(results, start=1):
            found[name][row["label"]] = row
            suspect.update(bad)
            print(
                f"[{done}/{len(tasks)}] {time.time() - began:6.0f}s "
                f"{name} {row['label']}",
                flush=True,
            )

    if args.processes <= 1:
        collect(map(_task, tasks))
    else:
        with mp.get_context("spawn").Pool(
            min(args.processes, len(tasks)),
            initializer=_init,
            initargs=(str(args.data_dir), symbols, options),
        ) as pool:
            collect(pool.imap_unordered(_task, tasks))

    runtime = time.time() - began
    spy = _CTX["spy"]
    out: dict[str, dict] = {}
    body: list[str] = []
    for name in names:
        labels = [BASELINE] + [c["label"] for c in CANDIDATES[name]]
        rows = [found[name][label] for label in labels]
        for row, score in zip(rows, build_composite(rows)):
            row["build_composite"] = score
        result = decide(rows)
        out[name] = {"rows": rows, **result}
        verdict = (
            f"Chosen on 2016-2022: **{result['chosen'] or 'none'}**. "
            f"Kept: **{'yes' if result['kept'] else 'no'}** ({result['reason']})."
        )
        body += [f"### {name}", "", *_table(rows, result), "", verdict, ""]

    header = [
        "# Strategy improvements (cycle 03 step 3)",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by "
        f"`scripts/run_improvements.py` in {runtime / 60:.1f} min.",
        "",
        f"- Universe: {len(_CTX['frames'])} cached symbols; long-only; "
        "scorecard account rules ($10,000, 1% risk, 25% max position, 6 "
        "positions, 6% heat, costs as in docs/scorecard.md).",
        suspect_line(sorted(suspect)),
        f"- Build {args.start} to {args.split_date} (exclusive); test "
        f"{args.split_date} to {spy.index[-1]:%Y-%m-%d}.",
        "- Candidates were fixed before any run: one change each against the "
        "baseline. Build composite = the scorecard weights without "
        "robustness, rescaled to 1, as percentile ranks among this "
        "strategy's rows on build-period stats only.",
        "- Rule: choose the best build composite; keep it only if it beats "
        "the baseline's and its test return vs exposure-matched SPY and test "
        "max drawdown are no worse. Other candidates' test columns are for "
        "information and chose nothing.",
        "",
    ]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": args.start,
        "split_date": args.split_date,
        "end": f"{spy.index[-1]:%Y-%m-%d}",
        "suspect_data": sorted(suspect),
        "runtime_seconds": round(runtime, 1),
        "strategies": out,
    }
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text("\n".join(header + body))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=1, default=str))
    print(f"wrote {args.markdown} and {args.json} in {runtime:.0f}s", flush=True)
    return payload


if __name__ == "__main__":
    main()
