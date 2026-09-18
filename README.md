# SwingTrader

A quantitative swing/momentum trading research platform: a FastAPI backend
runs systematic strategies through a shared risk engine and answers one
question precisely — **if you'd put $1,000 into this strategy, what would
you have now, versus buying and holding the same ticker, versus SPY?** — and
a React terminal-style frontend surfaces that backtest plus a live setup
screener over a stock watchlist.

This repo just went through a full-stack refactor: the original
Streamlit/CLI research tool (`src/`) has been rebuilt as a proper `backend/`
FastAPI service, with a new `frontend/` React + Vite + Tailwind +
lightweight-charts app on top of it.

---

## What's here

- **`backend/app/quant/`** — the strategy/risk/backtest engine. Strategies
  emit direction + stop/target intent, `RiskManager` turns that into sized
  orders, `quant/engine.py` replays them bar-by-bar, and
  `quant/backtest.py` compares the resulting equity curve to buy-and-hold
  and SPY on a shared $1,000 baseline. `quant/setups.py` runs the same
  strategies live for the screener instead of over history.
- **`backend/app/api/`** — the FastAPI routes (`/api/v1/backtest`,
  `/api/v1/screener/live`, `/ws/screener`, `/api/v1/health`) that expose all
  of the above to the frontend.
- **`frontend/`** — a React + Vite + Tailwind terminal UI (lightweight-charts
  for the equity curves), maintained separately.
- **`docs/`** — architecture, API reference, and the benchmark math, in
  depth. See below for the map.

For the underlying engineering contracts — the exact strategy signal
schema, how `RiskManager` resolves stop/target types, how to add a new
strategy — see **[`AGENTS.md`](AGENTS.md)**. That document predates this
refactor and still refers to the old `src/` paths; the same contracts now
live under `backend/app/quant/`, `backend/app/data/`, `backend/app/journal/`
and `backend/app/analytics/`. This README does not duplicate that material.

---

## Architecture at a glance

```
data/raw/*.parquet, yfinance  ──►  BaseStrategy.generate_signals
                                          │
                                          ▼
                                   RiskManager.build_order
                                          │
                              ┌───────────┴───────────┐
                              ▼                        ▼
                   quant/engine.py (backtest)   quant/setups.py (live screener)
                              │                        │
                              ▼                        │
                   quant/backtest.py                   │
                   ($1,000 vs buy&hold vs SPY)          │
                              │                        │
                              └───────────┬────────────┘
                                          ▼
                                   backend/app/api/*
                              (FastAPI REST + WebSocket)
                                          │
                                          ▼
                                   frontend/ (React)
```

See **[`docs/architecture.md`](docs/architecture.md)** for the full data-flow
diagram, including where `/ws/screener` fits.

---

## Quickstart

### Backend

```bash
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
```

The API comes up on `http://localhost:8000`. Interactive docs (Swagger UI)
are auto-generated at `http://localhost:8000/docs`.

Relevant environment variables (see `backend/app/config.py`):

| Variable | Default | Purpose |
|---|---|---|
| `FINNHUB_API_KEY` | unset | Enables Finnhub real-time quotes on the live screener; unset falls back to yfinance-only |
| `DATA_DIR` | `data/raw` | Parquet cache directory |
| `ALLOW_DOWNLOADS` | `true` | Whether the API may hit a live data provider for an uncached ticker (off in CI) |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated browser origins allowed to call the API |
| `WATCHLIST_PATH` | `config/watchlist.txt` | Newline-delimited ticker list the screener scans |
| `SCREENER_MAX_TICKERS` | `60` | Hard cap on tickers scanned per screener call |
| `WS_POLL_SECONDS` | `15.0` | Interval between `/ws/screener` pushes |
| `MAX_BACKTEST_TICKERS` | `10` | Cap on tickers per backtest request |

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Comes up on `http://localhost:5173` and talks to the backend at
`http://localhost:8000`.

### Tests

```bash
pytest tests/ -v
```

---

## The API, briefly

Full reference with request/response shapes and curl examples:
**[`docs/api.md`](docs/api.md)**.

- **`POST /api/v1/backtest`** — run a strategy over one or more tickers and
  get back three aligned dollar equity curves (strategy, buy & hold, SPY),
  their summary stats, and alpha/beta/Sharpe/information-ratio relative to
  each benchmark. See **[`docs/benchmark_math.md`](docs/benchmark_math.md)**
  for exactly how those numbers are computed.
- **`GET /api/v1/screener/live`** — the latest-bar setup (LONG / SHORT /
  EXIT_LONG / FLAT) for every ticker in the watchlist, sized against a
  notional account.
- **`WS /ws/screener`** — the same scan, pushed on an interval, for the live
  dashboard.
- **`GET /api/v1/health`** — liveness + which optional data providers are
  configured.

---

## Strategies, risk, and backtesting

A strategy only looks at price history and returns direction + relative
stop/target sizing; `RiskManager` is the only thing that knows about account
equity and converts that into shares and absolute prices; the engines
(`quant/engine.py` for full-history replay, `quant/setups.py` for the live
screener) are the only things that simulate execution. This separation, the
exact strategy output schema, and the steps for adding a new strategy are
documented in **[`AGENTS.md`](AGENTS.md)** — read that before writing a new
strategy, this README won't repeat it.

---

## Project layout

```text
backend/
  app/
    main.py              FastAPI app, CORS, health check
    config.py             Environment-driven Settings
    api/                  Routes: backtest.py, screener.py, schemas.py, deps.py
    quant/                Strategy engine: strategies/, engine.py, risk.py,
                           regime.py, indicators.py, setups.py, screener.py,
                           backtest.py (the $1,000 benchmark), metrics.py
    data/                 Price loading/caching (loader.py, agent.py)
    journal/              Trade journal persistence
    analytics/             Console/report rendering
frontend/
  src/                    React + Vite + Tailwind + lightweight-charts UI
docs/
  architecture.md          Data-flow diagram, backend/frontend separation
  api.md                   Full endpoint reference
  benchmark_math.md         Alpha/beta/Sharpe/information-ratio, explained
  media/                    Demo video recordings
scripts/
  generate_demo_videos.py  Playwright walkthrough recorder
tests/                     pytest suite, one file per backend/app module
config/
  watchlist.txt             Ticker universe the screener scans
data/raw/                  Cached OHLCV parquet files (gitignored)
AGENTS.md                  Engineering contract for strategies/risk/engines
```

---

## Status

The project began as a Yahoo Finance historical-data pipeline and evolved
into a systematic momentum/swing-trading research platform: a stock
universe of 500+ tickers cached locally as Parquet, a modular strategy
framework, a risk manager, full-history and live-screener engines, and now
a FastAPI + React application on top of all of it. The target account size
for eventual live deployment is small (low four figures); risk management
takes priority over raw historical return, and a backtest is treated as
evidence for further investigation, not proof of anything.
