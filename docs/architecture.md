# Architecture

SwingTrader is two applications sharing one contract: a Python quant engine
that owns all trading logic, and two consumers of it — a full-history
backtest replay and a live latest-bar screener — both exposed over HTTP by
FastAPI, both rendered by one React frontend.

## Data flow

```
                    data/raw/<TICKER>.parquet  (yfinance cache, gitignored)
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
