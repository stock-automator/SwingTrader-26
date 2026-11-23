# Cycle 03: strategy scorecard and daily shortlist (🛑 Gate 1)

Tier: **full**. This cycle covers strategy and benchmark maths, sizing, and shorts.

## A's answers (23 Nov)
- **Stocks:** all ~520.
- **Period:** from 2016, with a held-out test period.
- **Ranking:** use all the criteria.
- **Position size:** at most 25% of capital per trade.
- **Direction:** longs and shorts.
- **Holding period:** 1 day to 2 months.
- **Shortlist:** one section per strategy.

## Defaults I propose (A can change any)
| Setting | Proposal | Why |
|---|---|---|
| Starting capital | $10,000 | Easy to read. Results scale, except for whole-share rounding. |
| Risk per trade | **1%** of equity, from the stop distance | 2% with up to 4 open positions can mean an 8% drawdown on one bad day. |
| Position size cap | **25%** of equity per position, as you asked | Prevents one stock dominating. |
| Max open positions | **6** | 25% each still allows smaller positions alongside. |
| Total open risk cap | **6%** of equity | The live gate's "portfolio heat" rule. |
| Time stop | Exit after **40 trading days** (~2 months) | Your maximum holding period. |
| Shorts | Allowed in the backtest. Live, only when the market regime isn't bullish. Borrow cost assumed 0, and every short-side result is flagged. | Shorting against a strong market is how small accounts blow up. |
| Test split | Build and tune on **2016–2022**. Final score on **2023–today**, which is never tuned on. | So we don't fool ourselves. |
| Shortlist size | Up to **3 per strategy**, and only for strategies that pass the scorecard | Keeps the daily message phone-sized. |

## Plan
1. **Portfolio backtest engine** (prerequisite). Runs one strategy across all 520 stocks at once with the limits above and a time stop, for longs and shorts. Built on the existing engine, risk gate and costs. It replaces the single-position forward tester.
2. **Scorecard.** Each of the 10 strategies runs alone, long-only and long+short, on the same rules. Scored on:
   - return vs SPY at the same exposure;
   - profit factor;
   - win rate;
   - average win vs average loss (payoff);
   - max drawdown;
   - number of trades (fewer than 30 is flagged);
   - year-by-year and market-regime consistency;
   - robustness to ±20% changes in each parameter;
   - held-out result vs build-period result.

   One ranked table, with a composite score that weights all of these.
3. **Improve the top 2–3.** Tune on 2016–2022 only, and keep a change only if it holds on 2023–today.
4. **Daily shortlist.** One section per strategy, for passing strategies only. Each strategy ranks its own picks by its own signal strength and shows up to 3, each with entry, stop, target, shares, $/£ risk and that strategy's scorecard line. It goes in the 08:00 Telegram brief, and the paper executor trades it within the limits above.

## Risks
- Survivorship bias: today's members only. This is labelled, and fixing it is a later item.
- Short borrow and fees aren't modelled. Shorts are flagged.
- Runtime: about 520 stocks × 10 strategies × variants is several minutes per strategy, run offline.

## Needs A
1. Approve the plan, or change any default above.
2. Shortlist size: 3 per strategy OK?
