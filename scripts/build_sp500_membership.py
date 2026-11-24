#!/usr/bin/env python3
"""
Rebuild `config/sp500_membership.csv` (columns date, ticker): S&P 500
membership at every month end from 2015-12 plus today, walked back from
today's Wikipedia list through the change log. Exits non-zero, loudly, when a
page is unreachable or the walk does not reconcile.

    python scripts/build_sp500_membership.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.app.data.sp500_history import SourceError, build_membership  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--start", default="2015-12-01")
    parser.add_argument(
        "--out", type=Path, default=ROOT / "config" / "sp500_membership.csv"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        members = build_membership(args.start, pd.Timestamp.now().normalize())
    except SourceError as exc:
        print(f"ERROR: S&P 500 membership NOT rebuilt: {exc}", file=sys.stderr)
        return 1
    members.to_csv(args.out, index=False, date_format="%Y-%m-%d")
    sizes = members.groupby("date").size()
    print(
        f"wrote {args.out}: {len(sizes)} snapshots, {sizes.min()}-{sizes.max()} "
        f"names, {members['ticker'].nunique()} distinct tickers"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
