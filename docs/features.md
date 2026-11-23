# SwingTrader-26: what it does and how it helps you

This is a research and paper-trading tool. Nothing here is financial advice, and no real money is traded.

## Every day, automatically
| Feature | What it does | How it helps you |
|---|---|---|
| **Daily Telegram brief** (08:00 UK, weekdays) | Lists up to 3 setups, each with entry, stop, target, whole shares and risk in $ and £. When nothing qualifies, it says "No trades today" and why. It also flags stale data, shows day trades used (n/3) and the GBP/USD rate. | One short message to read before the US open, with no chart-watching. |
| **Morning data refresh** (07:30 UK) | Updates prices for every stock in your list, plus SPY and GBP/USD. If the Mac was asleep, it catches up when it wakes. | The brief and backtests always use current prices. |
| **Monthly ticker list** (1st of the month, 07:00 UK) | Rebuilds the S&P 500 + Nasdaq-100 list (518 stocks today) from live sources. New members get their price history, renamed tickers are detected, and removed tickers are flagged but never deleted. If a download fails, the old list is kept. | Your universe stays correct without you maintaining a watchlist. |
| **Saturday weekly report** (09:00 UK) | Covers your paper trades this week and since the start: win rate, net $ and £ after costs, and open positions. The comparison with SPY only appears from 30 trades onward. It also lists the month's ticker-list changes. | An honest view of whether the forward test is working, not just backtests. |

## Safety
| Feature | What it does | How it helps you |
|---|---|---|
| **Order guards** | Every order route checks the real market calendar (holidays and half-days) and the earnings and split dates. New buys are refused if the market is closed or a check can't run. Selling shares you hold always works. | No accidental entries after hours or right before earnings. |
| **Risk gate** | Requires at least 2.5 reward:risk, sizes each position with dynamic risk, caps total risk across positions, and enforces daily-loss and drawdown stops. | The same rules apply in backtests, the brief and the bot. |
| **Secrets kept local** | API keys, the Telegram token and the chat ID stay in your local `.env`. They're hidden in logs and in error messages. | Nothing sensitive leaks through the app. |

## Research
| Feature | What it does | How it helps you |
|---|---|---|
| **Fair backtests** | Shows $1,000 in the strategy next to buy & hold, SPY, and SPY with the same amount invested. Includes costs (and FX if set), optional fractional shares, the live risk gate, a trend filter with no look-ahead, and out-of-sample results first. Warnings cover survivorship bias and small samples. | Tells you whether a strategy really has an edge. Today: +7.2% vs +8.0% for SPY with the same exposure, so no edge yet. |
| **10 strategies** | Donchian, MA cross, VCP, relative strength, Bollinger-Keltner squeeze, KAMA, z-score mean reversion, Supertrend/PSAR, dual momentum and OBV divergence. | One set of building blocks to test ideas with. |
| **Walk-forward and Monte Carlo** | Out-of-sample windows, plus bootstrap risk-of-ruin estimates. | Protects against curve-fitting. |
| **Historical simulator** | Replays the scan as it would have looked on any past date. | Lets you check what a signal looked like at the time, with no peeking. |
| **Market regime** | Classifies the market (bull, bear or choppy) and blocks new longs when conditions are against them. | Keeps you out of the worst conditions. |

## Web app (React, on your Mac)
The app has these tabs:
- Dashboard
- Live Screener
- Signal Matrix
- Backtesting Studio
- Portfolio (Alpaca paper)
- Trade Journal (MAE/MFE, win-rate decay)
- Alerts (Telegram, Discord or webhook)
- Historical Simulator

## Data
- About 520 US stocks with daily prices since 2015, plus SPY and GBP/USD.
- Delisted names are archived, and the blocked list records the date and reason for each.
- A data-health check shows stale and delisted tickers.

## Not done yet (see `.claude/team/backlog.md`)
- A portfolio engine that uses more of your capital. Today only 3.4% is invested on average.
- Spin-off price adjustment (CTVA, DD, HON, FDX).
- Plain-English labels in the brief and backtests.
- The always-on service and the paper bot using the app's setups are waiting for your `.env` file.
