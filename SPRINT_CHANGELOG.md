# Sprint Changelog

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
- `black`, `isort`, `bandit`, `safety`, `watchdog` (`requirements-test.txt`)
  — not invoked by `pytest.ini`, `.flake8`, CI, or any pre-commit config;
  `flake8` was kept since `.flake8` actively configures it.
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
