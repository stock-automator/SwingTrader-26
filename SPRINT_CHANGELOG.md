# Sprint Changelog

## Sprint 6 Phase 3-4 — Performance, UI Resilience & Documentation (2026-09-20)

Branch: `feature/sprint-6-phase3-4-performance-ui`, stacked on
`feature/sprint-6.1-audit-cleanup` (PR #13). No new user-facing features —
this is a performance and resilience pass over the surface Sprint 6.1
audited, plus filling the documentation gaps that audit flagged but didn't
fix (`docs/architecture.md` only covered the backtest/screener path).

### Backend performance

- **`data/universe.py::UniverseManager.purge_broken_symbols`** — the
  500+-symbol per-ticker parquet-existence check (S&P 500 + Nasdaq-100
  union) now runs across a bounded `ThreadPoolExecutor` instead of a
  serial loop, matching `quant/regime.py::compute_breadth`'s existing
  concurrency pattern. Wired from `settings.screener_max_workers` at the
  API layer (`api/universe.py`) so it shares that one tuning knob.
- **`api/analytics.py`** — closed a real event-loop-blocking gap: all
  three routes here are `async def` (unlike `api/backtest.py`'s plain
  `def`, which Starlette auto-threadpools), so `load_frames`/
  `_load_benchmark_or_503` — both synchronous, a parquet read or a
  possible live yfinance fetch — were running directly on the event loop
  *before* the already-`run_in_threadpool`-wrapped backtest computation
  that followed them. Now wrapped in `run_in_threadpool` too.
- **DuckDB indexes** — added `idx_scan_results_job_id`,
  `idx_routed_orders_status`, `idx_routed_orders_updated_at`
  (`db/session.py`) on the hot filter columns that didn't already have one
  via a `PRIMARY KEY`. `scan_jobs`/`routed_orders` were already covered by
  their own PKs.
- **`GET /api/v1/market/regime` response cache** — 10-second TTL,
  `threading.Lock`-guarded (`api/market.py::get_cached_regime_report`).
  Shared with `WS /ws/v1/live-feed`'s poll loop, which previously carried
  its own independent copy of the same SPY/QQQ/VIX/breadth computation
  (`api/ws.py::_build_regime_event`) — now calls the same cached function,
  removing both the duplication and the redundant recomputation.
- **Global exception handler** (`main.py`) — an unhandled exception now
  returns a consistent `{"detail": "Internal server error..."}` 500
  instead of an unformatted error. Every route's own deliberate
  `HTTPException` handling (422/503/etc.) is untouched — this only catches
  what wasn't already turned into one.

### Frontend UI polish & resilience

- **`CloseAllModal`** now animates in/out (`framer-motion`
  `AnimatePresence`) instead of an instant pop — matches the transition
  treatment already used for tab switches and `OrderTicketDrawer`.
- **Loading-state spinners** — `ScreenerGrid`'s Rescan, `Dashboard`'s
  Refresh, and `BacktestStudio`'s Run Backtest buttons now show a spinning
  icon alongside the existing text swap, matching the icon+text pattern
  `PortfolioDashboard`'s own refresh button already had (previously the
  only place with one).
- **Touch targets** — `OrderTicketDrawer`'s and `Simulator`'s drawer close
  buttons bumped from an unlabeled ~28px hit area to 44px with an
  `aria-label` (mobile accessibility guideline minimum).
- **`ErrorBoundary`** (new, `components/common/ErrorBoundary.tsx`) — wraps
  every tab (and the always-mounted `ScreenerGrid`) in `App.tsx`, so a
  render error in one tab's subtree shows a graceful fallback instead of
  unmounting the whole app. No frontend unit-test runner exists in this
  repo (Playwright E2E + `tsc` + `oxlint` only — confirmed in the Sprint
  6.1 audit), so this ships without automated test coverage of its own;
  adding one wasn't judged worth a new test-framework dependency for a
  single, standard React pattern.
- **Mobile/LAN/Meshnet access** (`0.0.0.0` bindings, CORS private-network
  regex, viewport meta tag) — audited, already fully in place from Sprint
  5; nothing to add.

### Documentation

- **`docs/architecture.md`** — filled the gap the Sprint 6.1 audit flagged
  (only diagrammed the backtest/screener path): added sections covering
  every other subsystem (execution, order routing, journal, alerts,
  market regime, position sizer, universe sync, background scans,
  live-feed WS), the full DuckDB schema (tables, keys, the new indexes,
  the single-lock-connection concurrency model), both WebSocket routes'
  frame shapes, and a catalog of the four concurrency patterns used across
  the codebase (bounded `ThreadPoolExecutor`, `run_in_threadpool`,
  `asyncio.to_thread`, and the new short-TTL cache pattern).
- **`README.md`** — added two "Status" entries: a pointer to
  `SPRINT_CHANGELOG.md` as the canonical record starting at Sprint 4 (the
  prior numbered list predated it and had drifted out of sync), and this
  round's summary.

### Verification

- Backend: `pytest tests/` — **849 passed** (848 + one new test for the
  exception handler), 0 failed. `black --check`, `isort --check-only`,
  `flake8` — all clean.
- Frontend: `npm run build` (tsc + vite) clean; `npm run lint` (oxlint) —
  only the same pre-existing warning from Sprint 6.1; `npx playwright
  test` — **20 passed**, 0 failed (one selector updated in
  `simulator.spec.ts` after a close button's accessible name changed from
  "✕" to "Close simulate trade" — an intentional accessibility fix, not a
  regression).
- Code-reviewed via the `code-reviewer` subagent per `AGENTS.md`'s Code
  Review Workflow before merge.

## Sprint 6.1 — Audit, Dead-Code Cleanup & Documentation Sync (2026-09-20)

Branch: `feature/sprint-6.1-audit-cleanup`.

Phase 1 was a read-only audit of the full repository (backend, frontend,
docs, config); findings are recorded in `docs/SPRINT_6_AUDIT.md`. Phase 2
applied the surgical cleanup that audit called for. Headline finding: there
was no large body of dead code to remove — every route, strategy, and
frontend view is live and reachable. The real gaps were a documentation-drift
problem (14 undocumented endpoints, stale pre-refactor paths, two missing
views in both READMEs) and a handful of small, unambiguous cleanups.

### Removed

- `scikit-learn` dependency (`requirements.txt`) — unused, no `import
  sklearn` anywhere in the codebase.
- `bandit`, `safety`, `watchdog` (`requirements-test.txt`) — not invoked by
  `pytest.ini`, `.flake8`, `.github/workflows/`, or any pre-commit config.
  (`black` and `isort` were briefly removed too, then restored — see "CI
  fix" below; `flake8` was kept throughout since `.flake8` actively
  configures it.)
- `listScans()` and the now-unused `ListScansResponse` import
  (`frontend/src/lib/api.ts`) — defined, never called from any
  component/view/hook.
- `results/archive/` and `results/current/` — pre-refactor CLI-era output
  directories; confirmed nothing under `backend/app/` writes to either path
  anymore (the only current writer, `journal/executor.py`'s
  `export_summary`, targets `results/trade_journal_summary.json` directly).
  Both were already gitignored (`/results/`), so this is a local cleanup
  only and won't appear in the PR diff.

### Documentation fixes

- `docs/api.md` — added the 14 endpoints across 8 routers that had zero
  coverage despite being live since Sprint 4/5: order-ticket, data-sync
  (×2), scans (×3), universe (×2), market/regime, position-sizer, orders
  (×3), and the live-feed WebSocket.
- `README.md` / `frontend/README.md` — both "views" sections listed only 6
  of the app's 8 tabs; added **Dashboard** and **Historical Simulator**
  (shipped Sprint 4, PR #10) to both, including the nav-overview screenshot
  alt text and a "six tabs" → "eight tabs" correction in `README.md`.
- `AGENTS.md` — fixed three literal, copy-pasteable commands/imports still
  pointing at the pre-refactor `src/` layout that its own path-translation
  table (added in a prior session) didn't cover: the "Adding a New
  Strategy" worked example (`pytest --cov=src`, `from src.core.risk import
  RiskManager`) and the Code Review Workflow's `git status --porcelain
  src/ tests/`. The broader doc-wide `src/` → `backend/app/` rewrite
  remains an explicitly-deferred fast-follow (per the doc's own header),
  not done here.
- `docs/architecture.md` — corrected the claim that `data/raw/*.parquet` is
  entirely gitignored; the initial universe snapshot (502 files, 74MB) is
  actually tracked in git, with `/data/` in `.gitignore` only affecting
  files added since.
- `README.md` — updated the stale "687 passing" test-count badge and prose
  to the current count (848).

### Verification

- Backend: `pytest tests/` — **848 passed**, 0 failed.
- Frontend: `npm run build` (tsc + vite) clean; `npm run lint` (oxlint) —
  only a pre-existing warning, unrelated to this change; `npx playwright
  test` — **20 passed**, 0 failed.
- No test or business logic was modified — Phase 2 only removed confirmed-
  dead code/dependencies and fixed documentation; the full suite was
  already green before and after.

### CI fix (post-review correction, same PR)

The first push of this branch broke GitHub Actions CI, caught by the repo
owner reviewing the pipeline run rather than by this sprint's own
verification (which only ran suites locally):

- **`lint` job failure** — `black: command not found`. Cause: the initial
  cleanup pass removed `black`/`isort` from `requirements-test.txt` on the
  premise that nothing invoked them; that check missed
  `.github/workflows/ci.yml`, whose `lint` job runs `black --check backend/
  tests/` and `isort --check-only backend/ tests/` directly. Both are
  restored. (The codebase was already fully `black`/`isort`-compliant, so
  restoring the checks doesn't surface any new formatting failures.)
- **`test` job failure (both Python 3.11 and 3.12 matrix legs)** — pytest
  collection errored on every `test_*_api.py` file:
  `starlette.testclient` requires an HTTP client package to be installed,
  and it isn't listed in either requirements file. This is a **pre-existing
  gap predating this sprint** (confirmed via `git show main:requirements.txt`
  — never listed on `main` either); it was masked locally because
  developer machines already had it installed from some other project, and
  only surfaces on CI's clean install. `httpx` (the real package
  `starlette.testclient` depends on — the CI log's `pip install httpx2`
  is not a real published package, so it was not installed verbatim) is
  now in `requirements-test.txt`, since it's needed only by
  `tests/test_*_api.py`'s `TestClient` usage, not by `backend/app/` itself.
- `docs/SPRINT_6_AUDIT.md` §5 and its Phase 2 checklist item are annotated
  with both corrections.

### Deferred (flagged in `docs/SPRINT_6_AUDIT.md`, out of scope for this
### surgical pass)

- `data/universe.py::sync_universe` does fully sequential per-ticker
  fetches — a real parallelization opportunity, but a feature-level change.
- Two independent yfinance fetch paths (`data/loader.py` vs
  `quant/data/parquet_manager.py`) have duplicated retry/adjustment logic
  that will drift over time; worth consolidating in a future sprint.
- `frontend/e2e/backtest.spec.ts` doesn't exist — Backtesting Studio's
  form-submit → results flow has no dedicated E2E spec, only the shallow
  tab-renders check in `smoke.spec.ts`. Not required for the 100%-pass goal
  (nothing is failing), but the clearest coverage gap for a future sprint.

## Sprint 5 — Execution, Risk & Regime (2026-09-19)

Branch: `feature/sprint-5-execution-risk-regime` (worktree at
`../feature-sprint-5-execution-risk-regime`).

### Core Features Added

- **Top-Down Market Regime & Breadth Filter** — `quant.regime.MarketRegimeEngine`
  combines SPY/QQQ 50/200-EMA alignment, S&P 500 breadth (% of the parquet-cached
  universe above its own 50-EMA, scored in a bounded `ThreadPoolExecutor`), and a
  VIX volatility bucket (LOW/NORMAL/HIGH/EXTREME) into one `BULL_CONFIRMED` /
  `CAUTION_CHOP` / `BEAR_DEFENSIVE` state. Exposed via `GET /api/v1/market/regime`;
  surfaced on the Dashboard as a "Market Traffic Light" badge
  (`components/common/MarketRegimeBadge.tsx`) with a metrics tooltip.
- **ATR Position Sizer & Chandelier Exit Trailing Stop** — `quant.risk.PositionSizer`
  (`shares = capital * risk% / (ATR * multiplier)`) and `ChandelierExitStop`
  (rolling-extreme ATR trailing stop with a one-directional `ratchet`). Wired into
  the Order Ticket Drawer as a live sizing preview (`POST /api/v1/position-sizer/preview`)
  with 0.5% / 1.0% / 2.0% risk selectors.
- **Live Broker / Paper Order Routing Engine** — `execution.broker.ExecutionBroker`
  interface with `PaperBroker` (simulated spread-haircut fills, no network) and
  `AlpacaBroker` (wraps the existing `AlpacaExecutionClient`) adapters. New
  broker-agnostic endpoints: `POST /api/v1/orders/submit`,
  `POST /api/v1/orders/cancel/{id}`, `GET /api/v1/orders/active`, persisted to a new
  `routed_orders` DuckDB table (thread-safe via the existing shared-connection lock).
- **Real-Time WebSocket Streaming Alert Feed** — `WS /ws/v1/live-feed` multiplexes
  regime updates, one live screener scan, and order fill/cancel notifications over
  one socket. Frontend `useWebSocket` hook auto-reconnects with exponential backoff;
  `LiveFeedListener` turns arriving events into toast notifications (new setups,
  order fills/rejections, regime state changes).

### Architecture Updates

- **Loader/dataframe robustness**: `data.loader.flatten_columns` now dedupes
  duplicate-labeled columns from a malformed delisted/aliased-ticker frame before
  column selection (previously could raise `ValueError: Columns must be same
  length as key`); `api.deps._load_one` now catches any per-ticker exception (not
  only `DataUnavailableError`) so one bad symbol can't abort a whole
  `ThreadPoolExecutor` batch scan.
- **Fixed a latent loader contract bug**: `data.loader.fetch_yfinance` was
  silently falling back to yfinance's own `period="1mo"` default whenever no
  `start` date was given, contradicting its documented "`None` = all available
  history" contract — this is what was returning ~22 bars for SPY/QQQ/VIX and
  breaking the new regime engine's 200-EMA alignment. Now passes `period="max"`
  explicitly when `start` is `None`.
- **Universe sync 403 fix**: `data.universe.UniverseManager` now fetches the
  Wikipedia S&P 500/Nasdaq-100 constituent pages itself with a realistic browser
  `User-Agent` header before handing the HTML to `pd.read_html`, instead of
  letting `read_html` hit the URL directly with no headers.
- **Mobile / NordVPN Meshnet network binding**: `vite.config.ts` binds
  `server.host = "0.0.0.0"`; backend CORS gained `Settings.cors_origin_regex`
  (`192.168.*`, `10.*`, `100.*` on any port) via `CORSMiddleware.allow_origin_regex`,
  so a phone on the same LAN/Meshnet can reach both dev servers without
  hardcoding a machine's current IP.
- **Dependency integrity confirmed**: `@tanstack/react-query`, `lucide-react`,
  `framer-motion` were already declared in `frontend/package.json`; `npm install`
  run clean with 0 vulnerabilities.
- Multi-threaded batch ingestion (`api.deps.load_frames`, `MarketRegimeEngine.
  compute_breadth`) and DuckDB-backed persistence (`scan_jobs`/`scan_results`/
  `routed_orders`) were already established in prior sprints and reused as-is
  rather than duplicated.

### Verification Logs

- `pytest tests/` — **846 passed**, 0 failed (up from a 741-test baseline;
  105 new tests added this sprint: loader/deps hardening, universe headers,
  `MarketRegimeEngine`, `PositionSizer`/`ChandelierExitStop`, execution broker
  adapters, `AlpacaExecutionClient.cancel_order`, orders API, live-feed WS
  framing, CORS regex).
- `flake8 backend/ tests/` — **0 errors**.
- `npm run build` (`tsc -b && vite build`) — **0 TypeScript errors**.
- `npm run lint` (oxlint) — 0 errors, 1 pre-existing warning (unrelated to this
  sprint's changes).
- `npx playwright test` — **20/20 passed** (18 pre-existing + 2 new specs
  covering the Market Traffic Light badge and the ATR position sizer preview).

### Next Sprint Focus (Sprint 6 Roadmap)

- Wire the Order Routing Engine into the frontend (an actual order-submission UI
  against `POST /api/v1/orders/submit`, not just the sizing preview).
- Persist and surface `ChandelierExitStop` as a live trailing-stop overlay on
  open positions, not just as a standalone computation.
- Push setup-trigger and order-fill events from the live-feed WS into the
  DuckDB scan/order tables proactively (event-driven) rather than the current
  poll-and-diff model, once a message-queue layer is in place.
- Replace the Wikipedia-scrape universe source with a maintained constituents
  API/dataset now that a second scraping fragility class (403s) has surfaced.

## Sprint 7 — Deploy Workflow GHCR Fix (2026-09-20)

Branch: `sprint-7/deploy-workflow-fix` → PR #15 → merged to main as `25749bd`.

### Core Change

**Fix GHCR image naming in `.github/workflows/deploy.yml`**

- Changed `BACKEND_IMAGE` and `FRONTEND_IMAGE` from `${{ github.repository }}-backend/frontend`
  to `${{ github.repository_owner | lower }}/${{ github.event.repository.name | lower }}-backend/frontend`
- **Why:** `github.repository` preserves case (e.g., `SwingTrader-26`) but GHCR stores
  images under lowercase names. This caused all previous deploy workflow runs to fail.
- **Impact:** Future deploy workflow runs will correctly push to GHCR.

### Added Documentation

- `docs/agent/CODE_REVIEW_SPRINT_7.md` — Independent code review report
- `docs/agent/QE_SPRINT_7.md` — Independent QE validation report

### Validation

- YAML syntax: valid ✓
- Local pytest: 856 tests passing ✓
- Lint: black/isort/flake8 ✓
- CI (PR #15): lint ✓, frontend ✓, test (3.11) ✓, test (3.12) ✓
- Code Review: APPROVED ✓
- QE: PASSED ✓

### Deferred

None — minimal configuration change.
