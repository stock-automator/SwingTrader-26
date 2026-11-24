# Cycle 07a: pre-registered multi-asset rules

This cycle backtests the rules in `docs/research/strategy-evidence-2026-11.md`,
section "Cycle 07 pre-registered rules". They are implemented as written, with
no tuning, and every rule is reported.

- Run: `python scripts/run_multi_asset.py [--refresh]`.
- Code: `backend/app/quant/multi_asset.py`. Execution reuses `run_rebalanced`
  from `backend/app/quant/etf_strategies.py`.
- Tests: `tests/test_multi_asset.py`.
- Raw results: `data/reports/multi_asset.json` (gitignored).

## Verdict

**Risk parity (inverse volatility) with the 10-month SMA filter gave the
steadiest return per unit of drawdown. It had the best Calmar ratio both in the
2023+ test and over the full history.**

- **2023+ test:** 8.3% a year with a -6.1% max drawdown (Calmar 1.36).
- **Full history, 2007 to 2026:** 5.7% a year with a -11.7% max drawdown
  (Calmar 0.49).
  - It made +18.6% in 2008 and lost only -5.9% in 2022. That was the smallest
    loss of any rule in 2022, and its worst year overall.

Two caveats:

1. **It gives up return.** Its 5.7% full-history CAGR is about half of SPY's
   10.7%. On average it holds 38% of the portfolio in SHY.
2. **The ranking is not stable.** In the 2016–2022 build period it came third
   by Calmar (0.45), behind GTAA aggressive (0.68) and GTAA (0.52). In the test
   period, three rules finished within 0.09 of each other: 1.36, 1.28 and 1.27.
   SPY's worst fall in 2023+ was -18.8%, short of a bear market, so the test
   cannot separate them.

How the other rules compare:

- **GTAA aggressive top-3 is the higher-return alternative.** It returned 9.3%
  a year over the full history with a -19.8% max drawdown (Calmar 0.47). In the
  test it returned 14.7% with a -11.4% drawdown (Calmar 1.28).
- **All five trend or risk-parity rules beat SPY and 60/40 on Calmar** over the
  full history. SPY's Calmar was 0.19 (-55.2% max drawdown) and 60/40's was
  0.25 (-32.5%).
- **Dual momentum (GEM) failed.** Its drawdown reached -40.0% between 2018 and
  2020: it held SPY through Q4 2018 and through March 2020, and switched to
  IEF just before the 2019 rally. It also lost -25.0% in 2022, when it was in
  SPY until May and then in IEF.
- **The put-write and buy-write indexes are not a hedge.** Before costs they
  fell -26.8% (PUT) and -28.7% (BXM) in 2008. Over the full history their
  max drawdowns were -37% and -40%.

## Data

- **ETFs:** SPY, QQQ, IWM, EFA, EEM, TLT, IEF, SHY, GLD, DBC and VNQ. QQQ, IWM
  and EEM are in the pre-registered universe but no rule uses them. They are
  fetched only for the source check.
- **2016-01-04 onward:** Alpaca SIP daily bars with `adjustment=all`, in
  `data/raw_etf/<SYM>.parquet`.
- **Before 2016:** yfinance auto-adjusted daily bars from 2005, in
  `data/raw_etf/yf/<SYM>.parquet`. These bars are scaled to match Alpaca's
  first session, so returns across the join are yfinance's own.
- **CBOE PUT and BXM:** closing levels from CBOE's free history CSVs, in
  `data/raw_etf/cboe/`. PUT has daily values only from 2007-01-03; before that
  the series is sparse.
- **Fetch end date:** every fetch ends at midnight New York, so the last
  session used is 2026-11-23.

### Source check: Alpaca vs yfinance, 2016-01-04 to 2026-11-23

| ETF | Raw corr. | Repaired Alpaca closes | Corr. after | Days still > 1% apart |
|---|---|---|---|---|
| SPY | 0.9962 | 2020-03-12, 2020-03-18 | 0.9994 | 0 |
| QQQ | 1.0000 | none | 1.0000 | 0 |
| IWM | 0.9992 | 2020-03-16, 2020-03-17 | 0.9993 | 1 (2020-03-17) |
| EFA | 0.9987 | 2020-03-16 | 0.9999 | 0 |
| EEM | 0.9996 | 2020-03-16 | 0.9998 | 0 |
| TLT | 1.0000 | none | 1.0000 | 0 |
| IEF | 0.9999 | none | 0.9999 | 0 |
| SHY | 0.9981 | none | 0.9981 | 0 |
| GLD | 0.9959 | 2020-03-12, 2020-03-16 | 0.9999 | 0 |
| DBC | 0.9994 | none | 0.9994 | 1 (2018-12-26) |
| VNQ | **0.9802** | 2020-03-09, 2020-03-16, 2020-03-18 | 0.9999 | 0 |

- **What went wrong:** VNQ's raw correlation was below the required 0.99.
  - All of the gaps are Alpaca daily closes in March 2020 that revert the next
    session. Alpaca's opens agree with yfinance's: the 99th-percentile gap is
    at most 0.17% in the six ETFs checked (SPY, QQQ, SHY, GLD, DBC, VNQ).
  - The known S&P 500 move shows which source is wrong. On 2020-03-12 the
    index fell 9.5%. yfinance has SPY at -9.6%; Alpaca has -6.9%.
- **The repair:** `patch_bad_closes` replaces only one-session prints. These
  are days where the Alpaca/yfinance price ratio moves by more than 1% and is
  back within 1% the next session. On those days the close becomes yfinance's
  close times the previous session's ratio.
  - Level shifts are not repaired.
- **What remains:** DBC on 2018-12-26 differs by 1.4% (Alpaca +3.6%, yfinance
  +2.2%). This is a difference in how the two vendors adjusted for the
  December 2018 distribution. Both sources include it, and it is left as
  Alpaca has it.
- **Distributions before 2016:** yfinance has DBC's 2006–2008 distributions.
- **Test:** `test_real_sources_agree_where_they_overlap` asserts a correlation
  above 0.99 after the repair for all 11 ETFs. It also asserts that there are
  at most 5 repairs per ETF. It skips when the cache is absent.

## Rules as implemented

**Execution and costs (all rules):**

- Signals are taken at the month-end close and traded at the next session's
  open.
- Costs are 2bp per side on every unit traded.
- Shares are fractional. Weights drift between rebalances.
- Month-based lookbacks count month-end closes.

| Rule | Implementation |
|---|---|
| GTAA (Faber) | SPY, EFA, IEF, DBC and VNQ at 20% each. An asset is held only if its month-end close is above the mean of the last 10 month-end closes (current month included). Any failing 20% goes to SHY. |
| GTAA aggressive top-3 | The 7 assets (GTAA's 5 plus GLD and TLT) are ranked by the mean of their 1/3/6/12-month returns. The top 3 get 1/3 each. Any of them below its 10-month SMA has its third in SHY. |
| Dual momentum (GEM) | If SPY's 12-month return is above SHY's, hold the better of SPY and EFA (a tie goes to SPY). Otherwise hold IEF. |
| Risk parity inv-vol | SPY, TLT, GLD and DBC, weighted by 1 / standard deviation of the last 60 daily returns. |
| Risk parity inv-vol + SMA | The same weights, but an asset below its 10-month SMA has its weight moved to SHY. |
| Permanent portfolio | 25% each in SPY, TLT, GLD and SHY. It buys at the first signal, then rebalances at each December month-end, traded on January's first session. |
| SPY buy-and-hold | Bought once at the first trade. |
| 60/40 SPY/IEF | Rebalanced monthly. |
| CBOE PUT / BXM | Index close-to-close returns with no costs. These indexes cannot be bought directly. |

**Periods:**

- The full-history run starts once every rule has its whole lookback. That is
  DBC's 12th month-end: the first signal is 2007-02-28 and the first trade is
  2007-03-01.
- Build (2016–2022) and test (2023+) are slices of that one continuous run. A
  period starts with the positions already held.

**Metrics:**

- **Sharpe:** uses a risk-free rate of 0, annualised over 252 days.
- **Calmar:** CAGR / |max drawdown|.
- **Worst year and positive months:** include partial edge years and months.
  These are 2007 (March to December) and 2026 (to 23 November).
- **Turnover:** buys plus sells as a share of equity, summed per year. The
  cost drag is 2bp × turnover; for example, 400% a year costs 0.08% a year.

## Results

### Build (2016-01-04 to 2022-12-30)

| Rule | CAGR | Max DD | Sharpe | Calmar | Worst year | Pos. months | Turnover/yr |
|---|---|---|---|---|---|---|---|
| GTAA (Faber) | 4.8% | -9.2% | 0.72 | 0.52 | -8.3% (2022) | 67.9% | 376.5% |
| GTAA aggressive top-3 | 8.2% | -12.0% | 0.79 | 0.68 | -5.8% (2022) | 65.5% | 527.7% |
| Dual momentum (GEM) | 1.2% | -40.0% | 0.16 | 0.03 | -25.0% (2022) | 63.1% | 429.1% |
| Risk parity inv-vol | 7.4% | -20.4% | 0.83 | 0.36 | -9.9% (2022) | 63.1% | 103.6% |
| Risk parity inv-vol + SMA | 5.0% | -11.2% | 0.75 | 0.45 | -5.9% (2022) | 57.1% | 433.3% |
| Permanent portfolio | 5.1% | -18.4% | 0.73 | 0.28 | -13.5% (2022) | 61.9% | 5.8% |
| SPY buy-and-hold | 11.2% | -33.8% | 0.66 | 0.33 | -18.2% (2022) | 71.4% | 0.0% |
| 60/40 SPY/IEF monthly | 7.2% | -21.2% | 0.69 | 0.34 | -16.6% (2022) | 71.4% | 24.0% |
| CBOE PUT index (no costs) | 5.6% | -28.9% | 0.46 | 0.19 | -7.7% (2022) | 75.0% | n/a |
| CBOE BXM index (no costs) | 4.8% | -30.3% | 0.40 | 0.16 | -11.4% (2022) | 71.4% | n/a |

### Test (2023-01-03 to 2026-11-23)

| Rule | CAGR | Max DD | Sharpe | Calmar | Worst year | Pos. months | Turnover/yr |
|---|---|---|---|---|---|---|---|
| GTAA (Faber) | 6.7% | -7.4% | 0.95 | 0.92 | 4.9% (2023) | 66.0% | 424.3% |
| GTAA aggressive top-3 | 14.7% | -11.4% | 1.23 | 1.28 | 6.9% (2026) | 70.2% | 446.1% |
| Dual momentum (GEM) | 13.9% | -18.8% | 0.92 | 0.74 | 2.5% (2026) | 63.8% | 670.6% |
| Risk parity inv-vol | 11.1% | -8.7% | 1.20 | 1.27 | 4.0% (2026) | 72.3% | 101.9% |
| Risk parity inv-vol + SMA | 8.3% | -6.1% | 1.13 | 1.36 | 2.9% (2023) | 70.2% | 423.2% |
| Permanent portfolio | 10.8% | -10.8% | 1.22 | 1.01 | -2.5% (2026) | 70.2% | 12.8% |
| SPY buy-and-hold | 19.3% | -18.8% | 1.22 | 1.03 | 6.8% (2026) | 66.0% | 0.0% |
| 60/40 SPY/IEF monthly | 12.4% | -10.6% | 1.26 | 1.17 | 3.6% (2026) | 66.0% | 16.0% |
| CBOE PUT index (no costs) | 12.2% | -15.1% | 1.22 | 0.81 | 6.2% (2026) | 80.9% | n/a |
| CBOE BXM index (no costs) | 11.7% | -15.5% | 1.12 | 0.76 | 5.0% (2026) | 74.5% | n/a |

### Full history (2007-03-01 to 2026-11-23)

| Rule | CAGR | Max DD | Sharpe | Calmar | Worst year | Pos. months | Turnover/yr |
|---|---|---|---|---|---|---|---|
| GTAA (Faber) | 4.7% | -13.7% | 0.63 | 0.34 | -8.3% (2022) | 63.7% | 370.5% |
| GTAA aggressive top-3 | 9.3% | -19.8% | 0.83 | 0.47 | -10.9% (2015) | 64.1% | 475.8% |
| Dual momentum (GEM) | 5.7% | -40.0% | 0.43 | 0.14 | -25.0% (2022) | 61.2% | 451.6% |
| Risk parity inv-vol | 7.1% | -20.4% | 0.78 | 0.35 | -9.9% (2022) | 62.0% | 108.0% |
| Risk parity inv-vol + SMA | 5.7% | -11.7% | 0.82 | 0.49 | -5.9% (2022) | 60.3% | 407.5% |
| Permanent portfolio | 6.7% | -18.4% | 0.90 | 0.37 | -13.5% (2022) | 60.3% | 14.4% |
| SPY buy-and-hold | 10.7% | -55.2% | 0.62 | 0.19 | -36.8% (2008) | 65.8% | 5.1% |
| 60/40 SPY/IEF monthly | 8.0% | -32.5% | 0.75 | 0.25 | -17.9% (2008) | 65.8% | 27.8% |
| CBOE PUT index (no costs) | 7.0% | -37.1% | 0.56 | 0.19 | -26.8% (2008) | 73.4% | n/a |
| CBOE BXM index (no costs) | 5.7% | -40.1% | 0.46 | 0.14 | -28.7% (2008) | 69.2% | n/a |

### Calendar-year returns in stress years

| Rule | 2008 | 2020 | 2022 |
|---|---|---|---|
| GTAA (Faber) | -0.1% | 4.4% | -8.3% |
| GTAA aggressive top-3 | 6.4% | 16.4% | -5.8% |
| Dual momentum (GEM) | 17.9% | -3.5% | -25.0% |
| Risk parity inv-vol | 1.3% | 12.5% | -9.9% |
| Risk parity inv-vol + SMA | 18.6% | 10.8% | -5.9% |
| Permanent portfolio | 2.2% | 16.1% | -13.5% |
| SPY buy-and-hold | -36.8% | 18.5% | -18.2% |
| 60/40 SPY/IEF monthly | -17.9% | 16.5% | -16.6% |
| CBOE PUT index (no costs) | -26.8% | 2.1% | -7.7% |
| CBOE BXM index (no costs) | -28.7% | -2.8% | -11.4% |

Average share of the portfolio in SHY, full run:

| Rule | Average SHY weight |
|---|---|
| GTAA | 36% |
| GTAA aggressive | 12% |
| Risk parity + SMA | 38% |
| Permanent portfolio | 25% (fixed) |
| Dual momentum and plain risk parity | 0% |

## Limits

- **Publication bias.** These rules were
  published in 2007–2014, and 2007 to the publication dates is partly
  in-sample for their authors. Only 2015 onward is fully after publication.
- **Proxies differ from the published versions.**
  - GEM uses SHY in place of T-bills and IEF in place of aggregate bonds, as
    pre-registered.
  - The options rows are index levels, with no fees or costs.
- **Taxes are not modelled.** About 4× turnover a year in the SMA-based rules
  would realise gains frequently in a taxable account.
