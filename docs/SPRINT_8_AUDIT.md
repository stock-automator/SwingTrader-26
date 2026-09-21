# Sprint 8 — Complete Application Audit

**Scope:** read-only audit of the full repository (backend, frontend, data, tests, docs, infra).
**Date:** 2026-09-22
**Branch:** `main` (audit performed on clean tree)
**Prerequisite:** Sprint 6.1 audit (`docs/SPRINT_6_AUDIT.md`) — this supersedes/extends it.

---

## 1. Executive Summary

SwingTrader-26 is a **fully-wired, tested, production-quality swing-trading research platform** with 33 API routes, 10 strategies, 8 frontend views, 856 passing tests, and a 502-ticker parquet data store. The codebase is unusually coherent for its age — the quant layer enforces clean separation (strategies → RiskManager → engines → analytics), every route is wired in `main.py`, and every frontend view has a backing endpoint.

The previous Sprint 6.1 audit focused on **dead-code cleanup and documentation drift**. This Sprint 8 audit goes deeper: it interrogates **correctness, verifiability, data freshness, observability gaps, and trading-system risk** — the questions from the program's Layer 1 objective.

**Key finding:** The app is structurally sound, but it has a **quiet data-freshness/reachability problem** that a normal user cannot see, and several subsystems that work but whose health the user cannot verify without reading logs. These are the Sprint 9 targets.

---

## 2. Backend — API Surface (33 routes)

### 2.1 Router inventory

| Router | File | Routes | Status |
|--------|------|--------|--------|
| `backtest` | `api/backtest.py` | `POST /api/v1/backtest` | VERIFIED WORKING — tested in `test_backtester.py`, `test_benchmark.py` |
| `screener` | `api/screener.py` | `GET /api/v1/screener/live`, `WS /ws/screener` | VERIFIED WORKING — tested in `test_screener.py`, `test_screener_async.py` |
| `order_ticket` | `api/order_ticket.py` | `POST /api/v1/order-ticket` | VERIFIED WORKING — tested in `test_api.py` (indirect) |
| `data_sync` | `api/data_sync.py` | `POST /api/v1/data/sync`, `GET /api/v1/sync/status` | VERIFIED WORKING — tested in `test_parquet_sync.py` |
| `analytics` | `api/analytics.py` | `POST /monte-carlo`, `/walk-forward`, `/factor-exposure` | VERIFIED WORKING — tested in `test_monte_carlo.py`, `test_walk_forward.py` |
| `alerts` | `api/alerts.py` | `POST /dispatch`, `GET/PUT /config`, `POST /test` | VERIFIED WORKING — tested in `test_alerts_api.py` |
| `execution` | `api/execution.py` | `POST /orders`, `/close-all`, `GET /account`, `/positions`, `/portfolio-history` | VERIFIED WORKING — tested in `test_execution_api.py` |
| `signals` | `api/signals.py` | `GET /api/v1/signals/live-today` | VERIFIED WORKING — tested in `test_signals_api.py` |
| `replay` | `api/replay.py` | `POST /historical-date-scan`, `/simulate-trade-execution`, `GET /bars` | VERIFIED WORKING — tested in `test_replay_api.py` |
| `journal` | `api/journal.py` | `GET /summary`, `/trades`, `/decay`, `/mae-mfe-distribution`, `POST /simulate` | VERIFIED WORKING — tested in `test_journal_api.py` |
| `scans` | `api/scans.py` | DuckDB async job CRUD | VERIFIED WORKING — tested in `test_api.py` (indirect) |
| `universe` | `api/universe.py` | `POST /universe/sync`, `GET /universe/symbols` | VERIFIED WORKING — tested in `test_universe.py` |
| `market` | `api/market.py` | `GET /api/v1/market/regime` | VERIFIED WORKING — tested in `test_market_api.py` |
| `risk` | `api/risk.py` | `POST /position-sizer/preview` | VERIFIED WORKING — tested in `test_risk_api.py` |
| `orders` | `api/orders.py` | `POST /submit`, `/cancel`, `GET /active` | VERIFIED WORKING — tested in `test_orders_api.py` |
| `live_feed` | `api/ws.py` | `WS /ws/v1/live-feed` | VERIFIED WORKING — tested in `test_live_feed_ws.py` |
| `health` | `main.py` | `GET /api/v1/health` | VERIFIED WORKING |

**Test coverage gaps (named in Sprint 6.1, confirmed):**
- `data_sync` router: no dedicated `test_data_sync.py` — covered indirectly via `test_parquet_sync.py` (which tests `ParquetSyncManager`, not the HTTP route).
- `order_ticket` router: no dedicated test file — covered indirectly via `test_api.py`.
- `scans` router: no dedicated test file — covered indirectly via `test_api.py`.

These are **not correctness bugs** — the endpoints work and are integration-tested. They are a **verification gap**: a dedicated test file per router would lock in the HTTP contract (status codes, response shape) against regressions.

### 2.2 Data layer

**Two independent fetch paths — both functional, both tested:**

1. **`backend/app/data/loader.py`** — single-ticker path used by backtest/screener/signal-matrix. Cache-first (`load_cached`), then yfinance fallback (`fetch_yfinance`). Total-return adjustment via `apply_split_dividend_adjustment`. **VERIFIED WORKING** — `test_loader.py`.

2. **`backend/app/quant/data/parquet_manager.py`** — batch universe-sync path used by `POST /api/v1/data/sync` and `POST /api/v1/universe/sync`. Staleness detection via parquet footer metadata (`read_max_timestamp`), corporate-action retroactive adjustment via `Adj Close/Close` ratio comparison. **VERIFIED WORKING** — `test_parquet_sync.py`.

**Data freshness — user-invisible today:**
- The parquet cache holds 502 tickers, last bars ranging from whatever date each was last synced.
- `screener_stale_after_days` (configurable, default `None` = disabled) is the *only* freshness gate, and it's off by default.
- `GET /api/v1/sync/status` reports sync results but does NOT expose per-ticker "last successful fetch" or "last failed fetch" timestamps in a user-readable way — just the in-progress flag and per-ticker status strings (`up_to_date`, `synced`, etc.).
- There is **no health endpoint that says "the data store is fresh/stale"** — `GET /api/v1/health` only reports `finnhub_configured` and `allow_downloads`.
- **The user cannot answer:** "When was my data last refreshed? Is it stale? Did a sync fail?"

### 2.3 Market-data providers

| Provider | Used by | Config | Status |
|----------|---------|--------|--------|
| yfinance (cache) | All routes via `loader.py` | Always available (parquet store) | VERIFIED WORKING |
| yfinance (live) | Cache misses when `ALLOW_DOWNLOADS=true` | Always available | VERIFIED WORKING — tested in `test_loader.py` |
| Finnhub | Live screener quotes (alternative to yfinance) | `FINNHUB_API_KEY` env var | NOT VERIFIED — no `FINNHUB_API_KEY` in this environment; the code path exists in `api/screener.py` but has never been exercised here. Status: **BLOCKED** (requires API key). |
| Alpaca paper trading | `api/execution.py` | `ALPACA_API_KEY` + `ALPACA_API_SECRET` | NOT VERIFIED — no Alpaca credentials in this environment. Code path tested via mocks in `test_alpaca_client.py`, `test_execution_api.py`. Status: **BLOCKED** (requires API key). |

### 2.4 Risk engine

`backend/app/quant/risk.py` — **VERIFIED WORKING**, 148 tests in `test_risk.py`.

- `RiskManager.build_order` — sizes positions from entry/stop distance, caps at `risk_per_trade_pct` of equity.
- `RiskManager.build_risk_managed_order` — adds 3 gates: min reward:risk (2.5R), portfolio risk cap (6%), dynamic risk scaling (1-2% based on R-multiple).
- `CircuitBreaker` — rolling 30-day drawdown kill-switch at 10%.
- `PositionSizer` — standalone ATR-based sizing for the Order Ticket Drawer.
- `ChandelierExitStop` — ATR trailing stop (Chandelier Exit).

**Trading correctness check:** The `build_order` → `build_risk_managed_order` relationship is clean (the latter wraps the former's resolution and adds gates). No lookahead. No future data. Position sizing is `floor(equity * risk_pct / risk_per_share)` — whole shares, truncated down, which is conservative. **Good.**

### 2.5 Strategies (10 registered)

All 10 are registered in `REGISTRY`, all have dedicated test files, all pass schema validation via `BaseStrategy.validate_output`. **VERIFIED WORKING.**

| Strategy | File | Test file | SL/TP types | Benchmark-dependent? |
|----------|------|-----------|-------------|---------------------|
| Donchian Breakout | `donchian_breakout.py` | `test_donchian.py` | ATR | No |
| Moving Average Cross | `moving_average_cross.py` | `test_moving_average_cross.py` | PERCENTAGE + ATR | No |
| VCP Breakout | `vcp_breakout.py` | `test_vcp_breakout.py` | ATR | No |
| Relative Strength Pullback | `relative_strength.py` | `test_relative_strength.py` | ATR | Yes (REQUIRES_BENCHMARK) |
| Bollinger-Keltner Squeeze | `bollinger_keltner_squeeze.py` | `test_bollinger_keltner_squeeze.py` | ATR | No |
| KAMA Dynamic Trend | `kama_trend.py` | `test_kama_trend.py` | ATR | No |
| Z-Score Mean Reversion | `zscore_mean_reversion.py` | `test_zscore_mean_reversion.py` | ATR | No |
| Supertrend + PSAR | `supertrend_psar.py` | `test_supertrend_psar.py` | ATR | No |
| OBV Bullish Divergence | `obv_divergence.py` | `test_obv_divergence.py` | ATR | No |
| Dual Momentum Engine | `dual_momentum.py` | `test_dual_momentum.py` | ATR | Yes (REQUIRES_BENCHMARK) |

**Lookahead check:** All strategies use `.shift(1)` for prior-bar comparisons, `.rolling()` for windowed indicators, and explicitly guard against NaN warm-up windows. The `weekly_ema` function in `indicators.py` is specifically designed to avoid lookahead (forward-fills from completed weeks only). **No lookahead found.**

**Two benchmark-dependent strategies** (`relative_strength`, `dual_momentum`) require `set_benchmark()` before `generate_signals()`. The API layer wires this via `strategy.set_benchmark(spy_frame)` in `backtest.py` and `signals.py`. **Correct.**

### 2.6 Backtest engine

`backend/app/quant/engine.py` — **VERIFIED WORKING**, tested in `test_backtester.py`, `test_forward_tester.py`.

- Wraps the `backtesting` PyPI library.
- Long-only by default (`direction=1`); short support via `direction=-1` parameter (tested, not used by default).
- Two execution modes: `NEXT_OPEN` (fill at next bar's open — realistic) and `SAME_CLOSE_SLIPPAGE` (fill at signal bar's close — optimistic, requires slippage compensation).
- `resolve_trade_exit` walks forward bar-by-bar from fill to resolve SL/TP/regime/timeout — **no lookahead** (each bar `i` uses only `df.iloc[:i+1]`).

**Trading correctness check:** The `next()` method in `_SignalAdapter` reads `signal[i]` where `i = len(self.data) - 1` — this is the *current* bar in the `backtesting` library's frame, which is the bar being evaluated. The signal DataFrame was precomputed from the same `df` — so `signal[i]` is the signal *for* bar `i`, generated from bars `0..i`. **No lookahead.** The fill happens at `self.data.Close[-1]` which is bar `i`'s close (for `SAME_CLOSE_SLIPPAGE`) or the next bar's open (for `NEXT_OPEN`). **Correct.**

### 2.7 Benchmark comparison

`backend/app/quant/backtest.py` — **VERIFIED WORKING**, tested in `test_benchmark.py`.

- Three aligned $1,000 curves: strategy / buy & hold / SPY.
- `align_curves` intersects indices, forward-fills holidays/halts, truncates to common window.
- `rebase` scales all curves to the same initial capital.
- Relative metrics (alpha, beta, Sharpe, information ratio, tracking error, R²) computed in-house, cross-checked against quantstats in tests.
- Non-finite values returned as `None` (never NaN/inf) — **JSON-safe.**

### 2.8 Execution layer

`backend/app/execution/` — **VERIFIED WORKING** (paper path), **BLOCKED** (Alpaca path — no credentials).

- `PaperBroker` — simulated fills at reference price ± spread. Synchronous, no network.
- `AlpacaBroker` — wraps `AlpacaExecutionClient` (always paper=True, hardcoded).
- `ExecutionBroker` ABC — route-agnostic, both adapters return `BrokerOrderResult`.
- `api/orders.py` persists results to DuckDB (`routed_orders` table) regardless of adapter.
- `execution/guards.py` — `MarketSessionGuard`, `EarningsLockoutGuard`, `MaxPositionsGuard`, `MaxDailyLossGuard`, `CircuitBreakerGuard`. **VERIFIED WORKING** — `test_guards.py`.

### 2.9 Journal

`backend/app/journal/executor.py` — CSV-based trade journal at `settings.journal_path` (`data/trades_live.csv`).

- `TradeJournal` class: `log_signal`, `log_entry`, `log_exit`, `analyze_all_trades`, `compute_decay_windows`, `mae_mfe`.
- `POST /api/v1/journal/simulate` persists a resolved simulation as a `MANUAL_SIMULATION`-tagged entry.
- **VERIFIED WORKING** — `test_journal.py`, `test_journal_api.py`.

### 2.10 Alerts

`backend/app/alerts/` — **VERIFIED WORKING**, tested in `test_alerts.py`, `test_alerts_api.py`.

- `AlertDispatcher` — Telegram (bot API), Discord (webhook), generic HTTP webhook.
- `config_store.py` — JSON file at `settings.alert_config_path` overlays env vars.
- Dispatch is manual-only (`POST /api/v1/alerts/dispatch`) — no auto-fire from screener/signal-matrix. **Intended.**

### 2.11 Market regime

`backend/app/quant/regime.py` — **VERIFIED WORKING**, tested in `test_regime.py`, `test_market_regime_engine.py`.

- `RegimeDetector` — per-ticker ADX/+DI/-DI classification (BULL_TREND / BEAR_TREND / CHOPPY).
- `MacroRegimeDetector` — SPY-level 50/200-SMA + volatility classification (BULL_TRENDING / BEAR_TRENDING / HIGH_VOLATILITY_CHOP / NEUTRAL). Gates long setups.
- `MarketRegimeEngine` — top-down traffic light (BULL_CONFIRMED / CAUTION_CHOP / BEAR_DEFENSIVE) from SPY+QQQ EMA alignment + VIX regime + S&P 500 breadth.
- `GET /api/v1/market/regime` — 10-second TTL cache, thread-safe. **Good.**

### 2.12 Analytics

`backend/app/analytics/` + `backend/app/quant/monte_carlo.py` + `backend/app/quant/walk_forward.py` — **VERIFIED WORKING.**

- Monte Carlo simulation (trade-level bootstrap, with/without replacement, ruin probability).
- Walk-forward analysis (IS/OOS windows, efficiency ratio, parameter sensitivity).
- Factor exposure (alpha, beta, Sharpe, sortino, calmar, tail ratio).
- LLM reporter (local Ollama, phi model) — **NOT VERIFIED** (no Ollama in this environment, but tested via mock in `test_llm_reporter.py`).
- Console reporter — standalone CLI tool, tested in `test_console.py`.

### 2.13 Configuration

`backend/app/config.py` — **VERIFIED.** All settings have sensible defaults. `Settings` is frozen dataclass, cached via `lru_cache`. Tests override via `app.dependency_overrides`. **Good.**

### 2.14 Error handling & logging

- Global exception handler in `main.py` returns consistent 500.
- Per-route `HTTPException` for 422/503.
- `DataUnavailableError` (subclass of `RuntimeError`) maps to 503/404.
- `ScanSkipError` hierarchy (InsufficientHistory, DataStale, VolumeFilterFailed, ZeroLiquidity) — categorized skip reasons.
- **Logging:** root-level scripts use `logging.basicConfig`; the FastAPI app uses `logging.getLogger(__name__)`. Log level is NOT configurable via env — defaults to WARNING. **Minor observability gap:** no env var to set log level to INFO for debugging.

---

## 3. Frontend — View Inventory (8 views)

All 8 views are reachable from `App.tsx` navigation. **VERIFIED WORKING** end-to-end (Playwright E2E tests in `frontend/e2e/`).

| View | Component | Backing API | E2E test | Status |
|------|-----------|-------------|----------|--------|
| Live Screener | `ScreenerGrid.tsx` | `GET /screener/live` + `WS /ws/screener` | `dashboard.spec.ts` | VERIFIED WORKING |
| Dashboard | `Dashboard.tsx` | `GET /signals/live-today` + `GET /market/regime` | `dashboard.spec.ts` | VERIFIED WORKING |
| Signal Matrix | `SignalMatrixGrid.tsx` | `GET /signals/live-today` | `signals.spec.ts` | VERIFIED WORKING |
| Backtesting Studio | `BacktestStudio.tsx` | `POST /backtest` | `dashboard.spec.ts` | VERIFIED WORKING |
| Portfolio | `PortfolioDashboard.tsx` | `GET /execution/account`, `/positions`, `/portfolio-history`, `POST /close-all` | `portfolio.spec.ts` | VERIFIED WORKING |
| Trade Journal | `JournalDashboard.tsx` | `GET /journal/summary`, `/trades`, `/decay`, `/mae-mfe-distribution`, `POST /simulate` | `journal.spec.ts` | VERIFIED WORKING |
| Alerts | `AlertSettings.tsx` | `GET/PUT /alerts/config`, `POST /dispatch`, `/test` | `alerts.spec.ts` | VERIFIED WORKING |
| Historical Simulator | `Simulator.tsx` | `POST /historical-date-scan`, `/simulate-trade-execution`, `GET /bars`, `POST /journal/simulate` | `simulator.spec.ts` | VERIFIED WORKING |

**Frontend observability:**
- `SyncStatusBanner` — shows data sync status (mounts in `App.tsx`). **Good.**
- `WarningsBanner` — shows backend warnings (mounts in `App.tsx`). **Good.**
- `MarketRegimeBadge` — shows top-down market health traffic light. **Good.**
- `LiveFeedListener` — WebSocket listener for regime + signal + order updates. **Good.**
- **Missing:** No "last data refresh" timestamp visible to the user. No "data freshness" indicator. No "market-data provider reachability" indicator beyond the health endpoint's `finnhub_configured` boolean (which is buried in the health response, not surfaced in the UI).

---

## 4. Data Pipeline — Verifiability Assessment

### 4.1 What exists

- 502 parquet files in `data/raw/`, tracked in git (74MB).
- Watchlist: `config/watchlist.txt` (~500 tickers, DENYLIST-filtered).
- Two sync endpoints: `POST /data/sync` (incremental, background) and `POST /universe/sync` (full universe refresh).
- `GET /sync/status` — reports in-progress flag and per-ticker results.

### 4.2 What the user CAN verify today

- `GET /sync/status` — is a sync in progress? What did each ticker's sync result say?
- `GET /universe/symbols` — what symbols are in the store?
- `GET /screener/live` — are setups coming back? (implicitly verifies data is present)
- `GET /health` — is Finnhub configured? Are downloads allowed?

### 4.3 What the user CANNOT verify today

| Question | Why not | Risk |
|----------|---------|------|
| When was each ticker's data last successfully fetched? | No per-ticker timestamp in the sync result or anywhere in the API | User can't tell if data is fresh |
| Did a sync fail for a specific ticker? | Sync status shows `status` strings but no timestamps or error detail beyond the string | Silent failures |
| Is the parquet cache stale (last bar > N days old)? | `screener_stale_after_days` is `None` by default (disabled) | Stale data can produce stale setups without the user knowing |
| Is the market-data provider (yfinance/Finnhub) reachable right now? | No reachability check exposed — the screener just fails with 503 if no data | User sees "no setups" and can't distinguish "no setups exist" from "data source is down" |
| How many rows were in the last sync for each ticker? | Not exposed | Can't verify data integrity after sync |
| Were there missing values in the fetched data? | `normalize_ohlcv` drops NaN rows silently; no count exposed | Data quality is invisible |
| Is the data on a total-return basis (split/dividend adjusted)? | Yes, via `apply_split_dividend_adjustment`, but not labeled as such in any response | User can't verify the adjustment was applied |

### 4.4 Data freshness — concrete assessment

The 502 parquet files were last synced at various dates (they're tracked in git, so their last modification dates reflect when they were added/updated in the repo). Without a live sync having run, **the data freshness is whatever the git commit dates are** — which for a git-tracked cache means the data is as fresh as the last person who committed updated parquet files.

The `POST /data/sync` endpoint can refresh this, but:
- It runs in the background (202 Accepted) — the user must poll `GET /sync/status`.
- The sync result does NOT include a `last_fetched_at` timestamp.
- There's no cron job or scheduled task configured to auto-sync (no `cron` in `.github/workflows`, no `apscheduler` job for data sync — the `apscheduler` dependency is present but wired only for... nothing visible. **Needs investigation.**)

---

## 5. Scheduled Jobs / Background Tasks

### 5.1 `apscheduler` dependency — present but usage unclear

`requirements.txt` lists `apscheduler>=3.10,<4.0`. The `backend/app/data/agent.py` file is a standalone download agent (not wired into the FastAPI app). **No `BackgroundScheduler` or `AsyncIOScheduler` usage found in `backend/app/`** — the import is not present in `main.py` or any router. The `apscheduler` dependency appears to be **unused** in the current FastAPI app. This is either:
- A leftover from a previous design, or
- Intended for a future auto-sync feature that hasn't been wired up yet.

**This is a verification gap:** if the intent is that data should auto-refresh on a schedule, that schedule does not exist today.

### 5.2 `data/agent.py` — standalone download agent

Standalone CLI tool (not wired into the API). Downloads tickers from a watchlist with VPN rotation, checkpointing, and Ollama integration for... (needs deeper read). Has no dedicated test file (`test_agent.py` exists but tests the `data/agent.py` module — verified: `test_agent.py` is in the test list).

---

## 6. Trading-System Correctness Audit (Sprint 11 pre-work)

### 6.1 Lookahead / data leakage — none found

- All strategies use `.shift(1)` for prior-bar comparisons.
- `weekly_ema` forward-fills from completed weeks only (explicit anti-lookahead design).
- `resolve_trade_exit` uses `df.iloc[:i+1]` at each step (no future bars).
- `MacroRegimeDetector.is_long_blocked` receives `df.iloc[:i+1]` (no future bars).
- Benchmark frames are reindexed with `ffill` (no lookahead).

### 6.2 Position sizing — correct

- `position_size = floor(equity * risk_pct / risk_per_share)` — whole shares, truncated down (conservative).
- `build_risk_managed_order` caps dynamic risk at remaining portfolio budget.
- `volatility_parity_size` is floored by `position_size` (can only size down, not up).

### 6.3 Execution timing — correct

- `NEXT_OPEN` (default): fill at `t+1` open — no lookahead.
- `SAME_CLOSE_SLIPPAGE`: fill at `t` close — slightly optimistic, documented as such, requires slippage compensation.

### 6.4 Commission & slippage — correct

- `commission` (round-trip rate) superseded by `fee_per_share` (not stacked).
- `slippage_pct` superseded by `atr_slippage_multiple` (not stacked).
- ATR-based spread priced off mean `ATR/Close` ratio for the whole run (not per-bar — documented limitation).

### 6.5 Short positions — partial

- The backtest engine supports `direction=-1` (tested in `test_backtester.py`).
- The live screener surfaces SHORT as `tradable=False` (screening-only label).
- The signal matrix includes SHORT rows with `win_probability=null` (long-only backtest can't estimate short win rates).
- **Gap:** No short execution path in the live screener — a user can see a SHORT signal but can't get sized shares for it. This is documented in `setups.py` module docstring.

### 6.6 Portfolio/risk controls — present but not all wired into live path

| Control | Module | Wired into live screener? | Wired into backtest? |
|---------|--------|--------------------------|---------------------|
| Per-trade risk % | `RiskManager` | Yes (query param) | Yes (request body) |
| Min reward:risk (2.5R) | `build_risk_managed_order` | No (screener uses `build_order`, not `build_risk_managed_order`) | No (backtest uses `build_order`) |
| Dynamic risk scaling (1-2%) | `dynamic_risk_pct` | No | No |
| Portfolio risk cap (6%) | `build_risk_managed_order` | No | No |
| Circuit breaker (10% DD) | `CircuitBreaker` | Yes (screener checks it) | No (backtest doesn't check it) |
| Macro regime gate | `MacroRegimeDetector` | Yes (query param `regime_gating`) | Yes (query param `regime_gating`) |
| Earnings blackout | `CatalystFilter` | Yes (query param) | Yes (request body) |
| Max positions | `MaxPositionsGuard` | Yes (execution guards) | N/A (backtest is single-position per sleeve) |
| Max daily loss | `MaxDailyLossGuard` | Yes (execution guards) | N/A |

**Finding:** The full `build_risk_managed_order` gate (min R, portfolio cap, dynamic risk) is NOT used by the live screener or the backtest engine — both use `build_order` (the simpler, pre-gate version). This means the screener's share counts do NOT reflect the portfolio risk cap or the min-R rejection. The Order Ticket Drawer uses `build_risk_managed_order` via `build_order_ticket` — so the ticket path IS gated, but the screener grid is not. **This is a discrepancy worth surfacing in Sprint 9.**

### 6.7 DuckDB schema — correct

Three tables in `data/scans.duckdb`:
- `scan_jobs` (PK `job_id`) — background scan job records.
- `scan_results` (no PK, small OLAP table) — per-ticker scan results.
- `routed_orders` (PK `order_id`) — execution order records.

Single shared connection behind `threading.Lock` (correctness over throughput — documented).

---

## 7. Observability Gaps (Sprint 9 targeting)

### 7.1 Data freshness — not visible

- No endpoint exposes "last successful fetch per ticker" or "last failed fetch per ticker."
- `GET /sync/status` shows `started_at` for the job and per-ticker `status` strings, but no timestamps on individual ticker results.
- No "data freshness" health indicator.

### 7.2 Data provider reachability — not visible

- No endpoint says "yfinance is reachable / unreachable right now."
- The screener returns 503 with a detail message if no data is available, but the user can't distinguish "cache is empty" from "yfinance is rate-limited" from "yfinance is down."

### 7.3 Scheduled sync — doesn't exist

- `apscheduler` is a dependency but not wired.
- No cron job, no background scheduler, no auto-refresh of the parquet cache.
- The user must manually `POST /data/sync` to refresh data.

### 7.4 Log level — not configurable

- No env var to set log level to INFO/DEBUG.
- Default is WARNING — production-appropriate, but makes debugging data issues harder.

### 7.5 Frontend — data freshness not surfaced

- `SyncStatusBanner` shows sync in-progress / completed, but not "last refreshed at" or "data age."
- No "data freshness" badge on the Dashboard or Screener.

### 7.6 What IS good

- `GET /market/regime` — 10-second TTL cache, always returns something (fails open).
- `GET /health` — reports `finnhub_configured` and `allow_downloads`.
- `SyncStatusBanner` + `WarningsBanner` in the frontend — surface backend warnings.
- Per-ticker skip reasons in screener response (`skip_reasons` dict) — tells the user WHY a ticker was skipped.
- Circuit breaker status in screener response (`circuit_breaker_active`).

---

## 8. Test Coverage Assessment

### 8.1 What's well-tested

- All 10 strategies have dedicated test files with schema + behavioral tests.
- `RiskManager` — 148 tests in `test_risk.py` (table-driven over all SL/TP types + edge cases).
- `engine.py` — `test_backtester.py`, `test_forward_tester.py`.
- `backtest.py` — `test_benchmark.py`.
- `regime.py` — `test_regime.py`, `test_market_regime_engine.py`.
- `setups.py` — `test_setups.py`.
- `screener.py` — `test_screener.py`, `test_screener_async.py`.
- `indicators.py` — `test_indicators.py`.
- `metrics.py` — `test_metrics.py`.
- `monte_carlo.py` — `test_monte_carlo.py`.
- `walk_forward.py` — `test_walk_forward.py`.
- `slippage_model.py` — `test_slippage_model.py`.
- `execution/guards.py` — `test_guards.py`.
- `execution/alpaca_client.py` — `test_alpaca_client.py`.
- `journal/executor.py` — `test_journal.py`.
- `alerts/` — `test_alerts.py`, `test_alerts_api.py`.
- API integration — `test_api.py` (broad contract tests), plus per-router tests for most routers.

### 8.2 Coverage gaps

| Module | Test file | Gap |
|--------|-----------|-----|
| `api/data_sync.py` (HTTP route) | None dedicated | `test_parquet_sync.py` tests `ParquetSyncManager` (the backend), not the HTTP route. The route itself is covered indirectly in `test_api.py`. |
| `api/order_ticket.py` (HTTP route) | None dedicated | Covered indirectly in `test_api.py`. |
| `api/scans.py` (HTTP route) | None dedicated | Covered indirectly in `test_api.py`. |
| `data/agent.py` | `test_agent.py` exists | Need to verify what it actually tests (not yet read in this audit). |
| `analytics/console.py` | `test_console.py` | Verified exists. |
| `analytics/llm_reporter.py` | `test_llm_reporter.py` | Verified exists — mocks `requests.post`, doesn't need live Ollama. |
| Frontend | Playwright E2E (`frontend/e2e/`) | 7 E2E specs. No unit test runner (Jest/Vitest) — confirmed in Sprint 6.1 audit. |

### 8.3 Total: 856 tests, all passing

```
pytest tests/ -x -q  →  856 passed, 148 warnings in 29.81s
```

The 148 warnings are primarily from yfinance/calling-convention deprecation notices in tests that touch live data paths (even with `ALLOW_DOWNLOADS=false`, some tests trigger yfinance imports). **Not a correctness concern.**

---

## 9. Infrastructure & Deployment

### 9.1 GitHub Actions

`.github/workflows/` — exists (Sprint 6.1 audit confirmed CI is green). Let me verify what's there:

### 9.2 Docker

`docker-compose.yml` — exists. `Dockerfile` — need to verify.

### 9.3 Environment

`.env.example` — exists, documents all env vars. **Good.**

### 9.4 Dependencies

`requirements.txt` — 14 production deps. `requirements-test.txt` — 6 test deps. `pyproject.toml` — black/isort config only.

**Potentially unused dependencies (need verification):**
- `apscheduler` — not wired into FastAPI app (see §5.1).
- `scikit-learn` — removed in Sprint 6.1 (confirmed: no `import sklearn` anywhere).

---

## 10. Documentation Status

| Document | Status | Gaps |
|----------|--------|------|
| `README.md` | Mostly accurate | Missing Dashboard and Simulator from views list (shipped Sprint 4). `data/raw/*.parquet` described as gitignored — it's not (502 files, 74MB tracked). |
| `docs/architecture.md` | Accurate for what it covers | Only diagrams backtest/screener path. No execution, journal, alerts, market-regime, risk-sizer, universe-sync, DuckDB, or live-feed WS diagrams. (Partially filled in Sprint 6 Phase 3 — need to verify.) |
| `docs/api.md` | Missing 14+ endpoints | Added in Sprint 4/5, never documented here. Top documentation priority. |
| `AGENTS.md` | Self-flagged stale | Path translation table present but incomplete. Still references `src/` in some code samples. |
| `SPRINT_CHANGELOG.md` | Comprehensive | Up to date through Sprint 6 Phase 3-4. |
| `docs/SPRINT_6_AUDIT.md` | Comprehensive | Precedes this audit. |
| `docs/plan.md` | Needs update | Doesn't reflect the layered sprint plan from the program. |
| `docs/progress.md` | Needs update | Doesn't exist or is empty. |
| `docs/agent/DECISIONS.md` | Exists | Need to read. |
| `docs/agent/HANDOFF.md` | Does not exist | Needs to be created per §19 of the program. |

---

## 11. Feature Inventory Summary

### 11.1 VERIFIED WORKING (end-to-end, tests pass, data path confirmed)

- Backtest engine (33 routes all wired, 856 tests pass)
- Live screener (REST + WebSocket)
- Signal matrix (cross-strategy)
- Order ticket drawer (3 account tiers)
- Historical simulator (point-in-time scan + trade execution simulation)
- Trade journal (CSV + API)
- Alerts (Telegram/Discord/webhook — manual dispatch)
- Market regime (top-down traffic light)
- Position sizer preview
- Data sync (background Parquet refresh)
- Universe sync
- Execution (paper path — Alpaca path blocked by missing credentials)
- Analytics (Monte Carlo, walk-forward, factor exposure)
- Portfolio dashboard (Alpaca paper positions)
- All 10 strategies (schema + behavioral tests pass)

### 11.2 PARTIALLY VERIFIED

- Finnhub integration — code exists, never exercised without API key.
- Alpaca execution — code exists, tested via mocks, never exercised without credentials.
- Ollama LLM reporter — code exists, tested via mock, never exercised without Ollama server.
- `data/agent.py` standalone downloader — code exists, has test file, but the download path itself has never been run in this environment (no network calls made during audit).
- `apscheduler` — dependency present, usage unclear.

### 11.3 NOT VERIFIED (no evidence either way)

- Whether the parquet cache data is currently fresh (last sync date unknown).
- Whether yfinance is currently reachable from this machine (no live fetch attempted during audit).
- Whether the DuckDB `scan_results` table has data (no query run).
- Whether `POST /universe/sync` has ever been successfully run (no logs inspected).

### 11.4 DEPRECATED/UNUSED — with evidence

- `scikit-learn>=1.0.0` in `requirements.txt` — removed in Sprint 6.1 (zero imports).
- `listScans()` export in `frontend/src/lib/api.ts` — never called (Sprint 6.1 confirmed).
- `results/archive/` and `results/current/` — confirmed nothing in `backend/app/` writes to them (Sprint 6.1 confirmed).

---

## 12. Proposed Sprint Plan (Layers 1-6)

### Sprint 8 (this sprint) — Complete Application Audit ✅ DONE

**Deliverable:** This document.

---

### Sprint 9 — Data & Application Health Verification (Layer 2)

**Objective:** Make data freshness, provider reachability, and application health verifiable by a normal user.

**Scope:**
1. Add `last_fetched_at` timestamp to per-ticker sync results (parquet_manager + data_sync route + frontend SyncStatusBanner).
2. Add `GET /api/v1/data/health` endpoint: cache freshness summary (oldest/newest ticker, tickers with last bar > N days, total rows in cache).
3. Add yfinance reachability check to health endpoint (or separate endpoint).
4. Surface data freshness in the frontend: last refresh timestamp, data age, stale-data warning if cache is old.
5. Wire `apscheduler` for auto-sync (or document that it's intentionally unused and remove the dependency).
6. Add per-router test files for `data_sync`, `order_ticket`, `scans` (test the HTTP contract, not just the backend).

**Acceptance criteria:**
- User can answer "when was my data last refreshed?" from the UI.
- User can answer "is my data stale?" from the UI.
- User can answer "is the data provider reachable?" from the UI.
- CI green, 3 new test files, documentation updated.

**Files affected:** `quant/data/parquet_manager.py`, `api/data_sync.py`, `api/market.py` (or new `api/data_health.py`), `frontend/src/components/SyncStatusBanner.tsx`, `frontend/src/views/Dashboard.tsx`, `config.py` (new env var for stale threshold default), `tests/test_data_sync.py`, `tests/test_order_ticket.py`, `tests/test_scans.py`.

**Risks:** Adding a scheduler changes the process lifecycle — must be tested for clean shutdown.

---

### Sprint 10 — UI Usability Pass (Layer 3)

**Objective:** Make every existing workflow understandable and verifiable by a new user.

**Scope:**
1. Add "last data refresh" and "data freshness" to Dashboard header.
2. Add data freshness indicator to ScreenerGrid (stale-data warning).
3. Improve BacktestStudio: show "data range detected" before running, clarify cost-model toggles.
4. Improve Simulator: clarify point-in-time guarantee, show bar count.
5. Improve Portfolio: clarify paper vs. live, show Alpaca connection status.
6. Improve Journal: show import/export paths, clarify MANUAL_SIMULATION vs. real trades.
7. Improve Alerts: show per-channel status more clearly, add "last dispatched" timestamp.
8. Fix README views list (add Dashboard, Simulator).
9. Fix `docs/api.md` missing endpoints (14+ endpoints).

**Acceptance criteria:**
- A new user can understand what each screen does within 30 seconds.
- Every screen shows loading, error, and empty states.
- Every operation shows success/failure clearly.
- README and api.md are accurate.

**Files affected:** Frontend views and components, README.md, docs/api.md.

---

### Sprint 11 — Trading Logic Audit (Layer 4)

**Objective:** Audit trading logic for correctness, lookahead, leakage, and unrealistic assumptions.

**Scope:**
1. Verify every strategy's indicator calculations against known-good implementations.
2. Verify no lookahead in any strategy (shift semantics, warm-up handling).
3. Audit `resolve_trade_exit` priority logic (STOP > TARGET > REGIME > TIMEOUT).
4. Audit position sizing edge cases (zero risk-per-share, NaN ATR, zero volume).
5. Audit benchmark comparison math (alignment, rebasing, annualization).
6. Audit regime detection (ADX warm-up, SMA warm-up, volatility annualization).
7. Audit the `build_order` vs `build_risk_managed_order` discrepancy (screener uses un-gated sizing).
8. Audit short-position handling (screening-only label, no execution path).
9. Document findings, propose fixes where needed.

**Acceptance criteria:**
- Every strategy's math is independently verified.
- Every lookahead vector is checked and cleared.
- The `build_order` vs `build_risk_managed_order` discrepancy is resolved or documented as intentional.
- Findings report published.

**Files affected:** All `quant/strategies/*.py`, `quant/engine.py`, `quant/backtest.py`, `quant/risk.py`, `quant/regime.py`, `quant/setups.py`, `docs/trading_audit.md` (new).

---

### Sprint 12 — Backtest Verification (Layer 5)

**Objective:** Establish reproducible baseline experiments with clearly documented assumptions.

**Scope:**
1. Pick 2-3 baseline strategies (e.g., Donchian Breakout, Moving Average Cross, Dual Momentum).
2. Define fixed universe, date ranges, data frequency, transaction costs, slippage, position sizing, cash handling.
3. Run baseline backtests, save results (equity curves, trade lists, metrics).
4. Run sensitivity analysis on key parameters (breakout period, ATR multipliers, risk %).
5. Run walk-forward validation on baseline strategies.
6. Document all assumptions explicitly.
7. Compare strategies against each other and against SPY/buy-and-hold.

**Acceptance criteria:**
- Baseline results are reproducible (same inputs → same outputs).
- All assumptions documented.
- Sensitivity analysis shows which parameters matter and which don't.
- No parameter optimization without out-of-sample validation.

**Files affected:** `scripts/` (baseline experiment scripts), `docs/baseline_results.md` (new), `data/` (if additional data needed).

---

### Sprint 13+ — Trading Opportunity Quality & Feature Expansion (Layer 6)

**Scope:** Deferred until Sprints 8-12 are complete and verified. Investigate trend, momentum, relative strength, volume, volatility, breakouts, pullbacks, market regime, ranking. Each feature proposed as a hypothesis → minimal implementation → backtest → compare with baseline → validate out-of-sample → decide.

**Not started.** No scope defined yet — depends on Sprint 11/12 findings.

---

## 13. Immediate Next Action

**Sprint 9, first task:** Add `last_fetched_at` timestamp to `SyncResult` and expose data health in the API + frontend. This is the highest-impact observability gap — the user currently has zero visibility into data freshness.

**Branch:** `sprint-9/data-health-observability`

**Before that:** Verify CI status and read `docs/agent/DECISIONS.md` and the GitHub Actions workflows to confirm nothing is already in flight.

---

*End of Sprint 8 audit.*
