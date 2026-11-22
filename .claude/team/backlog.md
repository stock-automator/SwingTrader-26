# Backlog

Status: `todo` | `in-progress` | `blocked` | `done`. Priority P1–P3.

| id | story | priority | status | notes |
|---|---|---|---|---|
| T-1 | As A, I want a self-improving agent team set up for this repo so that all future work is planned, tested and reviewed with my approval at the gates. | P1 | in-progress | step 0 done, agents written, skills table pending OK |
| C-1 | As A, I want unnecessary code and docs removed so that the app stays lean. | P1 | todo | remove-list needs A's OK before any delete |
| F-1 | As A, I want a new feature (to be described) so that … | P2 | blocked | waits until the team base is ready |
| B-1 | As A, I want the paper-trader bot's market-hours fallback to use New York time so that it never trades on UK clock times. | P3 | todo | `executor.is_market_open` fallback compares local GMT to ET hours |
| B-2 | As A, I want the Hermes swing/day scans to use this backend's signals so that alerts carry entry/stop/target and sizing. | P2 | todo | from the 2026-11-22 review |
| R-1 | As A, I want every strategy's trading logic audited so that I can trust signals. | P1 | todo | Carried over from the Sprint 8 audit's "Sprint 11" plan: verify each strategy's math, every lookahead vector and short handling. Also resolve or document why the screener sizes with `build_order` instead of the gated `build_risk_managed_order`. |
| R-2 | As A, I want reproducible baseline backtests (fixed universe, dates, costs, sizing) with sensitivity and walk-forward runs vs SPY, so that I know whether any strategy has an edge. | P1 | todo | Carried over from the Sprint 8 audit's "Sprint 12" plan. The 2026-11-22 quick test found 0 of 120 strategy/ticker runs beat buy & hold or SPY. |
| U-1 | As a new user, I want every screen to be understandable in 30 seconds, with loading, error and empty states, so that I know what to do. | P2 | todo | Carried over from the Sprint 8 audit's "Sprint 10" plan (header freshness, BacktestStudio data range, Simulator point-in-time note, Portfolio paper vs live). Merge with the 2026-11-22 customer feedback. |
| H-1 | As A, I want to see from the UI whether my data is fresh and the provider reachable. | P2 | todo | Sprint 9 left these undone: yfinance reachability check, freshness shown in the frontend, APScheduler auto-sync (or drop the dependency), and per-router tests (`api/data_health.py` is at 28% coverage). |
| D-1 | As A, I want `docs/api.md` and `docs/architecture.md` to match the code so that agents and I aren't misled. | P3 | todo | The Sprint 6 audit found ~14 endpoints missing from api.md. architecture.md only covers the backtest/screener path, and the README views list misses tabs. |
| P-1 | As A, I want universe sync to fetch in parallel so that a full refresh is fast. | P3 | todo | `data/universe.py::sync_universe` is sequential per ticker (Sprint 6 audit). |
| S-1 | As A, I want every order to go through the market/earnings guards and every state-changing endpoint to need a token, so that nobody on my LAN can place orders or redirect alerts. | P1 | todo | `api/orders.py:219` `/orders/submit` skips the guards (verified). No endpoint has auth. Expert pick #1. |
| R-3 | As A, I want backtests to size positions like the live path (fractional shares, the risk gate, exposure % reported), so that the comparison with SPY is fair. | P1 | todo | Expert pick #2 (domain-expert). |
| T-2 | As A, I want one short Telegram message with at most 3 setups (entry/stop/target/$ risk) or "no trades today", so that I can act from my phone. | P1 | todo | Expert pick #3 (customer). Overlaps B-2. |
| E-1 | As A, I want dependencies locked and CI to run frontend lint and Playwright on Node 22, so that nothing breaks silently. | P2 | todo | Expert picks #5 and #7 (tech-expert). |
| U-2 | As A, I want the UI to show each strategy's record after costs and an honest "no strategy beats SPY yet" banner. | P2 | todo | Expert pick #6. |
| B-3 | As A, I want the day scan's scoring to be correct so that the strongest setups aren't missed. | P2 | todo | Reviewer, 2026-11-22: `day_setup_scan.py:145` turns `range_position` 0.0 into 0.5. `avg_vol` always divides by 5. Each ticker is downloaded twice, and `sma_20` is a 5-bar mean. Fix both the repo copy and `~/.hermes/scripts/`. |
| Q-1 | Decide whether `data/raw/*.parquet` (~74MB) should stay tracked in git. | P3 | todo | It's tracked while the docs say it's a gitignored cache. Open question from the Sprint 6 audit. |
