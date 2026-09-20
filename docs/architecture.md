# Architecture

SwingTrader is two applications sharing one contract: a Python quant engine
that owns all trading logic, and two consumers of it — a full-history
backtest replay and a live latest-bar screener — both exposed over HTTP by
FastAPI, both rendered by one React frontend.

## Data flow

```
                    data/raw/<TICKER>.parquet  (yfinance cache; the initial
                                universe snapshot is tracked in git, new
                                tickers/refreshes beyond that are gitignored)
                                │
                                │  cache hit ──► use it
                                │  cache miss ─► live yfinance fetch (if ALLOW_DOWNLOADS)
                                ▼
                    backend/app/data/loader.py
                    (flatten columns, sort/dedupe index,
                     total-return split/dividend adjustment)
                                │
                                ▼
                    BaseStrategy.generate_signals(df)
                    backend/app/quant/strategies/*.py
                    ─► signal ∈ {1, 0, -1}, sl_type/sl_value, tp_type/tp_value
                                │
                                ▼
                    RiskManager.build_order(...)
                    backend/app/quant/risk.py
                    ─► Order(shares, entry_price, stop_loss, take_profit,
                             risk_amount, risk_per_share)
                                │
                ┌───────────────┴────────────────┐
                ▼                                  ▼
   quant/engine.py (run_backtest)          quant/setups.py (scan_universe)
   full-history replay via the              latest-bar-only: one Setup row
   `backtesting` library — fills,           per ticker (LONG/SHORT/EXIT_LONG/
   commission, stop/target mechanics        FLAT), regime-tagged via
   over the whole date range                quant/regime.py, ranked via
                │                            quant/screener.py
                ▼                                  │
   quant/backtest.py (run_comparison)              │
   ─► three aligned $1,000 dollar curves:          │
      strategy / buy_and_hold / spy                │
      (align_curves ∩ dates, rebase to              │
       one baseline) + relative_metrics             │
      (alpha, beta, Sharpe, information              │
       ratio, tracking error, R²)                    │
                │                                  │
                ▼                                  ▼
   backend/app/api/backtest.py              backend/app/api/screener.py
   POST /api/v1/backtest                    GET /api/v1/screener/live
                                             WS  /ws/screener
                │                                  │
                └───────────────┬──────────────────┘
                                ▼
                    backend/app/main.py (FastAPI app, CORS)
                                │
                                ▼
                    frontend/ (React + Vite + Tailwind +
                                lightweight-charts)
                    ─► Backtesting Studio renders equity_curves + summaries
                    ─► Live Screener renders the setup grid, polling
                       /ws/screener for pushes every WS_POLL_SECONDS
```

## Backend / frontend separation

The backend (`backend/app/`) owns every piece of trading logic — signal
generation, position sizing, execution simulation, benchmark comparison,
regime detection — and exposes it only as JSON over HTTP/WebSocket. It has
no notion of a UI. The frontend (`frontend/`) owns presentation only: it
never recomputes an equity curve, a Sharpe ratio, or a share count — those
numbers arrive pre-computed in the API response (`comparison_to_payload` in
`quant/backtest.py`, `ScanReport.as_dict` in `quant/setups.py`) and the
frontend just renders them. This mirrors the internal separation the quant
layer already enforces (see `AGENTS.md` §1: strategies don't know about
equity, `RiskManager` doesn't know about execution, engines don't know
about reporting) — the API boundary is just the outermost instance of the
same rule.

Concretely:

- `backend/app/quant/` — trading logic. Pure functions and dataclasses,
  no HTTP awareness, unit-tested directly (`tests/test_*.py`) without a
  running server.
- `backend/app/api/` — the HTTP boundary. Thin: routes validate input via
  Pydantic (`schemas.py`), call into `quant/`, and pass back a dict that's
  already JSON-shaped. `deps.py` holds request-time helpers
  (`load_frames`, `load_watchlist`) shared between the two route modules
  so they don't each grow their own ticker-loading logic.
- `frontend/src/` — React components, Tailwind styling, lightweight-charts
  chart instances. Talks to the backend exclusively through
  `fetch`/`WebSocket` calls to the URLs in `docs/api.md`.

## Where the WebSocket fits

`GET /api/v1/screener/live` and `WS /ws/screener` are two doors onto the
same function (`_scan` in `backend/app/api/screener.py`) — the WebSocket
doesn't carry a second implementation of the scan, it just re-runs the REST
logic on a timer (`settings.ws_poll_seconds`, default 15s) and pushes each
result as a JSON frame. A client that only needs one snapshot (e.g. a
server-rendered page, a cron job) should use the REST endpoint; the Live
Screener view in the frontend opens the WebSocket once on mount and updates
its setup grid in place on every push, rather than polling REST itself.
Scan errors (e.g. an unknown strategy name) are sent as an `{"error": ...}`
frame rather than closing the socket, so a single bad request doesn't force
the client to reconnect — see `docs/api.md` for the frame shapes.

## Why the backtest and the screener are separate engines

`quant/engine.py` needs the *entire* price history to replay fills bar by
bar and needs `backtesting`'s order-book simulation. `quant/setups.py`
only needs the strategy's opinion on the most recent bar, plus enough
lookback (`MIN_BARS = 100`) to warm up ATR/ADX — running the full
`backtesting` replay just to read the last row would be wasteful and would
force a screening request to wait on a full historical simulation. Both
engines call into the same `BaseStrategy` and `RiskManager`, so a strategy
change is picked up by both automatically; only the *use* of the signal
differs (open/close a simulated position vs. label a single row LONG/SHORT/
EXIT_LONG/FLAT).

## Beyond backtest/screener: the other subsystems

The diagram above is the original backtest/screener data-flow path; every
other API router (all shipped in Sprint 4/5, see `SPRINT_CHANGELOG.md`)
builds on the same `quant/`/`data/` layer without duplicating it. Full
request/response shapes for all of these are in `docs/api.md` — this is
the "how does it fit together" view, not the wire format.

| Subsystem | Backend module(s) | Router | Notes |
|---|---|---|---|
| Execution (Alpaca paper) | `execution/broker.py`, `execution/alpaca_client.py`, `execution/guards.py` | `api/execution.py` | Pre-trade session/earnings-lockout guards gate every order before it reaches Alpaca. `paper=True` always - no code path places a live order. |
| Order routing (broker-agnostic) | `execution/broker.py`'s `PaperBroker`/`AlpacaBroker` | `api/orders.py` | Decoupled from `api/execution.py`: persists to DuckDB's `routed_orders` (survives a restart) rather than in-process state. |
| Trade journal | `journal/executor.py` | `api/journal.py` | Thin read layer over a CSV-backed `TradeJournal` (`JOURNAL_PATH`). Logging entries/exits happens via the `TradeJournal` class directly, outside the API. |
| Alerts | `alerts/dispatcher.py`, `alerts/config_store.py` | `api/alerts.py` | Multi-channel fan-out (Telegram/Discord/generic webhook); channel config is JSON-backed and browser-editable, no `.env` edit/restart needed. |
| Market regime | `quant/regime.py`'s `MarketRegimeEngine` | `api/market.py` | SPY/QQQ EMA alignment + S&P 500 breadth + VIX bucket → one traffic-light state. Result is cached - see "Multi-threading & async patterns" below. |
| ATR position sizer | `quant/risk.py`'s `PositionSizer` | `api/risk.py` | Stateless preview endpoint; the same class backs the risk engine's own sizing, so the UI preview can't drift from what a real order would compute. |
| Universe sync | `data/universe.py`'s `UniverseManager` | `api/universe.py` | S&P 500 + Nasdaq-100 membership (not price data - that's `quant/data/parquet_manager.py`, a separate fetch path from `data/loader.py`). Persists to `config/universe.json`. |
| Background scans | `api/scans.py`, reuses `api/screener.py::_scan` | `api/scans.py` | Async, DuckDB-persisted version of the live screener - a job survives past the request/response cycle. See "DuckDB schema" below. |
| Live-feed WebSocket | `api/ws.py` | `api/ws.py` | Multiplexes regime + signal + order-update events over one socket - see "WebSocket events" below. |

## DuckDB schema & concurrency model

`data/scans.duckdb` (path overridable via `SCANS_DB_PATH`, used by tests to
point at a throwaway file) backs the background-scan and order-routing
subsystems. Schema is created idempotently in
`backend/app/db/session.py::_create_tables` (`CREATE TABLE IF NOT EXISTS` /
`CREATE INDEX IF NOT EXISTS`, safe to call on every connection open):

| Table | Columns | Primary key | Indexes | Written by | Read by |
|---|---|---|---|---|---|
| `scan_jobs` | `job_id, status, params (JSON), created_at, completed_at, error` | `job_id` | — | `api/scans.py::_create_job`/`_set_job_status` | `GET /api/v1/scans/{job_id}`, `GET /api/v1/scans` |
| `scan_results` | `job_id, ticker, strategy, direction, entry, stop, target, win_probability, r_multiple, trigger_reason, payload (JSON)` | none | `idx_scan_results_job_id` | `api/scans.py::_persist_results` | `GET /api/v1/scans/{job_id}` (filters by `job_id` on every poll) |
| `routed_orders` | `order_id, broker, ticker, side, order_type, qty, limit_price, status, fill_price, broker_order_id, error, submitted_at, updated_at` | `order_id` | `idx_routed_orders_status`, `idx_routed_orders_updated_at` | `api/orders.py::_insert_order`/`_mark_cancelled` | `GET /api/v1/orders/active` (filters by `status`), `WS /ws/v1/live-feed`'s poll loop (`list_orders_updated_since`, range-scans `updated_at`) |

`scan_results` has no primary key by design (one job produces many result
rows, no natural single-column uniqueness) - `idx_scan_results_job_id`
exists purely to accelerate the per-job-status-poll lookup, added in
Sprint 6 Phase 3 alongside the two `routed_orders` indexes above (before
that, `status`/`updated_at` filters were full table scans; `scan_jobs`/
`routed_orders`'s own primary keys already covered their `job_id`/
`order_id` lookups for free).

**Concurrency model**: DuckDB is single-process/embedded and doesn't allow
many concurrent read-write connections against the same file safely, so
the process keeps exactly one shared `duckdb.DuckDBPyConnection`, guarded
by a module-level `threading.Lock` (`db/session.py::get_connection`).
Every statement - reads and writes alike - takes the lock for the
duration of one fast INSERT/UPDATE/SELECT, never across the CPU-bound
strategy scan itself (that runs outside the lock, in a background thread -
see the next section). A background scan writing results therefore never
blocks an HTTP GET polling job status for more than a query's worth of
time, and the reverse holds too.

## WebSocket events

Two independent WebSocket routes, both following the same convention: push
one frame immediately on connect, then again every
`settings.ws_poll_seconds` (default 15s) until the client disconnects; a
scan/computation error is sent as an `{"type"/"error": ...}` frame instead
of closing the socket, so one bad poll tick doesn't force a reconnect.

- **`WS /ws/screener`** (`api/screener.py`) - single-purpose: pushes the
  same payload shape as `GET /api/v1/screener/live` on a timer. Pre-dates
  the unified feed below and is kept as-is for backward compatibility
  rather than folded into it.
- **`WS /ws/v1/live-feed`** (`api/ws.py`, Sprint 5) - multiplexes three
  event types over one socket rather than three separate connections, each
  frame tagged by a `"type"` key so the client (`frontend/src/hooks/
  useWebSocket.ts`) can route without three sockets:
  - `{"type": "regime", ...}` - a `MarketHealthReport`, same shape as
    `GET /api/v1/market/regime` (and the same cached value - see below).
  - `{"type": "signal", ...}` - one fresh screener scan, same shape as
    `GET /api/v1/screener/live`.
  - `{"type": "order_update", "orders": [...]}` - only `routed_orders`
    rows whose `updated_at` changed since the *previous* push
    (`list_orders_updated_since`), not a full resend every tick.
  - `{"type": "error", "detail": "..."}` - a scan error on one poll tick.

Full frame examples are in `docs/api.md`.

## Multi-threading & async execution patterns

Every blocking operation in the request path follows one of a small number
of established patterns - new code should reuse one of these rather than
inventing a new concurrency primitive:

1. **Bounded `ThreadPoolExecutor` fan-out**, for "do the same cheap-ish
   operation N times, wait for all of them." Three call sites, all sized
   off `settings.screener_max_workers` (default 16, env `SCREENER_MAX_WORKERS`):
   - `api/deps.py::load_frames` - concurrent per-ticker OHLCV loads
     (parquet read or yfinance fetch), the original instance of this
     pattern (Sprint 3/4).
   - `quant/regime.py::MarketRegimeEngine.compute_breadth` - per-ticker
     50-EMA check across the breadth universe.
   - `data/universe.py::UniverseManager.purge_broken_symbols` (Sprint 6
     Phase 3) - per-symbol parquet-existence/row-count check across the
     full S&P 500 + Nasdaq-100 union (500+ symbols); constructor takes its
     own `max_workers` rather than depending on the full `Settings` object,
     wired from `settings.screener_max_workers` at the API layer
     (`api/universe.py`) so it shares the one tuning knob rather than
     inventing a second.
2. **`run_in_threadpool`**, for "move one blocking call off the event loop
   inside an `async def` route" - unlike a plain `def` route (which
   Starlette auto-threadpools for free, e.g. `api/backtest.py`), an
   `async def` route runs directly on the event loop, so anything
   synchronous inside it must be explicitly offloaded. Used for:
   - CPU-bound backtest/analytics work (`api/analytics.py`'s Monte Carlo
     trade collection, walk-forward scans, factor-exposure comparison;
     `api/scans.py`'s background job body) - the original use of this
     pattern.
   - **As of Sprint 6 Phase 3**: `api/analytics.py`'s `load_frames`/
     `_load_benchmark_or_503` calls too - these are synchronous (parquet
     reads, a possible live yfinance fetch) and were previously called
     directly in the three `async def` routes, blocking the event loop for
     the full data-loading duration *before* ever reaching the
     already-threadpooled computation. Wrapping them closes that gap.
   - DuckDB reads/writes in `api/scans.py` and `api/orders.py` (each
     statement is fast, but still synchronous I/O on an `async def` route).
3. **`asyncio.to_thread`**, for a single one-off blocking call from
   `async def` code outside a route handler - `data/universe.py`'s
   `_run_sync_soon` (startup hook) and `api/universe.py`'s two routes,
   wrapping `UniverseManager.sync()` (network fetch + parquet scan).
   Functionally equivalent to `run_in_threadpool` for a single call;
   used here because there's no FastAPI `Depends`-injected settings object
   driving a `ThreadPoolExecutor` size at these call sites.
4. **Short-TTL in-process cache**, for "many independent readers, one
   expensive computation, staleness on the order of seconds is fine" -
   introduced in Sprint 6 Phase 3 for `api/market.py`'s regime report
   (`get_cached_regime_report`, 10s TTL, `threading.Lock`-guarded dict).
   Both `GET /api/v1/market/regime` and `WS /ws/v1/live-feed`'s poll loop
   call the same cached function, so concurrent/rapid callers (multiple
   dashboard tabs, the WS loop overlapping a REST poll) share one full
   breadth scan instead of each recomputing it independently - this also
   removed a previous duplication where `api/ws.py` carried its own copy
   of the same SPY/QQQ/VIX/breadth computation `api/market.py` already had.
   Test isolation: `market.reset_regime_cache_for_tests()` clears it
   between tests that monkeypatch fresh inputs per-test (mirrors
   `db/session.py::reset_for_tests`'s same "test-only reset hook" shape).
5. **DuckDB's single-lock-guarded connection** - see "DuckDB schema" above;
   not a thread pool, but the fourth concurrency primitive in the codebase
   and the one every `scan_jobs`/`scan_results`/`routed_orders` read or
   write goes through.
