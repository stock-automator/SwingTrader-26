# Cycle 06: SPY intraday momentum and sector-ETF momentum

Backtests 1 and 3 from `docs/research/strategy-evidence-2026-11.md`. The rules
were pre-registered there and are implemented exactly, with no tuning. There
was nothing to select, so every variant is reported. Run it with
`python scripts/run_etf_strategies.py [--refresh]`. The code is in
`backend/app/quant/etf_strategies.py` and the tests are in
`tests/test_etf_strategies.py`.

## Data

- Source: Alpaca SIP, `adjustment=all` (split and dividend). Alpaca history
  starts on 2016-01-04, so no bars from 2015 are available.
- Caches (gitignored):
  - SPY 30-minute bars, one file per year, plus the Alpaca trading calendar, in
    `data/intraday/spy/`.
  - Sector and SPY daily bars in `data/raw_etf/`.
- Fetches end at midnight New York, so bars from an unfinished session are
  never cached. The current year and the calendar are refetched on every run.
  Daily bars are refetched only with `--refresh`.
- Checks:
  - No daily move above 40% in any ETF. The largest are around March 2020.
  - Calendar and bars match: 2,739 sessions. Only the first session is
    dropped, because it has no previous close.
- **XLF fix:** Alpaca leaves the 2016-09-19 XLRE spin-off unadjusted in XLF,
  which shows as a -17.8% bar. Bars before that date are scaled by the ex-date
  open divided by the previous close. This assumes no market move over that
  gap; SPY gapped +0.36% that morning. The fix moves Rule A's build result
  from 102.0% to 102.4%; the other rows are unchanged at one decimal place.

## 1. SPY intraday momentum (Gao, Han, Li & Zhou 2018)

- **Signal:** the return from the previous session's close to 10:00 New York.
  The 10:00 price is the close of the 09:30 bar.
- **Trade:** SPY from the open of the last 30-minute bar to its close, long if
  the signal is above 0 and short if it is below. Sizing is 100% of equity
  notional with no leverage.
- **Close price:** "16:00 close" means the close of the 15:30 bar, the last
  trade before 16:00. The closing-auction print falls in Alpaca's 16:00 bar
  and is not used.
- **Half days:** traded, not skipped. On the 21 early-close sessions the trade
  is the 12:30–13:00 bar, and the next day's signal starts from that 13:00
  close.
- **Variants:**
  - (a) every day.
  - (b) PDT-capped: trade only when |signal| is at or above its trailing
    252-session 80th percentile, computed from previous days only, with at
    most 3 trades in any 5 sessions, taken first come first served.
  - (c) long-only versions of (a) and (b). The cap counts only the trades
    actually taken.
- **Costs:** 1bp and 5bp per side. Break-even per side = mean gross trade / 2.
- **Periods:** every row starts on 2017-01-04, the first day the PDT
  threshold has 252 previous sessions. Build runs to 2022-12-30; test runs
  from 2023-01-03 to 2026-11-23.
- **Exposure match:** "SPY long same days" is the average 15:30–16:00 SPY
  return on the days the variant trades. The "SPY long 15:30–16:00 daily" row
  is that window held every session, gross of costs.

## 3. Sector-ETF momentum (Moskowitz & Grinblatt 1999; Moskowitz, Ooi & Pedersen 2012)

- **Universe:** XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY, XLRE and XLC.
  Alpaca data starts on 2016-01-04 (XLC on 2018-06-19). Each ETF joins once
  it has the lookback plus one bar of history.
- **Rule A:** at each month-end close, rank by 126-bar total return, hold the
  top 3 at equal weight, and trade at the next open.
- **Rule B:** at each month-end, hold every sector with a 252-bar return above
  0, at equal weight, or cash if none qualifies. There is no T-bill data, so
  the hurdle is 0, which flatters Rule B in 2023–2026 when T-bills paid about
  4–5%.
- **Execution and costs:** weights drift between rebalances. Costs are 2bp per
  side on turnover. The equal-weight benchmark rebalances monthly at the same
  cost.
- **Periods:** every row starts on 2017-02-01, the first execution with a
  12-month history. Test runs from 2023-01-03.

### SPY intraday momentum, build (2017-01-04 to 2022-12-30)

| Row | Trades | Hit | Avg bp net | Gross bp | Break-even bp/side | SPY long same days bp | Total | CAGR | Max DD | Sharpe | Pos. years |
|---|---|---|---|---|---|---|---|---|---|---|---|
| a every day @1bp | 1507 | 42.7% | -2.7 | -0.7 | -0.34 | -1.1 | -33.9% | -6.7% | -34.2% | -1.15 | 16.7% |
| a every day @5bp | 1507 | 25.9% | -10.7 | -0.7 | -0.34 | -1.1 | -80.2% | -23.7% | -80.2% | -4.58 | 0.0% |
| b PDT-capped @1bp | 317 | 44.5% | -5.0 | -3.0 | -1.50 | 2.9 | -15.1% | -2.7% | -15.8% | -0.65 | 16.7% |
| b PDT-capped @5bp | 317 | 30.3% | -13.0 | -3.0 | -1.50 | 2.9 | -34.1% | -6.7% | -34.1% | -1.66 | 0.0% |
| c long-only every day @1bp | 840 | 40.5% | -3.6 | -1.6 | -0.80 | -1.6 | -26.4% | -5.0% | -27.0% | -1.36 | 16.7% |
| c long-only every day @5bp | 840 | 22.9% | -11.6 | -1.6 | -0.80 | -1.6 | -62.5% | -15.1% | -62.5% | -4.26 | 0.0% |
| c long-only PDT-capped @1bp | 186 | 43.5% | -2.2 | -0.2 | -0.12 | -0.2 | -4.3% | -0.7% | -9.5% | -0.25 | 16.7% |
| c long-only PDT-capped @5bp | 186 | 27.4% | -10.2 | -0.2 | -0.12 | -0.2 | -17.5% | -3.2% | -17.8% | -1.14 | 16.7% |
| SPY buy-and-hold | - | n/a | n/a | n/a | n/a | n/a | 87.7% | 11.1% | -33.8% | 0.63 | 66.7% |
| SPY long 15:30-16:00 daily (gross) | 1509 | 47.5% | -1.1 | -1.1 | n/a | n/a | -16.4% | -2.9% | -20.1% | -0.48 | 33.3% |

SPY average 15:30-16:00 drift: -1.12 bp per session.

### SPY intraday momentum, test (2023-01-03 to 2026-11-23)

| Row | Trades | Hit | Avg bp net | Gross bp | Break-even bp/side | SPY long same days bp | Total | CAGR | Max DD | Sharpe | Pos. years |
|---|---|---|---|---|---|---|---|---|---|---|---|
| a every day @1bp | 974 | 43.4% | -1.8 | 0.2 | 0.11 | 0.1 | -16.1% | -4.4% | -19.7% | -1.30 | 0.0% |
| a every day @5bp | 974 | 26.4% | -9.8 | 0.2 | 0.11 | 0.1 | -61.5% | -21.8% | -61.5% | -7.12 | 0.0% |
| b PDT-capped @1bp | 171 | 42.1% | -3.8 | -1.8 | -0.90 | 0.1 | -6.4% | -1.7% | -7.3% | -0.93 | 0.0% |
| b PDT-capped @5bp | 171 | 29.2% | -11.8 | -1.8 | -0.90 | 0.1 | -18.3% | -5.1% | -18.9% | -2.71 | 0.0% |
| c long-only every day @1bp | 566 | 44.0% | -1.7 | 0.3 | 0.13 | 0.3 | -9.5% | -2.5% | -12.9% | -1.08 | 0.0% |
| c long-only every day @5bp | 566 | 26.7% | -9.7 | 0.3 | 0.13 | 0.3 | -42.5% | -13.3% | -43.1% | -5.73 | 0.0% |
| c long-only PDT-capped @1bp | 102 | 41.2% | -4.7 | -2.7 | -1.33 | -2.7 | -4.7% | -1.2% | -5.5% | -1.05 | 25.0% |
| c long-only PDT-capped @5bp | 102 | 26.5% | -12.7 | -2.7 | -1.33 | -2.7 | -12.1% | -3.3% | -12.8% | -2.56 | 0.0% |
| SPY buy-and-hold | - | n/a | n/a | n/a | n/a | n/a | 98.1% | 19.3% | -18.7% | 1.25 | 100.0% |
| SPY long 15:30-16:00 daily (gross) | 977 | 50.4% | 0.1 | 0.1 | n/a | n/a | 0.5% | 0.1% | -4.1% | 0.05 | 50.0% |

SPY average 15:30-16:00 drift: 0.07 bp per session.

### Sector-ETF momentum, build (2017-02-01 to 2022-12-30)

| Row | Total | CAGR | Max DD | Sharpe | Pos. years | Avg held | Months in cash |
|---|---|---|---|---|---|---|---|
| Rule A top-3 6m | 102.4% | 12.7% | -29.6% | 0.71 | 83.3% | 3.0 | 0.0% |
| Rule B 12m > 0 | 77.7% | 10.2% | -33.0% | 0.63 | 66.7% | 8.2 | 0.0% |
| Equal-weight sectors (monthly) | 78.8% | 10.3% | -36.2% | 0.61 | 66.7% | 10.8 | 0.0% |
| SPY buy-and-hold | 85.8% | 11.0% | -33.8% | 0.63 | 66.7% | n/a | n/a |

### Sector-ETF momentum, test (2023-01-03 to 2026-11-23)

| Row | Total | CAGR | Max DD | Sharpe | Pos. years | Avg held | Months in cash |
|---|---|---|---|---|---|---|---|
| Rule A top-3 6m | 32.1% | 7.4% | -20.7% | 0.54 | 75.0% | 3.0 | 0.0% |
| Rule B 12m > 0 | 63.1% | 13.4% | -15.0% | 1.05 | 100.0% | 8.8 | 0.0% |
| Equal-weight sectors (monthly) | 70.4% | 14.7% | -15.4% | 1.16 | 100.0% | 11.0 | 0.0% |
| SPY buy-and-hold | 98.0% | 19.3% | -18.8% | 1.22 | 100.0% | n/a | n/a |

## Reading

- **Intraday momentum:** no variant has a positive edge net of 1bp in either
  period. Gross per-trade returns are -3.0 to +0.3bp, so every break-even cost
  is at most 0.13bp per side.
- **Sector momentum:** Rule A beat SPY and equal weight in build but trailed
  both in test. Rule B tracked equal weight and stayed below SPY.
