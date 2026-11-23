# Roadmap: week of 30 Nov 2026

Every item runs through `/team` with A's two gates. Order follows the backlog (`.claude/team/backlog.md`). The goal is a strategy that **beats SPY at the same exposure, out of sample**, and is proven in paper trading.

## Before Monday (needs A)
- **Fill in `.env`:** A adds the Alpaca keys, Telegram token and chat id.
- **Finish F3b part 2:** install the always-on backend, switch the paper bot to the app's daily setups (the old bot is backed up first), and turn off the old Hermes scan jobs. The first real 08:00 brief and Saturday report go out after that.

## Mon–Tue: make the strategies use the account (R-4, P1)
- **Portfolio engine:** a portfolio backtest across all ~520 tickers at once, with max positions, a total-risk cap and ranking by signal quality. It replaces the per-ticker sleeves, which leave about 96% of capital idle.
- **Same rules everywhere:** the same engine drives the backtest, the brief and the bot, so there's one source of truth.
- **Done when:** the baseline reports average invested ≥ 30% and a walk-forward result against exposure-matched SPY.

## Wed: data honesty (D-2, R-1)
- **Spin-off adjustment:** fix the spin-off price gaps (CTVA, DD, HON, FDX) using the `ticker-lifecycle-check` skill.
- **Trading-logic audit:** check every strategy for lookahead and for its stop/target maths.

## Thu: tune on out-of-sample data only (R-2)
- **Sensitivity runs:** test the reward:risk minimum (2.0 / 2.5 / 3.0), the trend filter (on/off) and risk per trade, using walk-forward only. Keep only changes that hold up out of sample.

## Fri: readability and plumbing (U-5, U-6, U-4, E-1)
- **Plain English:** explain regime names, "exposure-matched" and reward:risk in plain words. Add the screened count to the brief, and say when a blocked order can be retried.
- **CI:** pin dependencies and run frontend lint plus Playwright in CI.

## Later
- Recognise name-change renames such as BK→BNY (U-7).
- Run all endpoints through one ticker normaliser (N-1).
- Fix the day-scan bugs (B-3), unless the bot switch retires that scan first.
- Decide whether `data/raw` stays tracked in git (Q-1).
