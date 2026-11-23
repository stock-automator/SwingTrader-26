#!/usr/bin/env python3
"""
Strategy scorecard over the cached universe (cycle 03 step 2).

Runs every registered strategy through the portfolio engine on 2016 to the
split (build) and the split to the last cached bar (test), long-only and
long+short, plus the build-period +/-20% parameter reruns, and writes a ranked table to
`docs/scorecard.md` and the full numbers to `data/reports/scorecard.json`.
Cached bars only: nothing is downloaded. Work is spread over processes by
(strategy, parameter set); each worker loads the cache once.

    python scripts/run_scorecard.py [--processes 8] [--strategies a,b]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.app.data.loader import (  # noqa: E402
    SPY_TICKER,
    DataUnavailableError,
    load_prices,
)
from backend.app.quant.portfolio import PortfolioConfig  # noqa: E402
from backend.app.quant.scorecard import (  # noqa: E402
    BUILD_START,
    COMPOSITE_WEIGHTS,
    SPLIT_DATE,
    param_variants,
    perturbation_run,
    robustness_window,
    score_strategy,
    scorecard_markdown,
    scorecard_rows,
    summarize_robustness,
)
from backend.app.quant.strategies import REGISTRY  # noqa: E402

#: Signal generation is far slower for these (rolling Hurst exponent), so
#: their tasks are queued first.
SLOW_STRATEGIES = ("zscore_mean_reversion",)

_CTX: dict = {}


def load_universe(
    data_dir: Path, symbols: list[str]
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, list[str]]:
    frames, missing = {}, []
    for symbol in symbols:
        try:
            frames[symbol] = load_prices(
                symbol, data_dir=data_dir, allow_download=False
            )
        except DataUnavailableError:
            missing.append(symbol)
    spy = load_prices(SPY_TICKER, data_dir=data_dir, allow_download=False)
    return frames, spy, missing


def _init(data_dir: str, symbols: list[str], options: dict) -> None:
    frames, spy, missing = load_universe(Path(data_dir), symbols)
    _CTX.update(frames=frames, spy=spy, missing=missing, **options)


def _task(task: tuple) -> tuple:
    kind, name, *rest = task
    config = PortfolioConfig()
    if kind == "score":
        return task, score_strategy(
            name,
            _CTX["frames"],
            _CTX["spy"],
            config,
            _CTX["split_date"],
            robustness=False,
            start=_CTX["start"],
        )
    param, value = rest
    start, end = robustness_window(_CTX["start"], _CTX["split_date"])
    return task, perturbation_run(
        name, param, value, _CTX["frames"], _CTX["spy"], config, start, end
    )


def _parse(argv: list[str] | None) -> argparse.Namespace:
    from backend.app.config import get_settings

    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--data-dir", type=Path, default=settings.data_dir)
    parser.add_argument("--universe", type=Path, default=settings.universe_path)
    parser.add_argument("--markdown", type=Path, default=ROOT / "docs/scorecard.md")
    parser.add_argument(
        "--json", type=Path, default=ROOT / "data/reports/scorecard.json"
    )
    parser.add_argument("--strategies", default=",".join(REGISTRY))
    parser.add_argument("--processes", type=int, default=max(1, mp.cpu_count() - 1))
    parser.add_argument("--split-date", default=SPLIT_DATE)
    parser.add_argument("--start", default=BUILD_START)
    parser.add_argument("--no-robustness", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> dict:
    args = _parse(argv)
    began = time.time()
    names = [n.strip() for n in args.strategies.split(",") if n.strip()]
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise SystemExit(f"Unknown strategies: {unknown}")
    symbols = json.loads(args.universe.read_text())["symbols"]
    options = {"split_date": args.split_date, "start": args.start}

    tasks = [("score", n) for n in names]
    if not args.no_robustness:
        tasks += [
            ("perturb", n, v["param"], v["value"])
            for n in names
            for v in param_variants(n)
        ]
    tasks.sort(key=lambda t: t[1] not in SLOW_STRATEGIES)

    _init(str(args.data_dir), symbols, options)
    missing = _CTX["missing"]
    if missing:
        print(
            f"WARNING: {len(missing)} universe symbols not cached, skipped: "
            f"{', '.join(missing)}",
            file=sys.stderr,
        )
    print(f"{len(_CTX['frames'])} tickers, {len(tasks)} runs", flush=True)

    scores: dict[str, dict] = {}
    perturbations: dict[str, list[dict]] = {n: [] for n in names}

    def collect(results) -> None:
        for done, (task, out) in enumerate(results, start=1):
            if task[0] == "score":
                scores[task[1]] = out
            else:
                perturbations[task[1]].append(out)
            label = " ".join(str(x) for x in task[1:])
            print(
                f"[{done}/{len(tasks)}] {time.time() - began:6.0f}s {label}",
                flush=True,
            )

    if args.processes <= 1:
        collect(map(_task, tasks))
    else:
        with mp.get_context("spawn").Pool(
            args.processes,
            initializer=_init,
            initargs=(str(args.data_dir), symbols, options),
        ) as pool:
            collect(pool.imap_unordered(_task, tasks))

    ordered = [scores[n] for n in names]
    for score in ordered:
        runs = sorted(perturbations[score["strategy"]], key=lambda r: r["param"])
        score["robustness"] = summarize_robustness(score["build_cagr_pct"], runs)
    rows = scorecard_rows(ordered)
    suspect = sorted({t for score in ordered for t in score["suspect_data"]})
    if suspect:
        print(
            f"WARNING: {len(suspect)} tickers excluded as suspect data: "
            f"{', '.join(suspect)}",
            file=sys.stderr,
        )
    weights = ", ".join(f"{w:.2f} {k}" for k, w in COMPOSITE_WEIGHTS.items())
    spy = _CTX["spy"]
    runtime = time.time() - began
    header = [
        "# Strategy scorecard",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by "
        f"`scripts/run_scorecard.py` in {runtime / 60:.1f} min.",
        "",
        f"- Universe: {len(_CTX['frames'])} cached symbols from "
        f"`{args.universe.name}`"
        + (f"; {len(missing)} not cached: {', '.join(missing)}" if missing else "")
        + ".",
        "- Suspect data (a >40% one-day close move that retraces at least half "
        "within 5 bars), excluded from every run: "
        + (", ".join(f"suspect data: {t}" for t in suspect) if suspect else "none")
        + ".",
        f"- Build {args.start} to {args.split_date} (exclusive); held-out test "
        f"{args.split_date} to {spy.index[-1]:%Y-%m-%d}. Signals use full "
        "history, so the test period starts warmed up.",
        "- One account per run: $10,000, 1% risk per trade from the stop, "
        "max 25% of equity per position, 6 open positions, 6% total open "
        "risk, 40-trading-day time stop, entries at the next open, "
        "stop checked before target. Costs: 0.1% commission and 0.05% "
        "spread per leg, FX per Settings.",
        "- 'vs SPY' = strategy return minus SPY held at the strategy's own "
        "invested fraction (exposure-matched), in percentage points.",
        "- Composite score: weighted percentile ranks across rows, 70% test / "
        f"30% build: {weights}. Positive regimes = % of SPY macro regimes "
        "(at the prior close) with a positive return. Robustness = worst "
        "long-only build-period CAGR with each numeric parameter at ±20% "
        "(±1 for small integers); it uses 2016-2022 only, so no test data "
        "feeds the score through it. Rows with fewer than 30 trades in "
        "either period are halved and sort last.",
        "- An 'equity below zero' flag means a short gapped past its "
        "collateral and the run stopped opening positions.",
        "- Caveats: survivorship bias (today's index members only); short "
        "borrow and fees not modelled; whole shares.",
        "",
        "",
    ]
    markdown = "\n".join(header) + scorecard_markdown(rows, ordered)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": args.start,
        "split_date": args.split_date,
        "end": f"{spy.index[-1]:%Y-%m-%d}",
        "universe_size": len(_CTX["frames"]),
        "missing_symbols": missing,
        "suspect_data": suspect,
        "runtime_seconds": round(runtime, 1),
        "rows": rows,
        "strategies": ordered,
    }
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=1, default=str))
    print(f"wrote {args.markdown} and {args.json} in {runtime:.0f}s", flush=True)
    return payload


if __name__ == "__main__":
    main()
