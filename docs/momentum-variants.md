# Momentum variants on point-in-time S&P 500 (cycle 04c)

Generated 2026-11-24 01:48 UTC by `scripts/run_momentum_variants.py` in 13 s. Code: `backend/app/quant/momentum_variants.py`, tests: `tests/test_momentum_variants.py`. Data, universe, costs and timing are those of `docs/momentum.md` (cycle 04b): point-in-time S&P 500 membership, 724 priced tickers, decisions at the month-end close, fills at the next open, 0.05% slippage per side, fractional shares, $10,000 start.

## Candidates

The baseline is **6-1 N10**: 6-1 return (126 to 21 bars), top 10, equal weight, held names kept while in the top 20, no market filter. Every candidate uses the baseline's eligibility (member that month, close >= $5, $20M 20-day dollar volume, full 6-1 history) and holds 10 names. Published parameters, no tuning: nothing was varied on the build period, so each candidate is one run.

1. **Vol-scaled (15% target)** (Barroso and Santa-Clara 2015): the baseline's weights times min(1, 15% / annualised realised vol of the unscaled baseline's daily returns over the last 126 days, known at the rebalance close); the rest in cash, no leverage. The reference run starts with the membership data (first fill 2016-01-04); before 21 days of returns the scale is 1.0. Average invested: build 63.9%, test 51.9%.
2. **Smooth momentum (FIP)** (Da, Gurun and Warachka 2014): among the top 20 by 6-1 return, rank on sign(6-1 return) x (% up days - % down days) over the same 105 daily returns, hold the top 10 (no extra hold buffer: the top-20 pool is the filter).
3. **Residual momentum (vs SPY)** (Blitz, Huij and Martens 2011): each month end, regress up to 36 monthly returns (to that month end) on SPY's; score = sum of the residuals of the 5 months from 6 to 1 months ago / their standard deviation. Needs 12 monthly returns (not 36: prices start 2015-01 for current members and 2016-01 for removed ones). Hold buffer as the baseline.
4. **52-week-high proximity** (George and Hwang 2004): close / highest close of the last 252 bars (adjusted closes), ties by 6-1 return. Hold buffer as the baseline.
5. **Inverse-vol weighted**: the baseline's 10 names weighted by 1 / standard deviation of the last 60 daily returns.

Turnover = max(buys, sells) at the open / equity, averaged over monthly rebalances; vol-scaled and inverse-vol turnover includes re-weighting.

## Selection

Winner on build-period Sharpe (ties: shallower max drawdown), chosen before reading the test period: **Residual momentum (vs SPY)** (build Sharpe 0.89; baseline 0.44).

## Build period (2016-01-01 to 2022-12-30)

| Run | CAGR % | Max DD % | Sharpe | Pos. years % | Turnover %/mo | Avg invested % |
|---|---:|---:|---:|---:|---:|---:|
| 6-1 N10 | 8.8 | -36.0 | 0.44 | 86 | 40.4 | 99.5 |
| Vol-scaled (15% target) | 3.5 | -29.4 | 0.28 | 71 | 30.1 | 63.9 |
| Smooth momentum (FIP) | 9.4 | -37.4 | 0.48 | 86 | 58.7 | 99.9 |
| **Residual momentum (vs SPY) (winner)** | 17.9 | -42.5 | 0.89 | 71 | 51.5 | 98.8 |
| 52-week-high proximity | 5.6 | -29.1 | 0.39 | 57 | 82.8 | 99.5 |
| Inverse-vol weighted | 10.9 | -34.6 | 0.54 | 86 | 44.7 | 99.4 |
| SPY buy & hold | 11.4 | -33.7 | 0.66 | 71 | 0.0 | 100.0 |

## Test period (2023-01-01 to 2026-11-20), reported once

| Run | CAGR % | Max DD % | Sharpe | Pos. years % | Turnover %/mo | Avg invested % |
|---|---:|---:|---:|---:|---:|---:|
| 6-1 N10 | 33.5 | -39.4 | 0.93 | 100 | 39.3 | 99.9 |
| Vol-scaled (15% target) | 17.0 | -19.9 | 0.98 | 100 | 24.1 | 51.9 |
| Smooth momentum (FIP) | 16.3 | -43.1 | 0.60 | 100 | 58.4 | 100.0 |
| **Residual momentum (vs SPY) (winner)** | 14.3 | -19.4 | 0.77 | 100 | 55.4 | 99.9 |
| 52-week-high proximity | 3.8 | -32.8 | 0.28 | 50 | 84.1 | 99.3 |
| Inverse-vol weighted | 27.4 | -41.7 | 0.83 | 100 | 43.2 | 99.9 |
| SPY buy & hold | 17.8 | -18.8 | 1.16 | 100 | 0.0 | 100.0 |

Winner on the test period: CAGR 14.3%, max drawdown -19.4%, Sharpe 0.77; baseline Sharpe 0.93; SPY Sharpe 1.16. Calendar-year returns, winner: build 2016: 31.6%, 2017: 34.5%, 2018: 2.4%, 2019: 24.2%, 2020: -1.7%, 2021: 58.9%, 2022: -9.8%; test 2023: 2.8%, 2024: 17.9%, 2025: 24.7%, 2026: 11.5%.

No candidate beats SPY buy and hold on Sharpe in both periods, so none beats it on a risk-adjusted basis. Build-period Sharpe: SPY 0.66, best candidate 0.89.

## Caveats

- All caveats of `docs/momentum.md` apply (Wikipedia change log, removed members without bars before 2016-01-04, Alpaca symbol splices, delisted exits at the last close, slippage only).
- Residual momentum coverage, share of baseline-eligible member-months with a residual score: 2016 84%, 2017 100%, 2018 100%, 2019 100%, 2020 100%, 2021 100%, 2022 100%. Removed members need 12 monthly returns from 2016-01, so in 2016 only names with 2015 prices (today's members) can be ranked: a survivorship tilt in the first build year. One-off check (2026-11-24), build period 2017-2022 without 2016: residual Sharpe 0.75, CAGR 15.2%, max drawdown -42.5%, still the best candidate (inverse vol 0.53, baseline 0.45, SPY 0.64).
- Six runs on one build/test split; the winner's build Sharpe is the best of five, so it is biased upward. The test period is under four years.
- Sharpe uses daily returns with a zero risk-free rate (`metrics.py`); cash in the vol-scaled run earns nothing, which understates it when rates are positive.
