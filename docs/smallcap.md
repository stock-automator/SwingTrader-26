# Small and mid caps (cycle 07b)

Generated 2026-11-24 15:35 UTC by `scripts/run_smallcap.py` in 73 s. Code: `backend/app/data/broad_universe.py`, `backend/app/quant/smallcap.py` (earnings drift reuses `backend/app/quant/earnings_drift.py`); tests: `tests/test_smallcap.py`. Rules are the pre-registered ones in `docs/research/strategy-evidence-2026-11.md` ("Cycle 07b"), run once with no tuning.

## Universe

- Alpaca US-equity assets: 33,528 (inactive 19,146, active 14,382). Candidates: 12,849 (7,046 on NYSE / Nasdaq / AMEX, 5,803 OTC). OTC symbols are kept because a delisted company keeps its exchange-era bars under its OTC symbol (BBBYQ holds Bed Bath & Beyond's Nasdaq history); without them the delisted losers would be missing. Warrants, rights, units, preferreds, depositary shares, ETFs and 5-letter foreign / ADR suffixes were dropped by symbol and name first.
- Bars: Alpaca SIP daily, all adjustments, requested from 2015 (Alpaca's history starts 2016-01-04), 7,244 symbols with bars, in `data/raw_broad/` (gitignored).
- Pre-screen: 5,242 symbols ever had a session with an unadjusted close >= $5 and 20-day unadjusted dollar volume >= $5M; only these were classified.
- EDGAR classification (User-Agent "SwingTrader-26 research bot"): 5,010 of 5,242 resolved to a CIK (current 4,514, fulltext 496, unresolved 232); **232 could not be classified and are excluded**. Common stocks (10-K / 10-Q filers, SIC not 6221 / 6722 / 6726): 4,177. Not common: no 10-K/10-Q 829, no CIK 232, fund SIC 6221 4.
- Spliced-bar filter (`portfolio.suspect_spike`): 126 tickers dropped. A bar gap over 10 sessions or a one-day move beyond 10x either way starts a new segment (reused symbols): 117 breaks. The 1m and 6-1 returns and the EAR never reach back across one, and a position held into one is sold at its last close before it (monthly runs, earnings drift and the long-short spreads alike).
- Price and dollar volume use what traded (unadjusted bars (4177 tickers, 0 without)): the $5 test on the unadjusted close, and dollar volume (the $5M test, the top-500 exclusion and the cost tier) as unadjusted close x unadjusted volume. Adjusted closes are used for returns only.
- Eligible names per month end from the build start (price >= $5, 20-day dollar volume >= $5M, not in the top 500 by dollar volume): min 962, median 1,348, max 1,905 (build 1,268, test 1,494 median).
- Delisted: 455 of the 3,600 names ever eligible (12.6%) have no bar on the last session. This is low for ten years of small caps: Alpaca's inactive list misses delisted names whose symbol moved on, so some survivorship bias remains.
- The common-stock classification is static and full-sample: one EDGAR answer per symbol over its whole bar span, not point in time.
- Known exclusions: banks that file with the FDIC, not the SEC (OZK, TowneBank; FRC and SBNY in cycle 06), have no 10-K on EDGAR and are excluded, as are foreign filers (20-F / 40-F).
- Earnings events (8-K Item 2.02): 133,487; from the build start 124,606, of which 58,599 were in the eligible list of their month; signals mid 46,439, bottom 6,081, top 6,075.

## Rules

- Signals at the month-end close, traded at the next open, equal weight, long-only, held one month. Reversal: lowest decile of the 21-session return. Momentum: highest decile of the 126-to-21-session return. Composite: mean of the cross-sectional z-scores (eligible names with both returns) of -1m return, 6-1 return and the latest EAR known within 60 sessions (else 0); top 20.
- Earnings drift: cycle 06's rules (day +2 open entry, top EAR decile against the trailing 252-session pool of eligible names' EARs, 60 sessions held, at most 20 positions), with the month's eligible list as members.
- Costs per side: 10bp where the previous close's 20-day dollar volume is above $20M, else 20bp; 2x doubles both. Fractional shares, cash earns 0, $10,000 at the start of each period. A stock with no bar is sold at its last close.
- The build period starts at the first month end where 6-1 momentum can rank (126 sessions after Alpaca's first bar), so every run and benchmark starts together. Turnover is one-way: traded notional / 2 / mean equity per year.
- Equal-weight universe: every eligible name, rebalanced monthly, same costs.

## Results

### Build (2016-08-01 to 2022-12-30)

At 1x costs (10bp / 20bp per side):

| Run | CAGR % | Max DD % | Sharpe | Calmar | Pos. years % | Turnover x/yr | Avg holdings |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1m reversal (bottom decile) | 4.3 | -54.6 | 0.29 | 0.08 | 71 | 10.6 | 129.6 |
| 6-1 momentum (top decile) | 11.4 | -50.1 | 0.53 | 0.23 | 71 | 5.8 | 126.4 |
| Earnings drift (EAR top decile) | 3.0 | -50.2 | 0.25 | 0.06 | 71 | 3.9 | 19.2 |
| Composite (top 20) | 6.7 | -59.3 | 0.36 | 0.11 | 71 | 8.8 | 19.9 |
| Equal-weight universe | 7.7 | -42.8 | 0.43 | 0.18 | 71 | 1.6 | 1,301.2 |
| IWM buy & hold | 7.2 | -41.3 | 0.41 | 0.17 | 71 | 0.0 | 1.0 |
| SPY buy & hold | 11.1 | -33.8 | 0.65 | 0.33 | 71 | 0.0 | 1.0 |

At 2x costs (20bp / 40bp per side):

| Run | CAGR % | Max DD % | Sharpe | Calmar | Pos. years % | Turnover x/yr | Avg holdings |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1m reversal (bottom decile) | 1.0 | -56.3 | 0.20 | 0.02 | 71 | 10.6 | 129.6 |
| 6-1 momentum (top decile) | 9.5 | -51.3 | 0.47 | 0.18 | 71 | 5.8 | 126.4 |
| Earnings drift (EAR top decile) | 1.8 | -51.5 | 0.20 | 0.04 | 71 | 3.9 | 19.2 |
| Composite (top 20) | 3.9 | -61.3 | 0.29 | 0.06 | 71 | 8.8 | 19.9 |
| Equal-weight universe | 7.1 | -42.8 | 0.41 | 0.17 | 71 | 1.6 | 1,301.2 |

### Test (2023-01-03 to 2026-11-23)

At 1x costs (10bp / 20bp per side):

| Run | CAGR % | Max DD % | Sharpe | Calmar | Pos. years % | Turnover x/yr | Avg holdings |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1m reversal (bottom decile) | -2.3 | -37.9 | 0.06 | -0.06 | 25 | 10.5 | 152.6 |
| 6-1 momentum (top decile) | 3.1 | -33.7 | 0.25 | 0.09 | 75 | 5.7 | 150.6 |
| Earnings drift (EAR top decile) | 12.1 | -33.8 | 0.59 | 0.36 | 75 | 3.8 | 18.9 |
| Composite (top 20) | -24.1 | -69.7 | -0.45 | -0.35 | 25 | 8.4 | 20.0 |
| Equal-weight universe | 9.6 | -27.7 | 0.57 | 0.35 | 100 | 1.5 | 1,531.3 |
| IWM buy & hold | 12.7 | -27.5 | 0.68 | 0.46 | 100 | 0.0 | 1.0 |
| SPY buy & hold | 19.2 | -18.8 | 1.22 | 1.02 | 100 | 0.0 | 1.0 |

At 2x costs (20bp / 40bp per side):

| Run | CAGR % | Max DD % | Sharpe | Calmar | Pos. years % | Turnover x/yr | Avg holdings |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1m reversal (bottom decile) | -5.2 | -41.9 | -0.04 | -0.12 | 25 | 10.5 | 152.6 |
| 6-1 momentum (top decile) | 1.5 | -34.0 | 0.19 | 0.04 | 50 | 5.7 | 150.6 |
| Earnings drift (EAR top decile) | 10.9 | -34.1 | 0.55 | 0.32 | 75 | 3.8 | 18.9 |
| Composite (top 20) | -25.8 | -72.2 | -0.51 | -0.36 | 25 | 8.4 | 20.0 |
| Equal-weight universe | 9.1 | -27.7 | 0.54 | 0.33 | 100 | 1.5 | 1,531.3 |

## Long-short diagnostic

Not a tradeable result (no borrow cost, gross of costs). Each month-end: equal-weight return of the long leg minus the short leg to the next month-end close. Reversal: lowest minus highest 1m decile; momentum: highest minus lowest 6-1 decile; composite: top 20 minus bottom 20.

| Period | Signal | Months | Mean spread %/month | t | % months > 0 |
|---|---|---:|---:|---:|---:|
| build | 1m reversal (bottom decile) | 77 | 0.49 | 0.87 | 44 |
| build | 6-1 momentum (top decile) | 77 | 0.91 | 1.66 | 57 |
| build | Composite (top 20) | 77 | 0.32 | 0.38 | 56 |
| test | 1m reversal (bottom decile) | 46 | -0.67 | -0.97 | 50 |
| test | 6-1 momentum (top decile) | 46 | -0.18 | -0.20 | 54 |
| test | Composite (top 20) | 46 | 0.17 | 0.11 | 48 |

Earnings drift long-short (top minus bottom decile events, 1x costs, borrow not modelled): build CAGR -12.9%, Sharpe -0.85; test CAGR 11.8%, Sharpe 0.71.

Event study (abnormal return vs SPY from the day +2 open to the day +61 close, gross):

| Period | Group | Events | Mean AR60 % | t (monthly) |
|---|---|---:|---:|---:|
| build | top | 3,449 | -0.02 | -0.49 |
| build | bottom | 3,495 | 2.07 | 1.35 |
| build | all_classified | 33,401 | -0.02 | 0.52 |
| test | top | 2,402 | -0.91 | -1.04 |
| test | bottom | 2,400 | -2.54 | -2.51 |
| test | all_classified | 23,351 | -2.01 | -3.40 |

## Data fixes after the first run (disclosed)

The first run exposed two data errors and review found two more, all fixed in the data layer and not in the rules:

1. The $5 test used split-adjusted closes, so stocks that later reverse-split (CBIO, GDC) showed adjusted prices of $600 to $7,750 while trading for pennies. The rule means the price that traded, so the test now uses unadjusted SIP closes.
2. Alpaca chains a reused symbol with no gap: SN jumped 116x in a day (Sanchez Energy's OTC tail, then SharkNinja), LINE 448x, SPRB 124x, WW 159x (post-bankruptcy shares). Their 6-1 returns of +10,000% and more swamped the composite's unwinsorised z-score. A one-day move beyond 10x now starts a new segment. The threshold was set from these data errors (the largest real move found was DFDV's 9.4x), not from strategy results.
3. (Review) Positions were carried across a segment break, so the jump was booked as profit or loss (BHVN: a fake -94.5% loss). A holding is now sold at its last close before the break.
4. (Review) Dollar volume used adjusted close x volume. Dividend adjustment lowers past prices but not volumes, so the $5M test, the top-500 exclusion, the cost tier and the pre-screen depended on later dividends. All four now use unadjusted close x unadjusted volume.

Rules, signals and costs are unchanged. 1x costs, first run versus this run:

| Period | Run | CAGR % first | CAGR % now | Sharpe first | Sharpe now |
|---|---|---:|---:|---:|---:|
| build | 1m reversal (bottom decile) | 3.1 | 4.3 | 0.26 | 0.29 |
| build | 6-1 momentum (top decile) | 8.8 | 11.4 | 0.44 | 0.53 |
| build | Earnings drift (EAR top decile) | 2.1 | 3.0 | 0.21 | 0.25 |
| build | Composite (top 20) | 8.3 | 6.7 | 0.40 | 0.36 |
| build | Equal-weight universe | 6.9 | 7.7 | 0.40 | 0.43 |
| test | 1m reversal (bottom decile) | -15.1 | -2.3 | -0.41 | 0.06 |
| test | 6-1 momentum (top decile) | -1.5 | 3.1 | 0.09 | 0.25 |
| test | Earnings drift (EAR top decile) | 6.5 | 12.1 | 0.38 | 0.59 |
| test | Composite (top 20) | -31.2 | -24.1 | -0.72 | -0.45 |
| test | Equal-weight universe | 6.1 | 9.6 | 0.40 | 0.57 |
