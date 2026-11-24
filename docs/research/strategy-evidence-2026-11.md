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
