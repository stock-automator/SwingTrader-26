# Sprint 6.1 — Phase 1 Audit

**Scope:** read-only audit of the full repository (backend, frontend, docs, config, tests). No code was modified while producing this document.
**Date:** 2026-09-20
**Branch:** `feature/sprint-6.1-audit-cleanup`

## 1. Executive summary

The app is in good shape for a 5-sprint-old project: every route is wired, every frontend view is reachable, and no strategy or component is silently orphaned. There is **no large body of dead code to rip out** — most "unused" candidates turned out to be intentional standalone CLI tools (documented, tested, just not on the HTTP request path). The real Phase 2 work is smaller and more surgical than "delete a bunch of files":

- One genuinely unused dependency (`scikit-learn`).
- One genuinely unused frontend export (`listScans()`).
- Two orphaned output directories (`results/archive/`, `results/current/`) that predate the FastAPI refactor and that nothing in `backend/app/` writes to.
- A significant **documentation drift** problem: `docs/api.md` is missing 14 of the app's endpoints (everything shipped in Sprint 4/5), `AGENTS.md` still describes the pre-refactor `src/` layout in several code samples, and both READMEs' "views" lists omit Dashboard and Simulator.
- A few items that look like dead code but are **not** — they need a human decision, not a delete, because deleting them could remove a tool someone still runs manually (see §5.2).
- One real (but out-of-scope-for-"surgical") performance finding: `data/universe.py`'s `sync_universe` fetches every ticker sequentially, no threadpool/async batching.

Full sub-reports (kept for reference, not part of the PR): produced by three parallel research passes over backend, frontend, and documentation.

## 2. Feature inventory

### 2.1 User-facing (frontend, 8 views — `frontend/src/App.tsx` `NAV_ITEMS`)

| View | Component | Backing endpoints |
|---|---|---|
| Live Screener | `components/ScreenerGrid.tsx` | `GET /api/v1/screener/live`, `WS /ws/screener` |
| Dashboard | `views/Dashboard.tsx` | `GET /api/v1/signals/live-today`, `GET /api/v1/market/regime` |
| Signal Matrix | `components/SignalMatrixGrid.tsx` | `GET /api/v1/signals/live-today` |
| Backtesting Studio | `components/BacktestStudio.tsx` | `POST /api/v1/backtest` |
| Portfolio | `components/portfolio/PortfolioDashboard.tsx` | `GET /api/v1/execution/account`, `/positions`, `/portfolio-history`, `POST /close-all` |
| Trade Journal | `components/journal/JournalDashboard.tsx` | `GET /api/v1/journal/summary`, `/trades`, `/decay`, `/mae-mfe-distribution`, `POST /simulate` |
| Alerts | `components/alerts/AlertSettings.tsx` | `GET/PUT /api/v1/alerts/config`, `POST /dispatch`, `/test` |
| Historical Simulator | `views/Simulator.tsx` | `POST /api/v1/backtest/historical-date-scan`, `/simulate-trade-execution`, `GET /bars`, `POST /journal/simulate` |

`ScreenerGrid` stays mounted at all times (display:none toggle) because it owns the WebSocket that also drives the header regime pill — intentional, documented in-code at `App.tsx:191-195`.

### 2.2 Backend (33 routes across 12 routers, all wired in `main.py:62-77`)

See the full endpoint table in the backend sub-audit. Summary by subsystem: backtest, screener (+ WS), order-ticket, data-sync, analytics (monte-carlo/walk-forward/factor-exposure), alerts, execution (orders/account/positions/close-all), signals, replay (bars/historical-date-scan/simulate-trade-execution), journal, scans (DuckDB async jobs), universe (sync/symbols), market (regime), risk (position-sizer preview), orders (submit/cancel/active), live-feed WS, health.

**Test coverage gap:** `data_sync`, `order_ticket`, and `scans` routers have no dedicated `test_<module>.py` — only indirect coverage via `tests/test_api.py`. Not a correctness bug today, but a named test file each would harden Phase 2's "tests for key workflows" goal.

## 3. Architecture & data models

- **Two independent market-data fetch paths**: `backend/app/data/loader.py::fetch_yfinance` (ad-hoc/backtest fetches) and `backend/app/quant/data/parquet_manager.py::_default_fetch_raw` (universe-sync batch path, with staleness detection + corporate-action retroactive adjustment). Different call sites, not a bug, but duplicated retry/adjustment logic that will drift over time — flag for a future consolidation sprint, not Phase 2.
- **DuckDB** (`backend/app/db/session.py`): single shared connection behind a `threading.Lock` (deliberate, documented correctness-over-throughput tradeoff). Three tables in `data/scans.duckdb`: `scan_jobs` (PK `job_id`), `scan_results` (no PK/index — low priority since it's a small OLAP table, not a hot path), `routed_orders` (PK `order_id`).
- **Risk/sizing**: consolidated in one module, `backend/app/quant/risk.py` (`RiskManager`, `CircuitBreaker`, `PositionSizer`, `ChandelierExitStop`).
- **Strategies**: 10 files under `quant/strategies/` + `base.py`, all registered and tested — no dead strategies.
- **Config**: `config/trading_config.json` (risk/strategy tunables, tracked), `config/universe.json` (gitignored, generated), `config/watchlist.txt` (seed tickers, DENYLIST-filtered per PR #12).

## 4. Dead / unused code

### 4.1 Safe to remove now (high confidence, no ambiguity)

| Item | Location | Why safe |
|---|---|---|
| `scikit-learn>=1.0.0` dependency | `requirements.txt` | Zero `import sklearn` anywhere in `backend/`, `scripts/`, `tests/` |
| `listScans()` export | `frontend/src/lib/api.ts:226` | Never called from any component/view/hook — but see note below |
| Stale local `__pycache__` dir | `backend/app/quant/risk/` (disk only, not tracked in git) | Bytecode for `manager.py`/`portfolio_manager.py`/`ranker.py` — none exist as source anymore, superseded by the consolidated `quant/risk.py`. Untracked, already gitignored; harmless to `rm -rf` locally so it stops shadowing imports |

Note on `listScans()`: `useOpportunityFeed.ts:54-57` has a comment referencing a future `/api/v1/scans` job-feed integration, so this may be intentional scaffolding rather than true dead code — confirm with the user before deleting (see §7).

### 4.2 Looks dead, is not — do not delete without a decision

These are unreferenced by any API route but are **documented, intentional, standalone CLI tools**, each with its own test file:

| Module | Purpose | Tests | Doc references |
|---|---|---|---|
| `backend/app/data/agent.py` | Legacy VPN-rotation-aware yfinance batch downloader | (none directly — pre-dates test suite) | `README.md:465`, `AGENTS.md:24,176,290` (paths stale — still say `src/agent.py`) |
| `backend/app/analytics/console.py` | Standalone CLI reporting tool | `tests/test_console.py` | `AGENTS.md:62` |
| `backend/app/analytics/llm_reporter.py` | Local-Ollama-backed report generator | `tests/test_llm_reporter.py` | `AGENTS.md:281-298` |
| `scripts/generate_demo_videos.py` | Playwright walkthrough recorder, run manually | — | `README.md:486`, `docs/code_review.md:294` |

**Recommendation:** keep all four. If Phase 2 touches them at all, it should only be to fix the stale `src/`-era path references pointing at them in `AGENTS.md`, not to delete the modules themselves.

### 4.3 Orphaned output directories (needs a decision, not auto-delete)

- `results/archive/` (4 CSVs) and `results/current/` (2 empty subdirs, `rsi_optimization/`, `supertrend_rsi/`) — **confirmed nothing in `backend/app/` writes to either path**. The only writer under `results/` is `journal/executor.py:543`'s `export_summary(output_file="results/trade_journal_summary.json")`, which doesn't touch `archive/` or `current/`. The CSV naming style (`backtest_results_ma_cross.csv`) matches the pre-refactor CLI-era workflow described in `AGENTS.md`'s old `src/` examples. Likely safe to delete, but flagging for explicit sign-off since it's output data, not code — cheap to keep, and deleting is one-way if not backed up elsewhere.

### 4.4 Frontend

No orphaned components, views, or tabs. Every file under `components/` is imported and rendered at least once. `types.ts` (~70 exported types, 703 lines) — each backs a real API response shape in active use. Frontend dead-code surface is essentially just `listScans()` above.

## 5. Documentation drift

**Biggest gap — `docs/api.md`:** explicitly promises ("if you change a route, update this page in the same change") to mirror the backend exactly. It documents 8 routers/~10 endpoints but is **missing 14 endpoints across 8 routers**, all shipped in Sprint 4/5: order-ticket, data-sync (×2), scans (×3), universe (×2), market/regime, risk/position-sizer, orders (×3), live-feed WS. Every router added in the last two sprints has zero coverage here — this is the top documentation fix for Phase 2.

**`docs/architecture.md`:** accurate for what it covers (backtest/screener data-flow diagram — every function reference checked out), but only diagrams that one path. Zero mention of execution, journal, alerts, market-regime, risk-sizer, universe-sync, DuckDB scans, or live-feed WS — six real subsystems with production code and tests but no presence in the repo's one architecture diagram.

**`AGENTS.md`:** already self-flags staleness via a path-translation table (lines 11-33) added in a prior session — good pattern, but incomplete. Concretely still-broken spots the table doesn't cover:
- Line 153/156: worked example still says `pytest tests/ -v --cov=src` and `from src.core.risk import RiskManager`.
- Line 356: `git status --porcelain src/ tests/` should be `backend/ tests/`.

**`README.md` and `frontend/README.md`:** both "views" lists (README.md:37-46, frontend/README.md:26-34) name only 6 of the 8 tabs — **Dashboard** and **Simulator** (shipped Sprint 4, PR #10) are missing from both. Everything else spot-checked in README.md (env var table, Docker steps, project-layout block) matches reality.

**`docs/architecture.md` line 11 / README.md line 490:** both describe `data/raw/*.parquet` as gitignored cache. It is not — 502 files, 74MB, are tracked in git (see §7).

## 6. Performance bottlenecks

- **`data/universe.py::sync_universe`** (711-line module) does fully sequential per-ticker fetches — no `ThreadPoolExecutor`/`asyncio.gather` found. Real optimization opportunity for a large universe, but it's a feature-level change to business logic, not a surgical cleanup — recommend a dedicated future sprint rather than folding into Phase 2's "surgical edits only" mandate.
- Sync `def` route handlers in `api/replay.py`, `api/data_sync.py` are **not** a problem — FastAPI/Starlette runs sync path operations in a threadpool automatically; no event-loop blocking.
- `scan_results` DuckDB table has no PK/index despite being queried by `job_id` — low priority, small OLAP table, not a hot path.
- Frontend is in good shape: `useWebSocket.ts` has correct cleanup/backoff, `CandlestickChart.tsx` scopes chart lifecycle correctly to mount/unmount, `ScreenerGrid`/`SignalMatrixGrid` (the two components most likely to hold large row counts) already use `useMemo`/`useCallback`. Only minor, low-priority gap: `TradesTable.tsx` and `JournalTradesTable.tsx` lack memoization, but current row counts make this a non-issue in practice.

## 7. Open questions requiring a decision before Phase 2 touches anything

1. **`listScans()`** (`frontend/src/lib/api.ts:226`) — delete as dead code, or keep as scaffolding for a future scan-history UI?
2. **`results/archive/` and `results/current/`** — delete as orphaned pre-refactor output, or keep?
3. **`data/raw/*.parquet`** (502 files, 74MB tracked in git) — is committing the warm cache intentional, or should this be gitignored (and, if so, should the already-tracked files be removed from git in a follow-up, out of scope for this sprint)? Not proposing any action here without explicit direction — untracking 74MB of history-bearing binary files is a bigger call than a doc/dead-code cleanup.
4. **`backend/app/data/agent.py`, `analytics/console.py`, `analytics/llm_reporter.py`** — confirmed as intentional standalone tools; default plan is to keep them and only fix their stale doc references. Flag if any should actually be retired.
5. **`requirements-test.txt`** lists `black`, `isort`, `bandit`, `safety`, `watchdog` — none appear invoked by `pytest.ini`, `.flake8`, or anything under `.github/`, and no `.pre-commit-config.yaml` exists. Likely unused tooling deps, but lower confidence — worth confirming before dropping.

## 8. Phase 2 checklist

**Code/dependency cleanup (surgical, high confidence):**
- [ ] Remove `scikit-learn` from `requirements.txt`
- [ ] Remove `listScans()` from `frontend/src/lib/api.ts` *(pending answer to §7.1)*
- [ ] Remove `results/archive/`, `results/current/` *(pending answer to §7.2)*
- [ ] Remove unused test-tooling deps from `requirements-test.txt` *(pending answer to §7.5)*

**Documentation fixes:**
- [ ] Add the 14 missing endpoints to `docs/api.md` (order-ticket, data-sync, scans, universe, market, risk, orders, live-feed WS)
- [ ] Add Dashboard and Simulator to the views list in `README.md` and `frontend/README.md`
- [ ] Fix stale `src/`-era references in `AGENTS.md` (lines 153, 156, 356) and correct the `agent.py`/`console.py`/`llm_reporter.py` path pointers
- [ ] Correct the "gitignored" claim about `data/raw/*.parquet` in `docs/architecture.md:11` and `README.md:490` to reflect that it's tracked (or fix the actual gitignore behavior, per §7.3 decision)

**Test suite enforcement:**
- [ ] Run full backend suite: `pytest`
- [ ] Run full frontend suite: `npm test` (if defined) and `npx playwright test`
- [ ] Add dedicated test files for `data_sync`, `order_ticket`, `scans` routers (currently only indirectly covered)
- [ ] Add `frontend/e2e/backtest.spec.ts` — Backtesting Studio's form-submit → results flow (stat cards, equity chart, trades table, warnings banner) has no dedicated E2E spec today, only the shallow `smoke.spec.ts` tab-renders check
- [ ] Fix any failures surfaced by the above until 100% pass
- [ ] Update `SPRINT_CHANGELOG.md` with everything removed/fixed
