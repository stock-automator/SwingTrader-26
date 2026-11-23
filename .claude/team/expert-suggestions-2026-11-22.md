# Team suggestions (22 Nov 2026)

These come from the domain-expert, system-architect, tech-expert and customer agents. They looked at the real code, and I spot-checked their claims. Effort: S = hours, M = a day or two, L = several days. Nothing here is built yet. Each one would go through `/team`, with your approval at Gate 1.

## Top picks (my ranking across all four)

| # | Suggestion | From | Effort | Why it matters |
|---|---|---|---|---|
| 1 | **Lock down the order endpoints.** Run every order through one guarded path and require a token on all state-changing endpoints. | architect | M | `POST /api/v1/orders/submit` sends orders to Alpaca **without** the market-hours and earnings guards (`api/orders.py:219-253`). No order, close-all or alert-config endpoint checks who is calling, and the server is meant to run on your LAN (`0.0.0.0`). Anyone on your network could place paper orders or redirect your alerts. |
| 2 | **Make backtests size positions realistically.** Add fractional shares, report "% of days invested" and "signals skipped", and send entries through the same risk gate the live code uses. | domain | S–M | Whole-share rounding plus 2% risk leaves most capital idle, while the buy-and-hold and SPY benchmarks are allowed fractional shares. The backtest also skips the 2.5R minimum, the portfolio-heat cap and the earnings lockout. That's a big part of why nothing "beat SPY", so it isn't a fair test yet. *(Note: the expert assumed my test split $1,000 across 12 tickers. It actually ran each ticker separately with the full $1,000, but the idle-capital point still holds.)* |
| 3 | **One short, actionable Telegram message.** At most 3 setups, each with entry, stop, target and risk in $, plus "no trades today" when there are none. Drop the weakness and sector blocks. | customer | S | Today's swing scan has no entry, stop or target, and can run to about 45 lines. Your words were "I couldn't act on it from my phone". |
| 4 | **Make the Hermes bot and scans call this backend** instead of reimplementing it. | architect | L | The bot has about 1,050 lines of its own strategies, with one function defined twice so the second silently replaces the first. It has its own sizing with different units (percent vs fraction), its own Alpaca client and its own cache. Two sources of truth. Doing this would delete most of the bot. |
| 5 | **Lock dependency versions** and install from a lock file in CI and Docker. | tech | M | Every requirement is `>=` with no upper bound, so pandas 3 or any new yfinance can break things silently. The Dockerfile says "pinned" but nothing is. |
| 6 | **Be honest in the UI.** Show each strategy's backtest record after costs next to its signals, and a "no strategy beats SPY yet" banner. Label results "survivor universe, optimistic". | customer + domain | S | The universe is *today's* S&P 500, so past results look better than reality (survivorship bias). The README's headline example implies the strategies win. |
| 7 | **CI: add frontend lint and Playwright, and move to Node 22.** | tech | S | 8 end-to-end specs exist but never run in CI. Node 20 reached end of life in April 2026. |

## Also worth doing (smaller or later)

- **Market calendar:** the session guard has no holidays or 1 PM half-days (`execution/guards.py:70-74`). Use Alpaca's calendar, as the bot does. (domain, S)
- **Earnings unknown:** if yfinance has no earnings date, orders go through. Say "earnings unknown" instead of staying silent. (domain, S)
- **Day-trade limits:** read Alpaca's `pattern_day_trader` / `daytrade_count` fields. FINRA's $25k PDT rule is being replaced in 2026, so don't hard-code it. (domain, S)
- **Walk-forward:** lead with out-of-sample results and warn when there are too few trades. (domain, S)
- **Shrink `data/universe.py`** by about 600 lines: drop the hard-coded fallback ticker lists and use the saved `config/universe.json`. (architect, S)
- **Replace black + isort + flake8 with ruff:** one tool instead of three. (tech, S)
- **Vectorise the slow pandas paths:** the z-score slope `rolling().apply(polyfit)` and the `iterrows` serialisers. (tech, S–M)
- **One "Today" view:** merge Dashboard, Live Screener and Signal Matrix into a single phone-friendly screen with plain-English labels. Also fix the README's "you'll land on Live Screener" step. (customer, M)
- **Portfolio-level backtest:** many tickers, max positions and total risk, which would replace the single-position `forward_tester.py`. (architect, L)
- **Split `api/schemas.py`** (1,010 lines) by router. (architect, M)
- **Lazy-load the journal charts** so Recharts isn't in the main bundle, and consider dropping framer-motion. (tech, M)

## Suggested order
Do 1 first, because it's a safety issue. Then 2 and 6 (a fair, honest test), 3 (your daily message), 5 and 7 (stability), and 4 last (big, and easier once 1 and 2 exist).
