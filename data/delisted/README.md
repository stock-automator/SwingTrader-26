# data/delisted/

Archived daily OHLCV history of tickers that left the universe (delisted,
acquired). No loader reads this folder yet; it is kept for a future
point-in-time universe so backtests can include names that later vanished.

Each filename carries the symbol's last trading day (`<TICKER>_<YYYYMMDD>`).

- `WBD_20261005.parquet` - last real bar 2026-09-18. A junk 2026-10-05 bar
  (Open 0, Volume 40) was removed; no bars exist after 2026-09-18.
- `KVUE_20261030.parquet` - last real bar 2026-10-30. Gap: no bars for
  2026-09-19..2026-10-29 (jumps from 2026-09-18 to the final bar).

See `DENY_ENTRIES` in `backend/app/data/universe.py` for why each was removed.
