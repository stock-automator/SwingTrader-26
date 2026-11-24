#!/usr/bin/env python3
"""
Small/mid-cap backtest (cycle 07b), rules pre-registered in
`docs/research/strategy-evidence-2026-11.md`.

    python scripts/run_smallcap.py download [--limit N]   # Alpaca, resumable
    python scripts/run_smallcap.py classify               # EDGAR, cached
    python scripts/run_smallcap.py run                    # cached data only

`download` writes `data/raw_broad/` (gitignored): `_assets.parquet`, one
parquet per symbol and `_status.json`. `classify` writes
`data/raw_broad/_classified.csv` and `data/raw_broad/_events.parquet` (EDGAR
JSON cached under `data/edgar/`). `run` writes `docs/smallcap.md` and
`data/reports/smallcap.json`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.app.data import broad_universe as bu  # noqa: E402

RAW = ROOT / "data/raw_broad"
log = logging.getLogger("smallcap")


def _downloader() -> bu.BarsDownloader:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY missing from .env")
    return bu.BarsDownloader(key, secret)


def download(args: argparse.Namespace) -> int:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("step", choices=["download", "classify", "run"])
    parser.add_argument("--raw", type=Path, default=RAW)
    parser.add_argument("--limit", type=int, default=0, help="first N symbols")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    steps = {"download": download}
    return steps[args.step](args)


if __name__ == "__main__":
    raise SystemExit(main())
