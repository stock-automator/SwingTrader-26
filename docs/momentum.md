# Ranked cross-sectional momentum (cycles 04 and 04b)

Generated 2026-11-24 01:20 UTC by `scripts/run_momentum.py` in 21 s. Code: `backend/app/quant/momentum.py`, tests: `tests/test_momentum.py`. Cycle 04b replaced today's universe with point-in-time S&P 500 membership; the cycle 04 numbers are kept in the test table as "Today's universe CAGR %".

## Data

- Membership: `config/sp500_membership.csv`, one snapshot per month end from 2015-12 plus the build day, 724 distinct tickers, built by `scripts/build_sp500_membership.py`. It starts from today's Wikipedia "List of S&P 500 companies" and walks back through the change log, which in 2026 moved from that page to "Historical components of the S&P 500". Joiners renamed later keep today's symbol (FLT is CPAY, CDAY is DAY); one change the log omits (SW for WRK, 2024-07-08) is added by hand. Replaying the log forward from 2015-12 gives today's list exactly.
- Universe: S&P 500 members only. Nasdaq-100 history was skipped, so the Nasdaq-100-only names of cycle 04 (ASML, MELI, PDD, SHOP and others) are out.
- Prices: current members from `data/raw` (yfinance, total-return adjusted via Adj Close / Close); removed members from `data/raw_pit` (`scripts/fetch_pit_prices.py`; alpaca 199, yfinance 3). Alpaca bars are split and dividend adjusted and start 2016-01-04. Each member's bars are cut 45 days after it left. Spliced data excluded (`portfolio.suspect_spike`): none. Members with no bars: none.
- Coverage: 65,497 of 65,540 member-months (99.93%) have a bar on the rebalance day. With the full lookback: 6-1 98.4%, 12-1 96.8%.
- Build 2016-01-01 to 2022-12-30, test 2023-01-01 to 2026-11-20. Each period starts at $10,000 at the close before its first day, and ranks use full history, so the test period starts warmed up.

## Rules

- Rebalance on the last trading day of each month using bars up to that close; trades fill at the next session's open. The unfinished last month is never a rebalance.
- Eligible: an S&P 500 member in that month's snapshot, a bar on the rank day, close >= $5, 20-day average dollar volume >= $20M, and the full lookback of history.
- Rank by total return from 252 (12-1) or 126 (6-1) bars ago to 21 bars ago.
- Hold the top N (10 or 20) at 1/N of equity each. A holding still eligible and ranked in the top 2N is kept; one that left the index is sold. Every holding is re-weighted to 1/N at each rebalance.
- Filter variant: SPY close below its 200-day SMA at the rebalance close means all cash until the next rebalance.
- Slippage 0.05% per side on traded notional, no commission, fractional shares, cash earns 0, $10,000 start.
- A holding with no bar on a day (buyout, merger, delisting) is sold at its last close less slippage ("delisted exits"). A holding with a close but no open on a rebalance day is kept at its last close until the next rebalance.
- Equal-weight universe: every member with a bar on the rebalance day, equal weight, same timing and costs. SPY buy and hold: adjusted close, no costs.
- Turnover per rebalance = max(buys, sells) at the open / equity at the open, averaged over rebalances (the first rebalance from cash counts 100%). Exposure = % of days holding anything; avg invested = long value / equity.

## Selection

Chosen on build-period CAGR / |max drawdown| (MAR), before looking at the test period: **6-1 N10** (build MAR 0.24).

## Build period (2016-01-01 to 2022-12-30)

| Run | CAGR % | Total % | Max DD % | Sharpe | Pos. years % | Turnover %/mo | Avg holdings | Exposure % | Avg invested % | Delisted exits | CAGR/MaxDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12-1 N10 | 4.5 | 36.5 | -43.4 | 0.30 | 71 | 25.7 | 9.9 | 98.9 | 98.7 | 1 | 0.10 |
| 12-1 N10 SPY>SMA200 | -1.4 | -9.7 | -39.2 | 0.04 | 43 | 33.6 | 7.9 | 78.7 | 78.6 | 1 | -0.04 |
| 12-1 N20 | 8.3 | 74.6 | -38.1 | 0.44 | 86 | 22.6 | 19.8 | 98.9 | 98.7 | 4 | 0.22 |
| 12-1 N20 SPY>SMA200 | 0.2 | 1.6 | -35.2 | 0.11 | 57 | 30.9 | 15.7 | 78.7 | 78.5 | 3 | 0.01 |
| **6-1 N10 (chosen)** | 8.8 | 80.2 | -36.0 | 0.44 | 86 | 40.4 | 10.0 | 100.0 | 99.5 | 6 | 0.24 |
| 6-1 N10 SPY>SMA200 | 0.9 | 6.6 | -37.3 | 0.15 | 57 | 42.7 | 7.8 | 78.7 | 78.2 | 6 | 0.02 |
| 6-1 N20 | 8.8 | 80.1 | -42.5 | 0.46 | 86 | 38.2 | 20.0 | 100.0 | 99.7 | 6 | 0.21 |
| 6-1 N20 SPY>SMA200 | 2.1 | 16.0 | -37.7 | 0.21 | 57 | 41.8 | 15.7 | 78.7 | 78.4 | 6 | 0.06 |
| Equal-weight universe | 11.6 | 115.5 | -39.7 | 0.64 | 71 | 4.5 | 502.0 | 100.0 | 99.8 | 75 | n/a |
| SPY buy & hold | 11.4 | 112.8 | -33.7 | 0.66 | 71 | 0.0 | 1.0 | 100.0 | 100.0 | 0 | n/a |
| zscore_mean_reversion (cycle 03) | 8.3 | 74.0 | -22.2 | n/a | 86 | n/a | n/a | 99.9 | 85.6 | n/a | n/a |

## Test period (2023-01-01 to 2026-11-20)

| Run | CAGR % | Total % | Max DD % | Sharpe | Pos. years % | Turnover %/mo | Avg holdings | Exposure % | Avg invested % | Delisted exits | CAGR/MaxDD | Today's universe CAGR % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12-1 N10 | 15.4 | 74.7 | -53.7 | 0.56 | 75 | 22.6 | 10.0 | 100.0 | 99.9 | 0 | 0.29 | 78.0 |
| 12-1 N10 SPY>SMA200 | -3.2 | -12.1 | -52.1 | 0.11 | 50 | 31.7 | 8.9 | 89.3 | 89.3 | 0 | -0.06 | 46.5 |
| 12-1 N20 | 14.6 | 70.1 | -47.6 | 0.56 | 75 | 21.7 | 20.0 | 100.0 | 99.9 | 0 | 0.31 | 47.4 |
| 12-1 N20 SPY>SMA200 | -1.6 | -6.0 | -47.2 | 0.12 | 50 | 30.7 | 17.9 | 89.3 | 89.3 | 0 | -0.03 | 25.1 |
| **6-1 N10 (chosen)** | 33.5 | 207.6 | -39.4 | 0.93 | 100 | 39.3 | 10.0 | 100.0 | 99.9 | 0 | 0.85 | 86.4 |
| 6-1 N10 SPY>SMA200 | 13.6 | 64.0 | -39.4 | 0.53 | 100 | 43.4 | 8.9 | 89.3 | 89.3 | 0 | 0.34 | 52.3 |
| 6-1 N20 | 23.1 | 124.6 | -32.0 | 0.81 | 100 | 36.7 | 20.0 | 100.0 | 99.9 | 0 | 0.72 | 47.3 |
| 6-1 N20 SPY>SMA200 | 3.1 | 12.5 | -33.5 | 0.25 | 50 | 42.5 | 17.9 | 89.3 | 89.3 | 0 | 0.09 | 22.2 |
| Equal-weight universe | 12.1 | 55.9 | -17.7 | 0.88 | 100 | 5.3 | 502.8 | 100.0 | 99.9 | 20 | n/a |  |
| SPY buy & hold | 17.8 | 89.1 | -18.8 | 1.16 | 100 | 0.0 | 1.0 | 100.0 | 100.0 | 0 | n/a |  |
| zscore_mean_reversion (cycle 03) | 11.3 | 51.5 | -19.8 | n/a | 100 | n/a | n/a | 99.7 | 86.9 | n/a | n/a |  |

Chosen variant on the test period: CAGR 33.5% (today's universe, cycle 04: 86.4%), total 207.6%, max drawdown -39.4%, Sharpe 0.93; SPY: CAGR 17.8%, max drawdown -18.8%. Calendar-year returns, chosen variant: build 2016: 5.8%, 2017: 15.9%, 2018: -14.4%, 2019: 26.4%, 2020: 17.0%, 2021: 15.4%, 2022: 0.6%; test 2023: 30.6%, 2024: 15.3%, 2025: 34.2%, 2026: 52.2%.

Where the gap comes from: on the test period the chosen variant made 86.4% a year on today's universe, 87.7% on today's 503 S&P 500 members without the membership rule (Nasdaq-100-only names out, survivorship in) and 33.5% point-in-time.

Delisted exits, chosen variant: build ANDV, APC, ESRX, TSS, WFM, XLNX; test none.

The zscore row is copied from `data/reports/scorecard.json` (cycle 03, today's universe, so still survivorship-biased): it ran under different rules (whole shares, 1% risk per trade, 6 positions, 0.1% commission plus 0.05% spread per leg, FX), and its Sharpe, turnover and holdings were not recorded, so it is shown as reported.

## Caveats

- The change log is Wikipedia's "selected changes", checked only for internal consistency (every walk step fits, the replay gives today's list, 503-505 names every month). A change it misses would leave a name in or out for the wrong months.
- Removed members have no bars before 2016-01-04 (Alpaca's start), so in 2016 some of them could not be ranked while survivors could: a small survivorship bias left in the first build year, visible in the lookback coverage above.
- Alpaca follows a symbol to whoever keeps it. Splices found by scanning for one-day moves over 40% are cut (AA 2016-11, ARNC 2020-04, DWDP 2019-06, WRK before 2016-05-16, 21st Century Fox after 2019-03-19); smaller splices or spin-off adjustments could remain. XL has no bars before its 2016-07 redomicile.
- One price file per symbol. The 43 unpriced member-months are mostly reused symbols whose file now belongs to a later company with no bars that early: DOW (Dow Chemical to 2017-08), SNDK (SanDisk to 2016-05), Q (Quintiles to 2017-11); the rest are XL before 2016-07, WRK before 2016-05-16 and final months of buyouts. The one reused symbol with bars in the old member's months, DD (E.I. du Pont to 2017-08), tracks Alpaca's E.I. du Pont bars at a steady 1.99 price ratio (one-off check, 2026-11-24).
- A delisted exit sells at the last close; cash or shares paid in a merger after that close are not modelled.
- Adjustment artefacts in `data/raw` (cycle 04 check): 10 cached series have a one-day jump of more than 10% in Adj Close / Close (spin-off adjustments, e.g. DHR 2016-07-05). One-off check (2026-11-24): MRNA has a +177% close on 2026-08-19 that was not verified; 6-1 N20 (with or without the filter) held it.
- The $5 floor is checked on adjusted closes, which sit below the traded price early in the history.
- Only 0.05% slippage per side is charged: no commission, FX or market impact. Fractional shares make every position exact on a $10k account.
- The test period is under four years; one path per variant and one train/test split. Eight variants were run, so the chosen one's build result is the best of eight.
