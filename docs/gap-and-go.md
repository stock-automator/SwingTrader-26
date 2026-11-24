# Gap-and-Go day trades (cycle 03 add-on)

Generated 2026-11-23 by `scripts/gap_and_go.py backtest` (4.3 min) from bars
fetched by `scripts/gap_and_go.py download`. Code: `backend/app/quant/intraday/`,
tests: `tests/test_gap_and_go.py`.

## Data

- Alpaca SIP 5-minute bars, `adjustment=split`, regular session only (09:30-16:00
  New York), candidate gap days only, stored in `data/intraday/gap_bars/<TICKER>.parquet`
  (git-ignored, 503 tickers, 8,261,927 bars, 243 MB).
- Span: 2016-01-04 to 2026-11-20 (the full 2016 start was used). A 20-session
  sample took 11.9 s (22 requests, 0.54 s each), which predicted ~28 min; the full
  run took 28.9 min: 2,733 sessions, 3,085 requests, 0 failures, at most 180 requests/min.
- Candidate days come from the daily cache (`data/raw`, split- but not
  dividend-adjusted closes) for the 517 symbols in `config/universe.json` minus FER
  (spliced bars). |open / previous close - 1| >= 2% (1.6% downloaded for the
  robustness check), open >= $5, mean volume of the 20 *prior* sessions >= 1M.
  14 universe names never pass the volume filter and have no bars.
- Ticker-days at 2% / 2x: 33,596 trade signals, 22,348 no breakout before 11:00,
  13,381 failed volume confirmation, 15 range too tight, 394 no bars returned,
  259 no 09:30 bar, and 2,267 skipped because the first 5-minute open was more
  than 3% from the daily cache's open. Most of the mismatches have a constant
  ratio per ticker (BDX 1.30, DD 2.39, FDX 1.24, GE 1.67): spin-off adjustments
  that exist in the Yahoo cache but not in Alpaca's split-only bars.

## Rules

Long trades a gap up, short a gap down, mirrored. The opening range is the
09:30 bar, and its volume must be at least 2 x (20-day ADV / 78). Entry is the first
later bar starting before 11:00 that breaks the range high (or low for a short), filled at the level, or at the
bar's open if it opens beyond it. The stop is the other side of the range; the
trade is skipped if the planned risk is under 0.2% of the price. The target is
2R of the filled risk. If neither stop nor target is hit, the trade exits at the 15:55 bar close. A bar that
touches both counts as a stop, including the entry bar. A later bar that opens beyond the stop
fills at its open. Slippage is 0.05% per side and there is no commission. Shorts are counted as needing a
borrow, but borrow cost is not modelled.

Portfolio: $10,000 start, 1% of start-of-day equity at risk per trade, 25%
notional cap, whole shares, flat every night. At 09:35 the top 3 qualified
setups by gap size x first-bar relative volume get orders. An order that never
fills still uses its slot, and fills are taken in time order. PDT variant: at
most 3 day trades in any rolling 5 sessions. Build is 2016-2022 and test is 2023
to 2026-11-20, each starting at $10,000. Nothing was tuned: the rules ran as
specified, and the ±20% check below is build-only. SPY B&H is the total return
(adjusted close) from the close before each period.

## Results

Build (2016-01-04 to 2022-12-30)

| Variant | Trades | Win | Payoff | PF | Exp (R) | Total | CAGR | Max DD | Sharpe | Pos. years | SPY B&H |
|---|---|---|---|---|---|---|---|---|---|---|---|
| long | 2316 | 41.2% | 1.24 | 0.87 | -0.050 | -53.4% | -10.4% | -54.2% | -0.93 | 0.0% | 112.8% |
| short | 2153 | 42.9% | 1.25 | 0.94 | -0.021 | -27.7% | -4.5% | -37.9% | -0.36 | 42.9% | 112.8% |
| both | 2916 | 42.8% | 1.26 | 0.94 | -0.026 | -38.2% | -6.6% | -48.0% | -0.49 | 14.3% | 112.8% |
| long (PDT) | 946 | 40.1% | 1.17 | 0.78 | -0.091 | -44.6% | -8.1% | -46.8% | -1.27 | 14.3% | 112.8% |
| short (PDT) | 932 | 40.6% | 1.26 | 0.86 | -0.059 | -28.9% | -4.8% | -32.7% | -0.66 | 28.6% | 112.8% |
| both (PDT) | 1013 | 41.4% | 1.22 | 0.86 | -0.051 | -31.8% | -5.3% | -37.0% | -0.72 | 28.6% | 112.8% |

Test (2023-01-03 to 2026-11-20)

| Variant | Trades | Win | Payoff | PF | Exp (R) | Total | CAGR | Max DD | Sharpe | Pos. years | SPY B&H |
|---|---|---|---|---|---|---|---|---|---|---|---|
| long | 1494 | 44.4% | 1.27 | 1.01 | -0.010 | 5.6% | 1.4% | -19.0% | 0.17 | 50.0% | 89.1% |
| short | 1399 | 41.5% | 1.24 | 0.88 | -0.072 | -36.8% | -11.2% | -45.1% | -0.82 | 0.0% | 89.1% |
| both | 1670 | 44.1% | 1.22 | 0.96 | -0.030 | -17.4% | -4.8% | -29.4% | -0.28 | 25.0% | 89.1% |
| long (PDT) | 549 | 46.8% | 1.28 | 1.13 | 0.047 | 21.0% | 5.0% | -11.3% | 0.63 | 75.0% | 89.1% |
| short (PDT) | 541 | 46.0% | 1.20 | 1.02 | -0.032 | 3.4% | 0.9% | -13.3% | 0.15 | 75.0% | 89.1% |
| both (PDT) | 566 | 44.2% | 1.15 | 0.91 | -0.025 | -15.2% | -4.1% | -30.8% | -0.47 | 25.0% | 89.1% |

Robustness (build, both directions, no PDT)

| Gap / vol | Trades | Win | Payoff | PF | Exp (R) | Total | CAGR | Max DD | Sharpe | Pos. years | SPY B&H |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1.6% / 2.0x | 3038 | 42.4% | 1.25 | 0.92 | -0.038 | -48.1% | -9.0% | -53.9% | -0.69 | 14.3% | 112.8% |
| 2.4% / 2.0x | 2738 | 43.1% | 1.25 | 0.95 | -0.022 | -33.9% | -5.8% | -42.7% | -0.42 | 28.6% | 112.8% |
| 2.0% / 1.6x | 2919 | 42.8% | 1.26 | 0.94 | -0.026 | -38.3% | -6.7% | -48.3% | -0.50 | 14.3% | 112.8% |
| 2.0% / 2.4x | 2909 | 42.9% | 1.26 | 0.94 | -0.025 | -37.7% | -6.5% | -48.1% | -0.48 | 14.3% | 112.8% |

Short-side trades needing a borrow (both, no PDT): 1,431 build, 754 test.

## Reading

Expectancy is negative in every build variant and every robustness rerun, and
SPY beat all of them by a wide margin. The only positive test row, long-only under PDT
(+21%, 0.047R), comes from a thinner subsample of a rule that lost 44.6% in
build. With 12 test rows, one positive row is what chance would produce. That
is not evidence of an edge.

## Caveats

- Survivorship: the universe is today's S&P 500 + Nasdaq-100, so names that
  dropped out or were delisted since 2016 are missing.
- Borrow: short fees, hard-to-borrow names, locates and the uptick rule (SSR
  after a 10% drop) are not modelled; shorts are only flagged.
- 5-minute granularity: the order of the high and low inside a bar is
  unknown, so a bar touching both stop and target counts as a stop. Real fills
  on fast opening breakouts can be worse than 0.05%.
- Price, volume and ADV filters use split-adjusted cache values, so some
  pre-split names (for example NVDA before 2021) are filtered on today's share
  basis. Spin-off-adjusted days (2,267) were skipped.
