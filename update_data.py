import pandas as pd
import yfinance as yf
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path("data/raw")
WATCHLIST_FILE = Path("watchlist.txt")

START_DATE = "2014-01-01"

# Re-download recent days so the latest bars are refreshed
REFRESH_DAYS = 7


def read_watchlist():

    if not WATCHLIST_FILE.exists():

        raise FileNotFoundError("watchlist.txt not found")

    with open(WATCHLIST_FILE, "r") as f:

        tickers = [
            line.strip().upper()
            for line in f
            if line.strip() and not line.startswith("#")
        ]

    return list(dict.fromkeys(tickers))


def update_ticker(ticker):

    parquet_path = DATA_DIR / f"{ticker}.parquet"

    try:

        # ----------------------------------------------------
        # No local file
        # ----------------------------------------------------

        if not parquet_path.exists():

            print(f"⚠️ {ticker}: no local cache")

            return "missing"

        # ----------------------------------------------------
        # Load existing
        # ----------------------------------------------------

        existing = pd.read_parquet(parquet_path)

        if existing.empty:

            print(f"⚠️ {ticker}: local file is empty")

            return "empty"

        latest_date = existing.index.max()

        # ----------------------------------------------------
        # Refresh recent period
        # ----------------------------------------------------

        start_date = max(
            pd.Timestamp(START_DATE),
            latest_date - timedelta(days=REFRESH_DAYS),
        )

        end_date = datetime.now() + timedelta(days=1)

        print(
            f"Updating {ticker} " f"({start_date.date()} → " f"{end_date.date()})...",
            end=" ",
            flush=True,
        )

        new_data = yf.download(
            ticker,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=False,
            timeout=30,
            threads=False,
        )

        if new_data is None or new_data.empty:

            print("⚠️ no new data")

            return "no_data"

        # ----------------------------------------------------
        # Combine
        # ----------------------------------------------------

        combined = pd.concat(
            [
                existing,
                new_data,
            ]
        )

        combined = combined[~combined.index.duplicated(keep="last")]

        combined = combined.sort_index()

        # ----------------------------------------------------
        # Save
        # ----------------------------------------------------

        combined.to_parquet(
            parquet_path,
            engine="pyarrow",
        )

        added = len(combined) - len(existing)

        print(f"✓ {len(combined):,} total rows" f" ({max(added, 0):,} new/updated)")

        return "success"

    except Exception as e:

        print(f"✗ ERROR: {str(e)[:100]}")

        return "failed"


def main():

    tickers = read_watchlist()

    print()
    print("=" * 70)
    print("LOCAL STOCK DATA UPDATE")
    print("=" * 70)

    print(f"Tickers: {len(tickers)}")

    print(f"Refresh window: {REFRESH_DAYS} days")

    print("=" * 70)
    print()

    stats = {
        "success": 0,
        "missing": 0,
        "empty": 0,
        "no_data": 0,
        "failed": 0,
    }

    for i, ticker in enumerate(
        tickers,
        start=1,
    ):

        print(
            f"[{i:3d}/{len(tickers)}] ",
            end="",
        )

        result = update_ticker(ticker)

        if result in stats:
            stats[result] += 1

    print()
    print("=" * 70)
    print("UPDATE COMPLETE")
    print("=" * 70)

    for key, value in stats.items():

        print(f"{key:<10}: {value}")

    print("=" * 70)


if __name__ == "__main__":

    main()
