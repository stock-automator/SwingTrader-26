# Sprint Changelog

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
