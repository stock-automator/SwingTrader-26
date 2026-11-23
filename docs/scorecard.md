# Strategy scorecard

Generated 2026-11-23 20:36 UTC by `scripts/run_scorecard.py` in 50.7 min.

- Universe: 518 cached symbols from `universe.json`.
- Suspect data (a >40% one-day close move that retraces at least half within 5 bars), excluded from every run: suspect data: BIIB, suspect data: CRWV, suspect data: CVNA, suspect data: FER, suspect data: HOOD, suspect data: NCLH.
- Build 2016-01-01 to 2023-01-01 (exclusive); held-out test 2023-01-01 to 2026-11-20. Signals use full history, so the test period starts warmed up.
- One account per run: $10,000, 1% risk per trade from the stop, max 25% of equity per position, 6 open positions, 6% total open risk, 40-trading-day time stop, entries at the next open, stop checked before target. Costs: 0.1% commission and 0.05% spread per leg, FX per Settings.
- 'vs SPY' = strategy return minus SPY held at the strategy's own invested fraction (exposure-matched), in percentage points.
- Composite score: weighted percentile ranks across rows, 70% test / 30% build: 0.25 vs_spy_pct, 0.15 profit_factor, 0.05 win_rate_pct, 0.05 payoff, 0.10 max_drawdown_pct, 0.05 trades, 0.10 positive_years_pct, 0.10 positive_regimes_pct, 0.15 robustness. Positive regimes = % of SPY macro regimes (at the prior close) with a positive return. Robustness = worst long-only build-period CAGR with each numeric parameter at ±20% (±1 for small integers); it uses 2016-2022 only, so no test data feeds the score through it. Rows with fewer than 30 trades in either period are halved and sort last.
- An 'equity below zero' flag means a short gapped past its collateral and the run stopped opening positions.
- Caveats: survivorship bias (today's index members only); short borrow and fees not modelled; whole shares.

| Rank | Strategy | Variant | Score | Test return % | Test exp-matched SPY % | Test vs SPY pp | Test CAGR % | Test PF | Test win % | Test payoff | Test max DD % | Test trades | Build CAGR % | Build vs SPY pp | Build trades | Worst ±20% build CAGR % | Flags |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | zscore_mean_reversion | long | 0.848 | 51.5 | 88.3 | -36.8 | 11.3 | 1.22 | 42.1 | 1.68 | -19.8 | 470 | 8.3 | -2.6 | 841 | 3.5 | - |
| 2 | supertrend_psar | long | 0.740 | 41.2 | 80.3 | -39.1 | 9.3 | 1.15 | 36.9 | 1.96 | -16.5 | 474 | 6.4 | -45.8 | 781 | 2.5 | - |
| 3 | dual_momentum | long | 0.671 | 10.9 | 82.7 | -71.8 | 2.7 | 1.05 | 33.3 | 2.11 | -16.6 | 454 | 5.7 | -27.7 | 812 | 3.6 | - |
| 4 | bollinger_keltner_squeeze | long | 0.665 | 17.0 | 53.3 | -36.3 | 4.1 | 1.10 | 36.1 | 1.94 | -20.2 | 341 | 4.0 | -34.5 | 571 | -0.0 | - |
| 5 | donchian_breakout | long | 0.598 | -6.0 | 77.9 | -83.9 | -1.6 | 0.98 | 33.1 | 1.97 | -34.0 | 534 | 7.6 | -18.2 | 860 | 1.7 | test much worse than build |
| 6 | kama_trend | long | 0.571 | -26.4 | 68.3 | -94.8 | -7.6 | 0.88 | 34.3 | 1.68 | -32.4 | 510 | 10.5 | -1.8 | 859 | 3.8 | test much worse than build |
| 7 | vcp_breakout | long | 0.500 | -0.2 | 39.8 | -40.0 | -0.1 | 1.00 | 34.8 | 1.87 | -21.3 | 227 | 2.2 | -14.5 | 437 | -4.1 | - |
| 8 | obv_divergence | long | 0.473 | 10.5 | 60.5 | -50.1 | 2.6 | 1.03 | 33.1 | 2.09 | -42.4 | 495 | 6.4 | -49.6 | 828 | -5.3 | - |
| 9 | relative_strength | long | 0.430 | -14.9 | 74.1 | -89.0 | -4.1 | 0.93 | 32.0 | 1.97 | -30.3 | 440 | 3.9 | -38.3 | 805 | 2.2 | test much worse than build |
| 10 | moving_average_cross | long | 0.279 | -46.6 | 53.4 | -100.0 | -14.9 | 0.83 | 21.6 | 3.03 | -49.9 | 1011 | 2.5 | -78.3 | 1526 | -0.9 | test much worse than build |
| 11 | moving_average_cross | long_short | 0.226 | -58.4 | 69.9 | -128.3 | -20.2 | 0.77 | 19.5 | 3.18 | -60.4 | 1152 | -4.9 | -138.4 | 1766 | -0.9 | test much worse than build, short-side unmodelled borrow |

### bollinger_keltner_squeeze (Bollinger-Keltner Squeeze Breakout)

- long build (2016-01-04 to 2022-12-30): return 31.8% vs exposure-matched SPY 66.3%, 571 trades (0 short), avg R 0.18, invested 73.6% on average, exposure 91.1% of days.
  - Years: 2016: 8.0%, 2017: 0.0%, 2018: -13.1%, 2019: 20.6%, 2020: 6.8%, 2021: 17.4%, 2022: -7.1%
  - By regime (prior close): BEAR_TRENDING: -3.0%, BULL_TRENDING: -6.5%, HIGH_VOLATILITY_CHOP: 16.3%, NEUTRAL: 24.9%
  - Skipped/capped signals: max positions 1326, size capped to 25% of equity 596, size capped to cash 269, insufficient cash 237, gap through stop/target 9, size rounds to zero 2
- long test (2023-01-03 to 2026-11-20): return 17.0% vs exposure-matched SPY 53.3%, 341 trades (0 short), avg R 0.13, invested 79.1% on average, exposure 98.3% of days.
  - Years: 2023: 5.3%, 2024: 24.1%, 2025: -4.4%, 2026: -6.4%
  - By regime (prior close): BEAR_TRENDING: 2.2%, BULL_TRENDING: 26.3%, HIGH_VOLATILITY_CHOP: -2.6%, NEUTRAL: -7.0%
  - Skipped/capped signals: max positions 640, size capped to 25% of equity 299, size capped to cash 155, insufficient cash 146, size rounds to zero 6, gap through stop/target 3
- long_short: not run separately - the strategy emits no short entries, so it equals long-only.
- Robustness (long-only, build period, each param ±20% or ±1, 16 reruns): CAGR -0.0% to 13.4% (spread 13.4 pp), 94% of runs profitable.

### donchian_breakout (Donchian 20-day Breakout)

- long build (2016-01-04 to 2022-12-30): return 66.9% vs exposure-matched SPY 85.1%, 860 trades (0 short), avg R 0.13, invested 86.5% on average, exposure 98.3% of days.
  - Years: 2016: 18.6%, 2017: 12.0%, 2018: -12.8%, 2019: 27.5%, 2020: -1.9%, 2021: 10.3%, 2022: 4.5%
  - By regime (prior close): BEAR_TRENDING: -4.3%, BULL_TRENDING: 9.8%, HIGH_VOLATILITY_CHOP: 51.4%, NEUTRAL: 4.9%
  - Skipped/capped signals: max positions 32763, size capped to 25% of equity 974, insufficient cash 488, size capped to cash 477, gap through stop/target 27, size rounds to zero 3
- long test (2023-01-03 to 2026-11-20): return -6.0% vs exposure-matched SPY 77.9%, 534 trades (0 short), avg R -0.07, invested 87.1% on average, exposure 99.8% of days.
  - Years: 2023: 14.1%, 2024: 11.3%, 2025: -18.0%, 2026: -9.7%
  - By regime (prior close): BEAR_TRENDING: 3.4%, BULL_TRENDING: 0.2%, HIGH_VOLATILITY_CHOP: -10.9%, NEUTRAL: 1.8%
  - Skipped/capped signals: max positions 17557, size capped to 25% of equity 718, insufficient cash 703, size capped to cash 261, gap through stop/target 12, size rounds to zero 5
- long_short: not run separately - the strategy emits no short entries, so it equals long-only.
- Robustness (long-only, build period, each param ±20% or ±1, 14 reruns): CAGR 1.7% to 10.9% (spread 9.3 pp), 100% of runs profitable.

### dual_momentum (Dual Momentum Engine)

- long build (2016-01-04 to 2022-12-30): return 47.6% vs exposure-matched SPY 75.3%, 812 trades (0 short), avg R 0.21, invested 90.1% on average, exposure 99.7% of days.
  - Years: 2016: 4.0%, 2017: 8.1%, 2018: -5.3%, 2019: 38.1%, 2020: 5.7%, 2021: 8.6%, 2022: -12.5%
  - By regime (prior close): BEAR_TRENDING: 1.4%, BULL_TRENDING: -11.4%, HIGH_VOLATILITY_CHOP: 19.0%, NEUTRAL: 38.1%
  - Skipped/capped signals: max positions 309207, insufficient cash 3556, size capped to 25% of equity 3486, size capped to cash 428, gap through stop/target 43
- long test (2023-01-03 to 2026-11-20): return 10.9% vs exposure-matched SPY 82.7%, 454 trades (0 short), avg R 0.05, invested 89.5% on average, exposure 99.8% of days.
  - Years: 2023: 17.2%, 2024: -8.1%, 2025: 6.2%, 2026: -3.0%
  - By regime (prior close): BEAR_TRENDING: 4.2%, BULL_TRENDING: -5.4%, HIGH_VOLATILITY_CHOP: -2.9%, NEUTRAL: 15.7%
  - Skipped/capped signals: max positions 152359, insufficient cash 1309, size capped to 25% of equity 1112, size capped to cash 229, gap through stop/target 26, size rounds to zero 21
- long_short: not run separately - the strategy emits no short entries, so it equals long-only.
- Robustness (long-only, build period, each param ±20% or ±1, 10 reruns): CAGR 3.6% to 12.0% (spread 8.5 pp), 100% of runs profitable.

### kama_trend (KAMA Dynamic Trend)

- long build (2016-01-04 to 2022-12-30): return 100.6% vs exposure-matched SPY 102.4%, 859 trades (0 short), avg R 0.12, invested 88.3% on average, exposure 99.5% of days.
  - Years: 2016: 2.8%, 2017: 13.1%, 2018: -10.3%, 2019: 35.5%, 2020: 34.6%, 2021: 23.2%, 2022: -14.3%
  - By regime (prior close): BEAR_TRENDING: -2.3%, BULL_TRENDING: 17.4%, HIGH_VOLATILITY_CHOP: 30.4%, NEUTRAL: 34.1%
  - Skipped/capped signals: max positions 21717, size capped to 25% of equity 1066, insufficient cash 663, size capped to cash 477, gap through stop/target 10, size rounds to zero 2
- long test (2023-01-03 to 2026-11-20): return -26.4% vs exposure-matched SPY 68.3%, 510 trades (0 short), avg R -0.01, invested 88.6% on average, exposure 99.8% of days.
  - Years: 2023: -1.4%, 2024: -5.1%, 2025: -21.9%, 2026: 0.7%
  - By regime (prior close): BEAR_TRENDING: 1.6%, BULL_TRENDING: -11.7%, HIGH_VOLATILITY_CHOP: -11.3%, NEUTRAL: -7.7%
  - Skipped/capped signals: max positions 12823, size capped to 25% of equity 460, insufficient cash 291, size capped to cash 263, gap through stop/target 7, size rounds to zero 3
- long_short: not run separately - the strategy emits no short entries, so it equals long-only.
- Robustness (long-only, build period, each param ±20% or ±1, 14 reruns): CAGR 3.8% to 15.8% (spread 12.1 pp), 100% of runs profitable.

### moving_average_cross (Moving Average Cross)

- long build (2016-01-04 to 2022-12-30): return 19.2% vs exposure-matched SPY 97.5%, 1526 trades (0 short), avg R 0.27, invested 76.2% on average, exposure 96.1% of days.
  - Years: 2016: 15.2%, 2017: 11.0%, 2018: -14.3%, 2019: -7.6%, 2020: 40.5%, 2021: 15.1%, 2022: -27.3%
  - By regime (prior close): BEAR_TRENDING: -0.3%, BULL_TRENDING: -23.4%, HIGH_VOLATILITY_CHOP: -1.4%, NEUTRAL: 58.4%
  - Skipped/capped signals: max positions 6575, size capped to 25% of equity 2571, insufficient cash 1158, size capped to cash 747, gap through stop/target 114, size rounds to zero 3
- long test (2023-01-03 to 2026-11-20): return -46.6% vs exposure-matched SPY 53.4%, 1011 trades (0 short), avg R -0.22, invested 77.2% on average, exposure 99.5% of days.
  - Years: 2023: -7.2%, 2024: -10.7%, 2025: -22.3%, 2026: -17.1%
  - By regime (prior close): BEAR_TRENDING: 3.5%, BULL_TRENDING: -31.3%, HIGH_VOLATILITY_CHOP: -7.0%, NEUTRAL: -19.3%
  - Skipped/capped signals: max positions 3568, size capped to 25% of equity 1732, insufficient cash 829, size capped to cash 473, gap through stop/target 79, size rounds to zero 11
- long_short build (2016-01-04 to 2022-12-30): return -29.8% vs exposure-matched SPY 108.7%, 1766 trades (579 short), avg R 0.02, invested 79.2% on average, exposure 98.8% of days.
  - Years: 2016: 7.0%, 2017: 4.0%, 2018: -22.4%, 2019: -11.8%, 2020: 37.6%, 2021: 14.8%, 2022: -41.6%
  - By regime (prior close): BEAR_TRENDING: -9.1%, BULL_TRENDING: -9.0%, HIGH_VOLATILITY_CHOP: -33.3%, NEUTRAL: 27.1%
  - Skipped/capped signals: max positions 10038, short blocked by bull regime 4496, size capped to 25% of equity 3578, insufficient cash 2031, size capped to cash 934, gap through stop/target 278, size rounds to zero 7
- long_short test (2023-01-03 to 2026-11-20): return -58.4% vs exposure-matched SPY 69.9%, 1152 trades (307 short), avg R -0.29, invested 76.7% on average, exposure 99.3% of days.
  - Years: 2023: -20.2%, 2024: -24.3%, 2025: -25.2%, 2026: -8.0%
  - By regime (prior close): BEAR_TRENDING: -3.9%, BULL_TRENDING: -30.3%, HIGH_VOLATILITY_CHOP: -4.1%, NEUTRAL: -35.2%
  - Skipped/capped signals: max positions 5710, short blocked by bull regime 3299, size capped to 25% of equity 1678, insufficient cash 665, size capped to cash 559, gap through stop/target 119, size rounds to zero 24
- Robustness (long-only, build period, each param ±20% or ±1, 10 reruns): CAGR -0.9% to 12.4% (spread 13.3 pp), 73% of runs profitable.

### obv_divergence (OBV Bullish Divergence)

- long build (2016-01-04 to 2022-12-30): return 54.7% vs exposure-matched SPY 104.3%, 828 trades (0 short), avg R 0.07, invested 87.0% on average, exposure 99.3% of days.
  - Years: 2016: 8.9%, 2017: 11.5%, 2018: -2.3%, 2019: 20.9%, 2020: 29.1%, 2021: 1.6%, 2022: -17.8%
  - By regime (prior close): BEAR_TRENDING: 2.8%, BULL_TRENDING: -16.1%, HIGH_VOLATILITY_CHOP: 48.2%, NEUTRAL: 20.9%
  - Skipped/capped signals: max positions 21503, size capped to 25% of equity 755, insufficient cash 420, size capped to cash 416, gap through stop/target 13, size rounds to zero 2
- long test (2023-01-03 to 2026-11-20): return 10.5% vs exposure-matched SPY 60.5%, 495 trades (0 short), avg R 0.03, invested 87.9% on average, exposure 99.6% of days.
  - Years: 2023: 34.6%, 2024: 11.4%, 2025: -5.5%, 2026: -22.0%
  - By regime (prior close): BEAR_TRENDING: 7.0%, BULL_TRENDING: -5.8%, HIGH_VOLATILITY_CHOP: -4.0%, NEUTRAL: 14.1%
  - Skipped/capped signals: max positions 12510, size capped to 25% of equity 346, size capped to cash 271, insufficient cash 118, gap through stop/target 2, size rounds to zero 1
- long_short: not run separately - the strategy emits no short entries, so it equals long-only.
- Robustness (long-only, build period, each param ±20% or ±1, 10 reruns): CAGR -5.3% to 10.1% (spread 15.4 pp), 91% of runs profitable.

### relative_strength (Relative Strength Pullback)

- long build (2016-01-04 to 2022-12-30): return 30.5% vs exposure-matched SPY 68.8%, 805 trades (0 short), avg R 0.10, invested 89.0% on average, exposure 99.7% of days.
  - Years: 2016: -10.6%, 2017: 22.4%, 2018: -1.6%, 2019: 36.4%, 2020: 5.5%, 2021: 11.8%, 2022: -24.6%
  - By regime (prior close): BEAR_TRENDING: -1.4%, BULL_TRENDING: 5.2%, HIGH_VOLATILITY_CHOP: -1.8%, NEUTRAL: 28.1%
  - Skipped/capped signals: max positions 89489, size capped to 25% of equity 1268, insufficient cash 959, size capped to cash 462, gap through stop/target 15, size rounds to zero 1
- long test (2023-01-03 to 2026-11-20): return -14.9% vs exposure-matched SPY 74.1%, 440 trades (0 short), avg R -0.02, invested 89.6% on average, exposure 99.6% of days.
  - Years: 2023: 11.5%, 2024: -6.5%, 2025: -15.2%, 2026: -3.7%
  - By regime (prior close): BEAR_TRENDING: 2.5%, BULL_TRENDING: -12.5%, HIGH_VOLATILITY_CHOP: -17.3%, NEUTRAL: 14.7%
  - Skipped/capped signals: max positions 45885, size capped to 25% of equity 357, insufficient cash 225, size capped to cash 218, gap through stop/target 7, size rounds to zero 3
- long_short: not run separately - the strategy emits no short entries, so it equals long-only.
- Robustness (long-only, build period, each param ±20% or ±1, 16 reruns): CAGR 2.2% to 9.3% (spread 7.1 pp), 100% of runs profitable.

### supertrend_psar (Supertrend + Parabolic SAR Confluence)

- long build (2016-01-04 to 2022-12-30): return 54.6% vs exposure-matched SPY 100.4%, 781 trades (0 short), avg R 0.07, invested 84.8% on average, exposure 99.3% of days.
  - Years: 2016: 1.9%, 2017: -3.8%, 2018: -7.7%, 2019: 29.1%, 2020: 7.4%, 2021: 32.6%, 2022: -7.1%
  - By regime (prior close): BEAR_TRENDING: 1.5%, BULL_TRENDING: -0.3%, HIGH_VOLATILITY_CHOP: 12.5%, NEUTRAL: 35.8%
  - Skipped/capped signals: max positions 11028, size capped to 25% of equity 810, insufficient cash 492, size capped to cash 390, gap through stop/target 10, size rounds to zero 1
- long test (2023-01-03 to 2026-11-20): return 41.2% vs exposure-matched SPY 80.3%, 474 trades (0 short), avg R 0.11, invested 87.3% on average, exposure 99.6% of days.
  - Years: 2023: 29.5%, 2024: 4.5%, 2025: 1.3%, 2026: 3.0%
  - By regime (prior close): BEAR_TRENDING: 5.4%, BULL_TRENDING: 44.1%, HIGH_VOLATILITY_CHOP: -7.6%, NEUTRAL: 0.6%
  - Skipped/capped signals: max positions 6564, size capped to 25% of equity 346, size capped to cash 240, insufficient cash 167, gap through stop/target 3, size rounds to zero 1
- long_short: not run separately - the strategy emits no short entries, so it equals long-only.
- Robustness (long-only, build period, each param ±20% or ±1, 16 reruns): CAGR 2.5% to 11.7% (spread 9.2 pp), 100% of runs profitable.

### vcp_breakout (VCP Breakout)

- long build (2016-01-04 to 2022-12-30): return 16.7% vs exposure-matched SPY 31.3%, 437 trades (0 short), avg R 0.02, invested 52.0% on average, exposure 81.4% of days.
  - Years: 2016: -3.8%, 2017: 22.3%, 2018: -2.7%, 2019: 2.9%, 2020: 8.4%, 2021: 4.8%, 2022: -12.8%
  - By regime (prior close): BEAR_TRENDING: -2.8%, BULL_TRENDING: -0.1%, HIGH_VOLATILITY_CHOP: 5.2%, NEUTRAL: 14.3%
  - Skipped/capped signals: max positions 443, size capped to 25% of equity 409, size capped to cash 144, insufficient cash 131, gap through stop/target 9
- long test (2023-01-03 to 2026-11-20): return -0.2% vs exposure-matched SPY 39.8%, 227 trades (0 short), avg R 0.02, invested 51.9% on average, exposure 92.5% of days.
  - Years: 2023: -4.8%, 2024: -4.0%, 2025: 20.1%, 2026: -9.1%
  - By regime (prior close): BEAR_TRENDING: 6.2%, BULL_TRENDING: -14.4%, HIGH_VOLATILITY_CHOP: -0.4%, NEUTRAL: 10.1%
  - Skipped/capped signals: size capped to 25% of equity 143, max positions 122, size capped to cash 57, insufficient cash 39, gap through stop/target 3
- long_short: not run separately - the strategy emits no short entries, so it equals long-only.
- Robustness (long-only, build period, each param ±20% or ±1, 14 reruns): CAGR -4.1% to 8.5% (spread 12.5 pp), 93% of runs profitable.

### zscore_mean_reversion (Z-Score Mean Reversion (Hurst-Filtered))

- long build (2016-01-04 to 2022-12-30): return 74.0% vs exposure-matched SPY 76.7%, 841 trades (0 short), avg R 0.21, invested 85.6% on average, exposure 99.9% of days.
  - Years: 2016: 13.3%, 2017: 3.1%, 2018: 2.5%, 2019: 17.1%, 2020: 21.6%, 2021: 9.7%, 2022: -7.0%
  - By regime (prior close): BEAR_TRENDING: 4.4%, BULL_TRENDING: 1.7%, HIGH_VOLATILITY_CHOP: 31.5%, NEUTRAL: 24.6%
  - Skipped/capped signals: max positions 29964, size capped to 25% of equity 1050, insufficient cash 1029, size capped to cash 382, gap through stop/target 13, size rounds to zero 1
- long test (2023-01-03 to 2026-11-20): return 51.5% vs exposure-matched SPY 88.3%, 470 trades (0 short), avg R 0.16, invested 86.9% on average, exposure 99.7% of days.
  - Years: 2023: 8.8%, 2024: 15.1%, 2025: 5.7%, 2026: 14.4%
  - By regime (prior close): BEAR_TRENDING: 0.8%, BULL_TRENDING: 19.8%, HIGH_VOLATILITY_CHOP: 3.9%, NEUTRAL: 20.8%
  - Skipped/capped signals: max positions 17069, size capped to 25% of equity 273, size capped to cash 219, insufficient cash 151, gap through stop/target 5, size rounds to zero 2
- long_short: not run separately - the strategy emits no short entries, so it equals long-only.
- Robustness (long-only, build period, each param ±20% or ±1, 14 reruns): CAGR 3.5% to 17.2% (spread 13.7 pp), 100% of runs profitable.
