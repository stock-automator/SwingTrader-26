# Strategy evidence review (cycle 06, 2026-11-24)

The domain-expert's research pass. Its rules are pre-registered here, before any backtest.

Context: published anomaly returns fall about 26% out-of-sample and 58% after publication (McLean & Pontiff, JF 2016). 65% of 452 anomalies fail replication (Hou, Xue & Zhang, RFS 2020). Our universe, S&P 500 large caps since 2016, is the hardest case.

## Tested first (rules fixed from the papers, no tuning)

1. **SPY intraday momentum** (Gao, Han, Li & Zhou, JFE 2018; Baltussen et al., JFE 2021).
   - Signal: the return from the previous 16:00 close to 10:00 today.
   - Trade: hold SPY from 15:30 to 16:00, long if the signal is positive and short if it is negative.
   - Variants:
     - every day;
     - PDT-capped (3 per 5 days), taking only the largest |signal|. Pick the threshold rule before seeing results: trade only when |signal| is above its trailing 1-year 80th percentile.
   - Costs: 1bp and 5bp per side, plus the break-even cost.
2. **Earnings-announcement-return drift** (Brandt et al. 2008; Chan, JFE 2003).
   - Data: 8-K Item 2.02 time from EDGAR, on New York time; a filing after the close moves day 0 to the next session.
   - Signal: EAR is the market-adjusted return over days -1 to +1 around day 0.
   - Trade: buy the top decile on day +2 and hold for 60 trading days. Variants: long-short, and long-only.
   - Caveat: Martineau (CFR 2022) finds the effect is zero in large caps since 2006.
3. **Sector-ETF momentum** (Moskowitz & Grinblatt, JF 1999; Moskowitz, Ooi & Pedersen, JFE 2012).
   - Universe: the 11 SPDR sector ETFs.
   - Rule A: hold the top 3 by 6-month return, rebalanced monthly.
   - Rule B: go long each sector only while its 12-month return is above T-bills (assume 0 if there is no T-bill data, and say so).

## Later candidates
- Turnover-conditioned short-term momentum (Medhat & Schmeling, RFS 2022).
- Turn-of-the-month on SPY (McConnell & Xu, FAJ 2008; contested).
- Earnings-announcement premium (Frazzini & Lamont; reported to have disappeared in the US).

## Dropped
- **Opening range breakout on "stocks in play":** in-sample only, needs about 20 day trades a day, and our gap-and-go replica lost money.
- **RSI(2):** only vendor evidence.
- **Plain short-term reversal:** failed replication.
- **Pre-FOMC drift:** gone after 2015.
- **Historical-news strategies:** Alpaca has no news history.

## Cycle 07 pre-registered rules (2026-11-24): steady multi-asset edge

A asked for a steady edge with a good reward-to-risk ratio; options, hedges and gold are allowed. Every rule below is taken as published, with no tuning.

- **Universe (ETFs):** SPY, QQQ, IWM, EFA, EEM, TLT, IEF, SHY (cash proxy), GLD, DBC, VNQ.
- **GTAA, Faber (2007, updated 2013):**
  - assets: SPY, EFA, IEF, DBC, VNQ, equal weight;
  - rule: hold each one only if its month-end close is above its 10-month SMA, otherwise hold SHY;
  - rebalance monthly.
- **GTAA aggressive (Faber):** of the same 5 assets plus GLD and TLT, hold the top 3 by the average of 1/3/6/12-month returns, each only while it is above its 10-month SMA.
- **Global equities momentum / dual momentum (Antonacci 2014):**
  - each month, if SPY's 12-month return is above SHY's, hold the better of SPY and EFA by 12-month return;
  - otherwise hold IEF.
- **Risk parity, inverse volatility:** SPY, TLT, GLD, DBC, weighted by the inverse of 60-day volatility and rebalanced monthly. A variant adds the 10-month SMA filter.
- **Permanent portfolio (Browne):** 25% each in SPY, TLT, GLD and SHY, rebalanced yearly.
- **Options proxy, cash-secured put writing:**
  - data: the CBOE S&P 500 PutWrite Index (PUT) and the BuyWrite Index (BXM), if the daily history can be downloaded from CBOE for free; otherwise skip;
  - benchmarks: SPY and 60/40 (SPY/IEF).
- **Judged on:** CAGR, max drawdown, Sharpe, Calmar (CAGR / max drawdown), worst year and % positive months.
- **Test periods:** build 2016–2022, test 2023+; also full-history results where the ETF data goes back to 2007, which covers 2008.
- **Costs:** 2bp per side.

## Cycle 07b pre-registered rules (2026-11-24): small and mid caps

The cycle 06 result on the S&P 500 was no earnings drift: the top-decile drift had t of 0.06 in the build period and −0.67 in the test period. The research expects these effects to survive mainly outside the largest stocks.

- **Universe (survivorship-free):**
  - US common stocks on NYSE, Nasdaq and AMEX, active and inactive, taken from Alpaca's assets list. Exclude ETFs, funds, ADRs, preferreds, warrants and units, using EDGAR's 10-K/10-Q filers and the SIC code where available.
  - Monthly eligibility: price ≥ $5, and 20-day average dollar volume ≥ $5M a day (tradeable for a small account).
  - Exclude the 500 largest by dollar volume (that's the S&P 500 space we already tested).
- **Signals** (decided at the month-end close, traded at the next open):
  1. **1-month reversal (Jegadeesh 1990):** buy the lowest decile of last month's return and hold 1 month.
  2. **6-1 momentum:** top decile, hold 1 month.
  3. **Earnings drift (EAR):** same rules as cycle 06, with EDGAR 8-K Item 2.02 dates.
  4. **Composite:** the average of the cross-sectional z-scores of (−1m return), (6-1 return) and (the latest EAR within 60 days, else 0). Buy the top 20, equal weight, rebalanced monthly.
- **Costs:** 10bp per side for stocks above $20M/day in dollar volume, 20bp per side below that. Also report results at 2× costs.
- **Sizing:** long-only for the headline; long-short only as a diagnostic.
- **Split:** build 2016–2022, test 2023+.
- **Benchmarks:** IWM, SPY, and an equal-weight version of the universe.
