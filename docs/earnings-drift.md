# Earnings-announcement-return drift (cycle 06, backtest 2)

Generated 2026-11-24 13:24 UTC by `scripts/run_earnings_drift.py` in 15 s. Code: `backend/app/data/edgar.py`, `backend/app/quant/earnings_drift.py`; tests: `tests/test_earnings_drift.py`. Rules are the pre-registered ones in `docs/research/strategy-evidence-2026-11.md`, run once with no tuning.

## Data

- Earnings dates: SEC EDGAR 8-K filings listing Item 2.02 (original 8-Ks, not 8-K/A), from the submissions JSON and its paged older files, cached in `data/edgar/` (gitignored). User-Agent "SwingTrader-26 research bot", at most 8 requests a second.
- Tickers to CIKs (current 503, fulltext 197, override 14, predecessor 12, current-verified 7, predecessor-checked 4, unresolved 3): current members from `company_tickers.json`; removed members by EDGAR full-text search for "NYSE: XYZ" / "NASDAQ: XYZ" in 8-Ks filed during their index years, taking the CIK that filed most of the Item 2.02 hits (at least 2 and half of them). A CIK that first reports more than 120 days after it is needed gets a predecessor found the same way (holding-company reorganisations). Search misses were filled by hand, each checked against the EDGAR name and former names: `edgar.CIK_OVERRIDES` (14 removed members, mostly second share classes) and `edgar.PREDECESSORS` (XOM, LIN, VTRS, SKYD, whose old CIK or symbol differs). A removed member search cannot resolve uses today's owner of the symbol if it filed an Item 2.02 during the member span ("current-verified"). Unresolved: AMTM, FRC, SBNY (FRC and SBNY were banks filing with the FDIC, not the SEC; AMTM was a member for two months).
- Day 0: `acceptanceDateTime` (UTC) on New York time; at or after 16:00, or not a session, the next session. One event per ticker and day 0. 32,232 events from 2015.
- Coverage: 21,389 of 21,679 member-quarters (98.7%) have at least one 8-K Item 2.02 with day 0 in the quarter (members in the quarter's last snapshot, 2016-01-01 to the last full quarter). Most quarters missing: WRK 23, TROW 21, FRC 17, CTAS 12, MKC 12, KMX 9, WBA 8, FLS 7, STZ 7, PAYX 7, BRK-B 6, BWA 6, CAG 5, INFO 5, SBNY 5.
- Prices as in the momentum backtest: `data/raw` (total-return adjusted) and `data/raw_pit` for removed members, each cut 120 days after its last monthly snapshot. Spliced series excluded: none. No bars: none.
- From 2016-01-01: 23,282 member events, 23,266 with an EAR, signals mid 18,371, bottom 2,455, top 2,440.

## Rules (pre-registered)

- EAR = the product over days -1, 0, +1 of (1 + stock return - SPY return), minus 1, on closes.
- An event counts only if the ticker is in the S&P 500 snapshot of day 0's month.
- At the day +2 open each EAR is compared with the member EARs whose day +1 fell in the previous 252 sessions: above their 90th percentile is the top decile, below the 10th the bottom. A pool under 100 EARs classifies nothing; 2015 events use the 2015-12-31 list and only fill the pool, so the first classified day 0 is 2015-01-29.
- Long-only: buy top-decile events at the day +2 open, 1/20 of the previous close's equity each, at most 20 held, largest EAR first when slots are short, a ticker already held skipped. Long-short: also short bottom-decile events (at most 20 shorts). **Borrow cost is not modelled** for the shorts.
- Exit at the close of day +61 (60 sessions held). A stock with no bar (delisted, acquired) exits at its last close.
- 5bp per side, fractional shares, cash earns 0, $10,000 at the start of each period.
- AR60: buy-and-hold abnormal return from the day +2 open to the day +61 close minus SPY's over the same span (gross of costs), averaged over the strategy's entered events with a full window. Break-even cost = long AR60 / 2 per side.
- Exposure-matched SPY holds SPY at the strategy's net invested fraction (long minus short value over equity) at the prior close.

## Event study (all classified member events, not just those traded)

| Period | Group | Events | Mean AR % | t | Months | t (monthly) | % > 0 |
|---|---|---:|---:|---:|---:|---:|---:|
| build | top | 1535 | 0.37 | 0.99 | 83 | 0.06 | 49 |
| build | bottom | 1572 | 1.20 | 3.05 | 83 | 1.37 | 51 |
| build | all_classified | 14871 | 0.12 | 1.23 | 84 | 0.36 | 49 |
| test | top | 849 | -0.50 | -0.85 | 44 | -0.67 | 45 |
| test | bottom | 835 | -2.29 | -4.01 | 44 | -4.22 | 38 |
| test | all_classified | 7887 | -1.84 | -11.53 | 44 | -4.19 | 41 |
| all | top | 2384 | 0.06 | 0.18 | 127 | -0.36 | 48 |
| all | bottom | 2407 | -0.02 | -0.05 | 127 | -1.24 | 46 |
| all | all_classified | 22758 | -0.56 | -6.45 | 128 | -2.30 | 46 |

`t` treats events as independent; their 60-session windows overlap, so `t (monthly)` (the t-stat of monthly cohort means) is the more honest one. Abnormal returns are against cap-weighted SPY, so they include the equal-weight member gap (all classified events: build 0.12%, test -1.84%). Top minus all classified: build 0.24pp, test 1.34pp; bottom minus all: build 1.07pp, test -0.45pp.

## Build period (2016-01-01 to 2022-12-31)

| Run | CAGR % | Max DD % | Sharpe | Pos. years % | Events | Long AR60 % | Short AR60 % | Avg holdings | Avg gross invested % | Delisted exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Long-only top decile | 13.7 | -33.6 | 0.70 | 86 | 560 | 0.90 | n/a | 18.9 | 92.6 | 2 |
| SPY exposure-matched | 11.3 | -33.7 | 0.68 | 71 | n/a | n/a | n/a | 1.0 | 92.6 | 0 |
| Long-short top/bottom decile | -3.4 | -42.0 | -0.26 | 29 | 1120 | 0.85 | 1.40 | 37.8 | 189.3 | 2 |
| SPY exposure-matched (long-short, net) | -0.6 | -7.1 | -0.30 | 29 | n/a | n/a | n/a | 1.0 | 0.2 | 0 |
| SPY buy & hold | 11.4 | -33.7 | 0.66 | 71 | n/a | n/a | n/a | 1.0 | 100.0 | 0 |

## Test period (2023-01-01 to 2026-11-23)

| Run | CAGR % | Max DD % | Sharpe | Pos. years % | Events | Long AR60 % | Short AR60 % | Avg holdings | Avg gross invested % | Delisted exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Long-only top decile | 20.1 | -20.8 | 1.13 | 100 | 320 | 0.93 | n/a | 18.8 | 91.7 | 1 |
| SPY exposure-matched | 16.7 | -18.6 | 1.14 | 100 | n/a | n/a | n/a | 1.0 | 91.7 | 0 |
| Long-short top/bottom decile | 13.4 | -11.8 | 1.00 | 75 | 640 | 0.79 | -2.82 | 37.5 | 185.4 | 1 |
| SPY exposure-matched (long-short, net) | 0.1 | -2.4 | 0.10 | 50 | n/a | n/a | n/a | 1.0 | 1.3 | 0 |
| SPY buy & hold | 19.2 | -18.8 | 1.22 | 100 | n/a | n/a | n/a | 1.0 | 100.0 | 0 |

Break-even cost per side (long leg): build 45.0bp, test 46.5bp. Calendar years, long-only: build 2016: 30.4%, 2017: 20.3%, 2018: 3.9%, 2019: 27.3%, 2020: 25.7%, 2021: 19.4%, 2022: -20.9%; test 2023: 6.6%, 2024: 17.6%, 2025: 31.3%, 2026: 24.0%. Long-short: build 2016: 7.7%, 2017: -5.4%, 2018: 10.1%, 2019: -8.6%, 2020: -10.7%, 2021: -13.5%, 2022: -0.8%; test 2023: -4.5%, 2024: 16.6%, 2025: 19.8%, 2026: 22.3%.

## Caveats

- Martineau (2022) finds the drift is zero in large caps since 2006; this is the hardest universe for it.
- An 8-K Item 2.02 is usually the quarterly release but can be a preliminary result or guidance update; all are kept as events. Companies with two share classes in the index (GOOGL/GOOG, FOXA/FOX, NWSA/NWS) count once per class.
- Ticker-to-CIK matching by full-text search was checked only by its rule (share of earnings hits), not company by company.
- A missing bar mid-hold also counts as a delisted exit at the last close; merger consideration after the last close is not modelled.
- The short leg needs borrow; hard-to-borrow names are likely over-represented in the bottom decile.
- Removed members have no bars before 2016-01-04, so the 2015 events that fill the first trailing pool are survivors only.
- One path, one split; the test period is under four years.
