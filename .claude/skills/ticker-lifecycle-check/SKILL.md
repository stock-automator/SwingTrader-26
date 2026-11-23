---
name: ticker-lifecycle-check
description: Classify a US ticker that yfinance reports as "possibly delisted", missing, stale or with junk bars as rename, stock_merger, cash_buyout or broken_data, using the cached parquet and the successor's price history, before anything is archived, blocked or deleted. Use this whenever a SwingTrader-26 task mentions delisted, acquired, renamed, merged or missing tickers, DENY_ENTRIES / denylist edits, data/delisted archives, universe sync failures or S&P 500 / Nasdaq-100 constituent changes, even if nobody asks for a "lifecycle check".
---

# Ticker lifecycle check

Web sources often disagree about corporate events. In cycle 01, two passes contradicted
each other on EQR. Prices don't disagree. Settle each ticker with data, then classify it.
Change nothing.

## 1. Inventory (read-only, per ticker)
- **Cache file** `data/raw/<T>.parquet`:
  - rows, first and last date;
  - the last bar with Volume > 0;
  - any zero-volume or junk bars after it (EQR had fake bars after 2026-08-17; WBD had a
    junk 2026-10-05 bar);
  - whether the file is tracked (`git ls-files`).
- **References:**
  - `DENY_ENTRIES` and the static fallback lists in `backend/app/data/universe.py`;
  - the universe/watchlist file and `data/delisted/`;
  - open paper positions in `~/.hermes/bots/paper_trader`.

  If the ticker is already denied, check that its entry agrees with the data.

```python
import pandas as pd
df = pd.read_parquet("data/raw/EQR.parquet")
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.droplevel(1)
live = df[df["Volume"] > 0]
print(len(df), df.index.min(), df.index.max(), live.index.max(), live["Close"].iloc[-1])
```

## 2. Find the candidate event
Run one targeted search for the acquirer or the new name, and cite the URL. The
candidates are:
- a renamed symbol (BK→BNY, MMC→MRSH, FI→FISV);
- the surviving company of a merger (AVB→VMRK);
- a cash buyer.

## 3. Compare with the successor's history
Fetch the successor into the session scratchpad only, never into `data/raw/`
(`yf.download("VMRK", start=..., auto_adjust=False)`). Align the dates before the event,
then classify:
- **rename**: the closes match (within ~0.5% on ≥ 20 overlapping days). Record the
  `successor`.
- **stock_merger**: the successor trades, its closes differ, and holders received shares,
  even with some cash (KVUE→KMB). Record the `successor`.
- **cash_buyout**: holders received only cash. No successor.
- **broken_data**: there was no corporate event, just gaps, zero-volume runs or impossible
  bars. Set `since` to None.

`since` = the first session after the last real trading day. Check the exchange calendar,
not the last row in the file.

## 4. Output
| ticker | kind | last real bar | since | successor | evidence (rows, close match, URL) | action |

**Possible actions:**
- archive to `data/delisted/`, trimmed to the last real bar;
- delete an untracked stub;
- add the successor to the universe;
- add or fix a `DENY_ENTRIES` row.

Flag any open paper positions and anything still unsettled. Each action needs a test that
asserts on the committed file. Deleting or blocking needs A's approval.

## Test prompts
- **Should trigger:** "During /data/sync yfinance says EQR and AVB are possibly delisted.
  Check them and tell me what to do." It passes if:
  - EQR comes back as a rename to VMRK, with closes matching up to 2026-08-17;
  - AVB comes back as a stock_merger to VMRK, with `since` 2026-08-18;
  - it notes that both entries already exist and agree with the data;
  - it writes nothing under `data/`.
- **Should not trigger:** "Add a P/E column to the scanner."
