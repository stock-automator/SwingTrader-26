# Strategy improvements (cycle 03 step 3)

Generated 2026-11-23 21:02 UTC by `scripts/run_improvements.py` in 16.9 min.

- Universe: 518 cached symbols; long-only; scorecard account rules ($10,000, 1% risk, 25% max position, 6 positions, 6% heat, costs as in docs/scorecard.md).
- Suspect data (a spliced bar: a >40% one-day close move that snaps back in one bar to within 5% of the pre-jump close within 5 bars), excluded from every run: FER.
- Build 2016-01-01 to 2023-01-01 (exclusive); test 2023-01-01 to 2026-11-20.
- Candidates were fixed before any run: one change each against the baseline. Build composite = the scorecard weights without robustness, rescaled to 1, as percentile ranks among this strategy's rows on build-period stats only.
- Rule: choose the best build composite; keep it only if it beats the baseline's and its test return vs exposure-matched SPY and test max drawdown are no worse. Other candidates' test columns are for information and chose nothing.

### zscore_mean_reversion

| Candidate | Change | Build composite | Build CAGR % | Build vs SPY pp | Build trades | Test return % | Test exp-matched SPY % | Test vs SPY pp | Test PF | Test max DD % | Test trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | scorecard defaults | 0.577 | 6.7 | -15.0 | 857 | 46.4 | 89.1 | -42.7 | 1.21 | -19.8 | 457 |
| regime | longs only when the SPY regime at the signal close is not BEAR_TRENDING or HIGH_VOLATILITY_CHOP | 0.423 | 2.5 | -38.4 | 649 | 9.5 | 54.1 | -44.7 | 1.04 | -24.7 | 441 |
| trend200 (chosen, not kept) | longs only when the close is above its 200-day SMA | 0.800 | 7.9 | 17.8 | 844 | -7.0 | 76.7 | -83.6 | 0.97 | -33.5 | 479 |
| time20 | 20-day time stop instead of 40 | 0.741 | 7.1 | -11.0 | 976 | -5.5 | 85.0 | -90.5 | 0.98 | -19.3 | 541 |
| tp2 | target 2.0 ATR instead of 3.0 (take the reversion sooner) | 0.459 | 5.2 | -39.0 | 1134 | 11.2 | 96.6 | -85.4 | 1.04 | -18.9 | 596 |

Chosen on 2016-2022: **trend200**. Kept: **no** (test vs SPY worse, test max drawdown worse).

### supertrend_psar

| Candidate | Change | Build composite | Build CAGR % | Build vs SPY pp | Build trades | Test return % | Test exp-matched SPY % | Test vs SPY pp | Test PF | Test max DD % | Test trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | scorecard defaults | 0.647 | 6.0 | -46.3 | 788 | 31.9 | 81.2 | -49.4 | 1.12 | -16.8 | 469 |
| regime | longs only when the SPY regime at the signal close is not BEAR_TRENDING or HIGH_VOLATILITY_CHOP | 0.494 | 1.4 | -44.6 | 596 | 36.4 | 67.2 | -30.8 | 1.14 | -18.5 | 468 |
| trend200 | longs only when the close is above its 200-day SMA | 0.400 | 2.7 | -58.0 | 777 | 18.7 | 87.5 | -68.8 | 1.08 | -26.8 | 479 |
| time20 | 20-day time stop instead of 40 | 0.541 | 3.3 | -46.9 | 902 | 20.4 | 89.6 | -69.1 | 1.08 | -15.2 | 551 |
| sl2 (chosen, not kept) | stop 2.0 ATR instead of 1.5 (room for the trend) | 0.918 | 6.9 | -4.8 | 645 | 24.9 | 76.5 | -51.7 | 1.12 | -22.6 | 364 |

Chosen on 2016-2022: **sl2**. Kept: **no** (test vs SPY worse, test max drawdown worse).

### bollinger_keltner_squeeze

| Candidate | Change | Build composite | Build CAGR % | Build vs SPY pp | Build trades | Test return % | Test exp-matched SPY % | Test vs SPY pp | Test PF | Test max DD % | Test trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | scorecard defaults | 0.894 | 3.8 | -34.3 | 571 | 15.4 | 53.2 | -37.8 | 1.09 | -21.4 | 341 |
| regime | longs only when the SPY regime at the signal close is not BEAR_TRENDING or HIGH_VOLATILITY_CHOP | 0.565 | 0.7 | -35.4 | 476 | 11.2 | 50.4 | -39.2 | 1.07 | -21.1 | 343 |
| trend200 | longs only when the close is above its 200-day SMA | 0.412 | 0.5 | -60.7 | 529 | -0.4 | 60.5 | -60.8 | 1.00 | -24.7 | 330 |
| time20 | 20-day time stop instead of 40 | 0.753 | 5.0 | -43.4 | 660 | -3.2 | 48.3 | -51.4 | 0.98 | -32.8 | 421 |
| sl2 | stop 2.0 ATR instead of 1.5 (room after the breakout) | 0.377 | -0.2 | -62.2 | 489 | 15.7 | 62.0 | -46.3 | 1.08 | -20.5 | 329 |

Chosen on 2016-2022: **none**. Kept: **no** (baseline wins build).

## Scorecard rows rerun on the tightened data filter

Rerun 2026-11-23 21:51 UTC with `scripts/run_scorecard.py --strategies zscore_mean_reversion,supertrend_psar,bollinger_keltner_squeeze` (48 min, robustness included). Only FER is now excluded; BIIB, CRWV, CVNA, HOOD and NCLH are back in the universe. The "before" rows are from `docs/scorecard.md` (20:36 UTC, old filter); that file still shows the old filter for all 10 strategies. Composite scores are omitted because they are relative ranks and a 3-row rerun is not comparable with the 11-row table.

| Strategy (long) | Filter | Test return % | Test exp-matched SPY % | Test vs SPY pp | Test PF | Test max DD % | Test trades | Build CAGR % | Build trades | Worst ±20% build CAGR % |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| zscore_mean_reversion | old | 51.5 | 88.3 | -36.8 | 1.22 | -19.8 | 470 | 8.3 | 841 | 3.5 |
| zscore_mean_reversion | tightened | 46.4 | 89.1 | -42.7 | 1.21 | -19.8 | 457 | 6.7 | 857 | 4.2 |
| supertrend_psar | old | 41.2 | 80.3 | -39.1 | 1.15 | -16.5 | 474 | 6.4 | 781 | 2.5 |
| supertrend_psar | tightened | 31.9 | 81.2 | -49.4 | 1.12 | -16.8 | 469 | 6.0 | 788 | 3.5 |
| bollinger_keltner_squeeze | old | 17.0 | 53.3 | -36.3 | 1.10 | -20.2 | 341 | 4.0 | 571 | -0.0 |
| bollinger_keltner_squeeze | tightened | 15.4 | 53.2 | -37.8 | 1.09 | -21.4 | 341 | 3.8 | 571 | 0.0 |

## Outcome and caveats

- No change was kept. For zscore and supertrend, the build winner (trend200 and sl2) did worse on the test period on both checks. For the squeeze strategy, the baseline won the build period.
- The rule was applied as written. The regime filter improved supertrend's test vs SPY (-30.8 pp against -49.4) but lost on build, so it was not chosen. Its test drawdown was also deeper (-18.5% against -16.8%), and picking it now would be tuning on the test period.
- Restoring the five real-event tickers lowered all three strategies' test returns (zscore -5.1 pp, supertrend -9.3 pp, squeeze -1.6 pp). The old filter was flattering the results.
- Every strategy still trails SPY held at the same exposure, in both periods.
- The build composite ranks only 5 rows, so it is coarse. Robustness is not part of it.
- Still not modelled: survivorship bias (today's members only) and whole-share sizing. Each candidate was run once, so these are point estimates with no confidence interval.
