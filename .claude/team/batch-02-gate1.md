# Batch 02: six features (🛑 Gate 1, awaiting A)

Tier: **full**. The batch touches orders, money and sizing, data integrity and strategy math.

Roles run:
- product-owner
- domain-expert and customer, in one combined round
- system-architect
- tech-expert

None skipped. Nothing is built yet.

## The features, in build order

| # | Feature | What you get | Size |
|---|---|---|---|
| F1 | **Guarded orders** | Every order route runs the safety checks. Exits always go through. **New entries are refused** if the market calendar or earnings lookup fails, on holidays, and after the early close on half-days. Uses Alpaca's real calendar. Deletes the "turn guards off" switch. | M |
| F6 | **UK FX cost** | A configurable FX cost (default 0.15% per conversion) **on top of** commission in backtests and the order-ticket estimate. Paper (Alpaca, USD) stays "no FX". £ figures come from cached GBPUSD. | S |
| F2 | **Fair backtests** | Optional fractional sizing (**backtests only**, because Alpaca can't do fractional stop/target orders). The same risk gate as live. A market-trend filter using the prior day's close (no peeking). "% of time invested" plus an exposure-matched SPY line. Out-of-sample results first, a "too few trades" warning, and a "current-constituent universe" label. A reproducible baseline run. | L |
| F3 | **Daily Telegram brief** | At 08:00 UK: up to 3 setups (entry, stop, target, shares, $ and £ risk, one-line reason), or "No trades today" with the reason. A stale-data warning, a day-trades-used line and a "research only, paper" footer. Plain text, split at 4096 characters. A README setup step. | M |
| F4 | **One ticker list, refreshed monthly** | `config/universe.json` becomes the only list. Refreshes on the 1st of each month. Detects renames (FI→FISV), fetches history for new names, and flags removed names (never deletes them). **Keeps the last good list and alerts you** if a source falls back. The "+X −Y" summary goes in the weekly report. | M |
| F5 | **Forward-test journal + weekly report** | Paper fills are pulled from Alpaca automatically, with no double-counting of partial fills or bracket legs. One Saturday 09:00 UK message: this week's trades, a cumulative record after costs, and a vs-SPY line **only once there are 30+ trades**. Includes the universe changes. | M |

## Design decisions (tech-expert corrections folded in)
- **Scheduling:** the backend runs always-on as **one launchd service** (bound to 127.0.0.1). Jobs use the app's existing APScheduler on **New York time**, so a missed job runs once when the Mac wakes. That's simpler than one plist per job, and it avoids UK/US clock-change drift. The DuckDB database allows one writer, so the backend stays that writer.
- **Hermes cron:** the swing and day scan jobs get **disabled**, not deleted. They're failing anyway (2 errors in a row, firing 15 minutes late).
- **Live sizing:** whole shares for live and paper orders, so the AGENTS.md order contract is unchanged. Fractional sizing is handled inside the backtest engine only.
- **Alpaca:** fills come from the account-activities API, through a call with its own timeout. The calendar is cached per day and converted to New York time.

## Will be deleted (your approval covers this list)
- APScheduler's old universe-only job code. The scheduler is reused, not removed.
- The `execution_guards_enabled` switch, plus the hand-coded holiday and weekday logic.
- `config/watchlist.txt` and `WATCHLIST_PATH`, replaced by `universe.json`. This touches about 14 files, and the expected test-count change is recorded before deletion.
- `scripts/swing_setup_scan.py`, `scripts/day_setup_scan.py` and their `~/.hermes/scripts` copies, replaced by the daily brief. Backlog item B-3 then no longer applies.
- **Depending on Q4:** the paper bot's own strategies, risk manager and data code, about 1,050 lines in `~/.hermes/bots/paper_trader`.
- LIVE rows in `data/trades_live.csv`, once fills are ingested. Simulated rows stay.

## Risks
- **Fail-closed entries:** if Alpaca keys are missing or Alpaca is down, no new paper entries go out, by design. Exits always work.
- **Backtest vs live sizing:** fractional backtest sizing ≠ whole-share live sizing. Every result that uses fractional sizing is labelled.
- **Survivorship:** backtests still use today's index members. They're labelled, and the archive keeps the data for a later fix.
- **Weekly report noise:** results stay noisy until about 30 trades. The report says so.
- **Bigger change:** removing `watchlist.txt` is the largest edit, and is planned as a separate step after F2.

## Questions for A
1. **Real broker / FX:** Trading 212 converting £→$ on every trade (0.15% each way)? Or do you keep a USD balance, which means no per-trade FX?
2. **Telegram sender:** put a bot token in your local `.env` so the app sends its own messages (recommended, testable)? Or go through `hermes send`?
3. **Always-on backend:** OK to run it as a background service on this Mac? Reachable from **this Mac only** (recommended), or also from your phone over your network?
4. **Bot role:** should the paper bot become a pure executor of the daily setups, retiring its own momentum, gap and short strategies (recommended: one source of truth, fewer day-trade-rule issues)? Or keep its intraday strategies too?
