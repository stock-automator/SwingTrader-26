# Cycle 01: remove delisted tickers (🛑 Gate 1, awaiting A)

A's request: "remove delisted tickers their files etc."
Roles run: product-owner, domain-expert (×3), customer, system-architect, tech-expert. None skipped.

## What we found (verified with Yahoo data and the cache files, not just web sources)

| Ticker | What happened | Successor | Cache file today | Plan |
|---|---|---|---|---|
| EA | Taken private for cash, closed 2026-08-04 | none | **1 row**, untracked (not in git) | delete the file |
| KVUE | Bought by Kimberly-Clark (cash + KMB shares), last trading day 2026-10-30 | KMB (already in universe) | 848 rows from 2023, tracked | **archive or delete: your call** |
| WBD | Bought by Paramount Skydance for cash, last trading day 2026-10-05 | none (cash) | 3,198 rows from 2014, tracked | **archive or delete: your call** |
| AVB | Merged into EQR, which was renamed Vivmark Residential (VMRK). Last day 2026-08-17 | VMRK | **27 rows**, untracked | delete the file |
| EQR | **Not delisted: renamed to VMRK.** Yahoo's VMRK history is EQR's (identical closes up to 08-17) | VMRK | 14 rows, untracked, with fake zero-volume bars after 08-17 | delete the stub and add **VMRK**, which brings the full history |

**The older blocked list has the same problem.** BK, CTRA, K, MMC and WBA have been hidden for months as "broken data":
- **BK** was renamed **BNY** and **MMC** was renamed **MRSH**. Both are live S&P 500 stocks that are currently missing from your universe.
- CTRA (merged into DVN), K (bought by Mars) and WBA (taken private) really are gone.

## Stories
- **C-2 (P1):** remove the 5 tickers from the watchlist, the fallback lists and the cache. Block them from coming back, each with a date, a reason and its successor. Add VMRK. The data-health page shows a separate "delisted" list so you can see what was removed.
- **C-3 (P3):** a stock that is only *stale* (for example a trading halt) is flagged and never deleted. That's already true in the code, so it only needs a test.
- **C-4 (P2, optional):** fix the old blocked list. Add BNY and MRSH, relabel CTRA, K and WBA as delisted with reasons, and remove all five old tickers from the watchlist.

## Design (system-architect; tech-expert approved with changes, which are folded in)
- `data/universe.py`: the bare blocked list becomes a small table: ticker → kind (`rename` / `stock_merger` / `cash_buyout` / `broken_data`), date, reason, successor. A derived set keeps every existing caller unchanged. Remove the 5 from the fallback lists and add VMRK (plus BNY and MRSH if C-4 is approved).
- `config/watchlist.txt`: remove the 5 and add VMRK.
- `api/data_health.py`: add a `delisted` list to the response, with a proper response model so it shows in the API docs.
- `api/data_sync.py`: **this closes a gap.** Today, `POST /data/sync {"tickers":["EQR"]}` would re-download a blocked ticker. Blocked names are now skipped and reported in a `skipped` field, and the request returns 422 if every name is blocked.
- **Data:**
  - delete the 3 untracked stubs (EA, AVB, EQR);
  - **archive** KVUE and WBD to `data/delisted/<T>_<date>.parquet`, trimmed to their last trading day, **or delete them**. It's a one-line difference either way;
  - fetch VMRK (and BNY/MRSH) and commit.
- **Tests first:** they use only `tmp_path`, never `data/raw`.
  - every blocked entry is valid;
  - no blocked ticker appears in any fallback list;
  - an offline universe sync can't bring them back;
  - `/data/sync` skips blocked names and returns 422 when every name is blocked;
  - `/data/health` lists exactly the delisted ones;
  - a stale ticker is flagged and its file left alone.
- **Rollback:** the code and data commits are separate, so either can be reverted on its own.

## Risks
- **Survivorship bias:** deleting KVUE and WBD history means backtests forget that these stocks existed. Archiving keeps the data. Nothing reads the archive yet, so it doesn't change any results until a future "point-in-time universe" story uses it.
- **KVUE and WBD gaps:** their cached history is missing a few weeks before delisting (Sept–Oct), because Yahoo stopped serving bars. Archived files will carry that gap, labelled as such.
- **Reused tickers:** every blocked entry has a date, so if a symbol comes back for a different company, it can be unblocked on purpose.
- Your paper bot holds none of these tickers (checked).

## Decisions for A
1. **KVUE and WBD history: archive (recommended) or delete?**
2. **Include C-4** (add BNY and MRSH, fix the old blocked list)? Recommended: yes. It's the same change and restores 2 live S&P 500 stocks.
