# Ranked cross-sectional momentum (cycle 04)

Generated 2026-11-24 00:47 UTC by `scripts/run_momentum.py` in 12 s. Code: `backend/app/quant/momentum.py`, tests: `tests/test_momentum.py`.

## Data

- Daily cache `data/raw`, total-return adjusted (OHLC scaled by Adj Close / Close, as `load_prices` does), on SPY's trading calendar.
- Universe: 518 symbols in `config/universe.json`; 517 used. Not cached: none.
- Excluded as spliced data (`portfolio.suspect_spike`): FER.
- Build 2016-01-01 to 2022-12-30, test 2023-01-01 to 2026-11-20. Each period starts at $10,000 at the close before its first day, and ranks use full history, so the test period starts warmed up.

## Rules

- Rebalance on the last trading day of each month using bars up to that close; trades fill at the next session's open. The unfinished last month is never a rebalance.
- Rank by total return from 252 (12-1) or 126 (6-1) bars ago to 21 bars ago.
- Eligible: a bar on the rank day, close >= $5, 20-day average dollar volume >= $20M, and the full lookback of history.
- Hold the top N (10 or 20) at 1/N of equity each. A holding still ranked in the top 2N is kept. Every holding is re-weighted to 1/N at each rebalance.
- Filter variant: SPY close below its 200-day SMA at the rebalance close means all cash until the next rebalance.
- Slippage 0.05% per side on traded notional, no commission, fractional shares, cash earns 0, $10,000 start.
- A holding with no bar on a day is sold at its last close less slippage ("delisted exits").
- Equal-weight universe: every used ticker with a bar on the rebalance day, equal weight, same timing and costs. SPY buy and hold: adjusted close, no costs.
- Turnover per rebalance = max(buys, sells) at the open / equity at the open, averaged over rebalances (the first rebalance from cash counts 100%). Exposure = % of days holding anything; avg invested = long value / equity.

## Selection

Chosen on build-period CAGR / |max drawdown| (MAR), before looking at the test period: **6-1 N10** (build MAR 1.01). The scorecard composite was not used: 70% of its weight is test-period data and most of its criteria are per-trade stats that do not fit a monthly rebalance.

## Build period (2016-01-01 to 2022-12-30)

| Run | CAGR % | Total % | Max DD % | Sharpe | Pos. years % | Turnover %/mo | Avg holdings | Exposure % | Avg invested % | Delisted exits | CAGR/MaxDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12-1 N10 | 31.5 | 581.0 | -38.4 | 0.98 | 86 | 21.0 | 9.9 | 98.9 | 98.8 | 0 | 0.82 |
| 12-1 N10 SPY>SMA200 | 16.8 | 196.5 | -37.9 | 0.73 | 71 | 30.6 | 7.9 | 78.7 | 78.6 | 0 | 0.44 |
| 12-1 N20 | 24.8 | 371.3 | -39.9 | 0.91 | 100 | 20.7 | 19.8 | 98.9 | 98.8 | 0 | 0.62 |
| 12-1 N20 SPY>SMA200 | 13.5 | 143.3 | -31.8 | 0.69 | 86 | 29.4 | 15.7 | 78.7 | 78.6 | 0 | 0.43 |
| **6-1 N10 (chosen)** | 40.7 | 989.6 | -40.4 | 1.21 | 100 | 35.6 | 10.0 | 100.0 | 99.9 | 0 | 1.01 |
| 6-1 N10 SPY>SMA200 | 25.6 | 393.4 | -33.9 | 1.03 | 86 | 39.5 | 7.9 | 78.7 | 78.6 | 0 | 0.76 |
| 6-1 N20 | 32.6 | 620.2 | -35.0 | 1.16 | 100 | 34.1 | 20.0 | 100.0 | 99.9 | 0 | 0.93 |
| 6-1 N20 SPY>SMA200 | 19.5 | 248.6 | -29.9 | 0.95 | 86 | 38.9 | 15.7 | 78.7 | 78.6 | 0 | 0.65 |
| Equal-weight universe | 16.7 | 194.0 | -38.1 | 0.87 | 71 | 3.9 | 488.1 | 100.0 | 99.9 | 0 | n/a |
| SPY buy & hold | 11.4 | 112.8 | -33.7 | 0.66 | 71 | 0.0 | 1.0 | 100.0 | 100.0 | 0 | n/a |
| zscore_mean_reversion (cycle 03) | 8.3 | 74.0 | -22.2 | n/a | 86 | n/a | n/a | 99.9 | 85.6 | n/a | n/a |

## Test period (2023-01-01 to 2026-11-20)

| Run | CAGR % | Total % | Max DD % | Sharpe | Pos. years % | Turnover %/mo | Avg holdings | Exposure % | Avg invested % | Delisted exits | CAGR/MaxDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12-1 N10 | 78.0 | 841.8 | -53.8 | 1.41 | 100 | 19.2 | 10.0 | 100.0 | 99.9 | 0 | 1.45 |
| 12-1 N10 SPY>SMA200 | 46.5 | 341.3 | -52.7 | 1.04 | 75 | 28.7 | 8.9 | 89.3 | 89.3 | 0 | 0.88 |
| 12-1 N20 | 47.4 | 352.8 | -50.7 | 1.14 | 100 | 19.7 | 20.0 | 100.0 | 99.9 | 0 | 0.94 |
| 12-1 N20 SPY>SMA200 | 25.1 | 138.7 | -51.7 | 0.77 | 75 | 29.0 | 17.9 | 89.3 | 89.3 | 0 | 0.48 |
| **6-1 N10 (chosen)** | 86.4 | 1027.4 | -41.9 | 1.55 | 100 | 34.9 | 10.0 | 100.0 | 99.9 | 0 | 2.06 |
| 6-1 N10 SPY>SMA200 | 52.3 | 413.9 | -40.9 | 1.17 | 100 | 41.2 | 8.9 | 89.3 | 89.3 | 0 | 1.28 |
| 6-1 N20 | 47.3 | 351.1 | -42.9 | 1.16 | 100 | 32.1 | 20.0 | 100.0 | 99.9 | 0 | 1.10 |
| 6-1 N20 SPY>SMA200 | 22.2 | 118.4 | -44.5 | 0.73 | 75 | 38.8 | 17.9 | 89.3 | 89.3 | 0 | 0.50 |
| Equal-weight universe | 19.0 | 97.0 | -18.1 | 1.27 | 100 | 5.3 | 509.3 | 100.0 | 99.9 | 0 | n/a |
| SPY buy & hold | 17.8 | 89.1 | -18.8 | 1.16 | 100 | 0.0 | 1.0 | 100.0 | 100.0 | 0 | n/a |
| zscore_mean_reversion (cycle 03) | 11.3 | 51.5 | -19.8 | n/a | 100 | n/a | n/a | 99.7 | 86.9 | n/a | n/a |

Chosen variant on the test period: CAGR 86.4%, total 1027.4%, max drawdown -41.9%, Sharpe 1.55 (SPY: CAGR 17.8%, max drawdown -18.8%). Calendar-year returns, chosen variant: build 2016: 21.7%, 2017: 42.9%, 2018: 46.8%, 2019: 45.2%, 2020: 103.1%, 2021: 28.7%, 2022: 12.4%; test 2023: 92.3%, 2024: 116.8%, 2025: 52.2%, 2026: 77.6%.

The zscore row is copied from `data/reports/scorecard.json` (cycle 03): it ran under different rules (whole shares, 1% risk per trade, 6 positions, 0.1% commission plus 0.05% spread per leg, FX), and its Sharpe, turnover and holdings were not recorded, so it is shown as reported.

## Chosen variant without the latest index additions

The universe is today's S&P 500 and Nasdaq-100. Names added to them recently were usually added because they had already risen, which is exactly what momentum buys. As a partial check, the chosen variant was rerun without the 41 names the latest universe sync added (ALAB, ALNY, ARES, BE, BF-B, BNY, BRK-B, CASY, CIEN, COHR, CRH, CRWV, CVNA, ECHO, EME, FDXF, FER, FERG, FISV, FIX, FLEX, HONA, HOOD, IBKR, ILMN, LITE, MRSH, NBIS, OKTA, P, Q, RDDT, RKLB, SKYD, SNDK, SPCX, TWLO, VEEV, VMRK, VRT, VYLR). Earlier additions stay in, so this understates the bias. It is a check only, not a selection input.

| Run | CAGR % | Total % | Max DD % | Sharpe | Pos. years % | Turnover %/mo | Avg holdings | Exposure % | Avg invested % | Delisted exits | CAGR/MaxDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 6-1 N10, build, without additions | 38.7 | 886.3 | -39.4 | 1.21 | 100 | 37.3 | 10.0 | 100.0 | 99.9 | 0 | n/a |
| 6-1 N10, test, without additions | 48.6 | 367.0 | -42.9 | 1.15 | 100 | 36.7 | 10.0 | 100.0 | 99.9 | 0 | n/a |

## Caveats

- Survivorship and index-inclusion bias: the universe is today's index members, which is why no run had a single delisted exit. Names that fell out, often after crashing, are missing; that hurts mean reversion (which buys falling names) more than momentum, which rarely holds a name through a long slide. But the opposite bias, names that joined the index after a big run, favours momentum directly: the check above removes only the latest additions. These results overstate what the rules would have earned by an unknown, probably large, amount. A point-in-time membership list is needed before trusting any of these numbers.
- Adjustment artefacts (one-off check, 2026-11-24): 10 cached series have a one-day jump of more than 10% in Adj Close / Close (spin-off adjustments, e.g. DHR 2016-07-05 +56%). No variant held a name across one, so none creates P&L, but each can distort that name's rank for up to a year.
- The $5 floor is checked on dividend-adjusted closes, which sit below the traded price early in the history, so it may have excluded a few names that a live run would have allowed.
- Only 0.05% slippage per side is charged: no commission, FX or market impact. Fractional shares make every position exact on a $10k account.
- The test period is under four years and dominated by a few themes (AI hardware and storage); one path per variant and one train/test split. Eight variants were run, so the chosen one's build result is the best of eight.
