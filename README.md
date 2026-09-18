<div align="center">

# SwingTrader

**A quantitative swing/momentum trading research platform.**

Answers one question precisely: *if you put $1,000 into this strategy, what
would you have now — versus buying and holding the same stock, versus the
S&P 500?*

[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)]()
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009485)]()
[![React](https://img.shields.io/badge/frontend-React%20%2B%20Vite-61DAFB)]()
[![Tests](https://img.shields.io/badge/tests-239%20passing-brightgreen)]()

[What is this?](#what-is-this) •
[Quickstart](#quickstart-for-first-time-users) •
[Do I need an API key?](#do-i-need-an-api-key) •
[Configuration](#configuration) •
[Hosting](#hosting-it-somewhere-else) •
[Docs](#learn-more)

</div>

---

## What is this?

SwingTrader is two things working together:

1. A **backend** (Python + FastAPI) that runs systematic trading strategies
   through a shared risk engine, then answers "what would $1,000 have
   turned into?" by comparing the strategy's results against **buying and
   holding the stock** and against **the S&P 500 (SPY)** — same starting
   dollar amount, same dates, so the comparison is fair.
2. A **frontend** (React) — a dark, terminal-style web app with two screens:
   a **Live Screener** that scans a watchlist for buy/sell setups, and a
   **Backtesting Studio** where you pick a strategy, a ticker, and a date
   range, and get back charts and numbers.

You do not need to know how to trade, or write any code, to run this and
click around it. The sections below assume you've never set up a project
like this before.

---

## Quickstart (for first-time users)

### What you need installed first

| Tool | Version | Check you have it | Don't have it? |
|---|---|---|---|
| **Python** | 3.11 or 3.12 | `python3 --version` | [python.org/downloads](https://www.python.org/downloads/) |
| **Node.js** | 20 or newer | `node --version` | [nodejs.org](https://nodejs.org/) (pick the "LTS" version) |
| **Git** | any recent version | `git --version` | [git-scm.com](https://git-scm.com/downloads) |

Run each `Check you have it` command in a terminal. If it prints a version
number, you're set — if it says "command not found," install that tool
first.

> **Tip:** on macOS/Linux, "terminal" means the Terminal app. On Windows, use
> PowerShell, or [Windows Terminal](https://apps.microsoft.com/detail/9n0dx20hk701).

### Do I need an API key?

**No.** SwingTrader works immediately with no signup and no API key —
price data comes from Yahoo Finance (via the free `yfinance` library),
which needs no account.

There's exactly **one optional** upgrade: a free [Finnhub](https://finnhub.io/register)
API key adds real-time quotes to the live screener. Skip it for now — you
can always add it later. Everything in this guide works without it.

### Step 1 — Get the code

```bash
git clone https://github.com/stock-automator/SwingTrader-26.git
cd SwingTrader-26
```

(Already have it? `cd` into the folder you cloned it to instead.)

### Step 2 — Start the backend

A **virtual environment** keeps this project's Python packages separate
from everything else on your computer — it's the standard, safe way to do
this, not an extra hoop.

```bash
python3 -m venv venv              # create it (only needed once)
source venv/bin/activate           # macOS/Linux
# venv\Scripts\activate             # Windows (PowerShell/CMD instead)

pip install -r requirements.txt    # install everything the backend needs
uvicorn backend.app.main:app --reload
```

Leave this terminal window running. You should see something like:

```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

That means the backend is live. Visit **http://localhost:8000/docs** in a
browser — you should see an interactive API page. If you do, the backend
works.

### Step 3 — Start the frontend

Open a **second** terminal window (leave the first one running) and:

```bash
cd SwingTrader-26/frontend   # from the repo root
npm install                   # only needed the first time
npm run dev
```

You'll see:

```
  VITE ready
  ➜  Local:   http://localhost:5173/
```

### Step 4 — Open it

Go to **http://localhost:5173** in your browser. You should land on the
**Live Screener** tab with a "API online" indicator in the top right — that
green dot means the frontend successfully reached the backend from Step 2.

> **Nothing showing up on the screener?** That's normal on a totally fresh
> checkout with no cached price data yet — the first scan has to fetch bars
> from Yahoo Finance, which takes a few seconds per ticker. Give it a
> minute, or try the Backtesting Studio next (below) with a well-known
> ticker like `AAPL`, which fetches faster since it's a single symbol.

### Try it: run your first backtest

1. Click **Backtesting Studio** in the top nav.
2. Leave **Strategy** as `Donchian 20-day Breakout` and **Tickers** as `AAPL`.
3. Set **Initial Capital** to `1000` (it already defaults to this).
4. Click **Run Backtest**.

After a few seconds you'll see a headline like *"$1,000 grown to $1,138 vs
$1,070 buying & holding AAPL"*, a chart comparing three lines (your
strategy, buy & hold, and SPY), and a table of every trade the strategy
would have made. That's the whole platform in one click.

### Running the tests (optional, for the curious)

```bash
pip install -r requirements-test.txt
pytest tests/ -v
```

You should see `239 passed`. This isn't required to use the app — it's how
you'd confirm nothing is broken if you change any code.

---

## Configuration

Everything below is **optional** — the app runs correctly with none of it
set. To customize any of it, copy `.env.example` to a new file named `.env`
in the repo root and edit the values there; the backend reads it
automatically on startup.

```bash
cp .env.example .env
```

| Variable | Default | What it does |
|---|---|---|
| `FINNHUB_API_KEY` | *(unset)* | Adds real-time quotes to the live screener. Get a free one at [finnhub.io/register](https://finnhub.io/register). Without it, the screener uses Yahoo Finance data instead — fully functional, just not tick-by-tick. |
| `ALLOW_DOWNLOADS` | `true` | Whether the backend may fetch a ticker it doesn't have cached. Set to `false` to run fully offline against only what's already downloaded. |
| `DATA_DIR` | `data/raw` | Folder where downloaded price data is cached, so repeat requests don't re-download. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Which frontend URLs the backend will accept requests from. Only change this if you're hosting the frontend somewhere other than your own machine. |
| `WATCHLIST_PATH` | `config/watchlist.txt` | The list of tickers the live screener scans (one per line — already comes with ~500 S&P 500-ish names). |
| `SCREENER_MAX_TICKERS` | `750` | Caps how many tickers one screener scan checks — a safety valve against an accidentally enormous watchlist, not a throttle (loading is parallelized, see below). |
| `SCREENER_MAX_WORKERS` | `16` | Thread-pool size used to fetch tickers concurrently; also the de facto rate limit against the data provider. |
| `SCREENER_MIN_AVG_VOLUME` | `100000` | Tickers below this trailing average daily volume are skipped as `VOLUME_FILTER_FAILED` rather than scanned. |
| `SCREENER_VOLUME_LOOKBACK` | `20` | Trailing bar count `SCREENER_MIN_AVG_VOLUME` is averaged over. |
| `SCREENER_STALE_AFTER_DAYS` | *(unset)* | Skip a ticker as `DATA_STALE` if its most recent cached bar is older than this many days. Unset disables the check. |
| `WS_POLL_SECONDS` | `15` | How often the live screener pushes a fresh scan over its live connection. |
| `MAX_BACKTEST_TICKERS` | `10` | Caps how many tickers one backtest request can run at once. |
| `JOURNAL_PATH` | `data/trades_live.csv` | Where the trade journal (entries/exits/MAE-MFE/decay analytics) reads and writes its CSV. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | *(unset)* | Telegram alert channel for `POST /api/v1/alerts/dispatch`; both must be set to activate it. |
| `DISCORD_WEBHOOK_URL` | *(unset)* | Discord alert channel (incoming webhook). |
| `GENERIC_WEBHOOK_URL` | *(unset)* | Generic HTTP alert channel — setups are POSTed here as flat JSON. |
| `ALPACA_API_KEY` / `ALPACA_API_SECRET` | *(unset)* | Alpaca **paper-trading** credentials for `/api/v1/execution/*`. The SDK always points at Alpaca's paper endpoint regardless of these values — there is no setting that makes this platform place live orders. |

> **Never commit a real `.env` file.** It's already excluded via
> `.gitignore` — only `.env.example` (with no real keys in it) is tracked.

---

## Hosting it somewhere else

The instructions above run everything on your own laptop, which is the
right starting point. If you want a "start it with one command" local setup
instead of two separate terminals, or you're ready to put it somewhere
other people can reach:

### Option A — One command, still on your own machine (Docker)

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/),
then from the repo root:

```bash
docker compose up --build
```

Open **http://localhost:8080**. This builds and runs both the backend and
frontend in containers — no Python or Node setup needed at all. Stop it
with `Ctrl+C`, or `docker compose down`.

### Option B — A real deployment (cloud hosting)

This repo's CI automatically builds a container image of the backend and
one of the frontend on every push to `main` and publishes them to GitHub
Container Registry (see `.github/workflows/deploy.yml`) — that's the
artifact you'd hand to a hosting provider (a VPS, Fly.io, Render, AWS/GCP/
Azure, etc.). There's no live, publicly-hosted instance of this app today;
wiring one up means picking a host, pointing it at those published images,
and setting `FINNHUB_API_KEY`/`CORS_ORIGINS` for that environment. That's a
deliberate, separate decision this repo doesn't make for you — nothing here
requires paid infrastructure to *use* it, only to make it reachable by
someone other than you.

---

## Architecture, for the curious

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

See **[`docs/architecture.md`](docs/architecture.md)** for the full
walkthrough, including where the live `/ws/screener` connection fits.

A strategy only looks at price history and returns direction + relative
stop/target sizing; `RiskManager` is the only thing that knows about account
equity and converts that into shares and absolute prices; the engines
(`quant/engine.py` for full-history replay, `quant/setups.py` for the live
screener) are the only things that simulate execution. This separation, the
exact strategy output schema, and the steps for adding a new strategy are
documented in **[`AGENTS.md`](AGENTS.md)** — note that document predates this
refactor and still refers to the code's old `src/` location; the same
contracts now live under `backend/app/quant/`, `backend/app/data/`,
`backend/app/journal/` and `backend/app/analytics/`.

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
- **`GET /api/v1/signals/live-today`** — the same idea across *every*
  registered strategy at once, flattened into one cross-strategy "what to
  look at today" grid (the frontend's Signal Matrix tab).
- **`POST /api/v1/backtest/historical-date-scan`** — the live screener's
  logic, but frozen at a `target_date` in the past with the frame sliced to
  `<= target_date` first, so it's structurally impossible for it to see data
  that wasn't available yet (zero lookahead bias).
- **`POST /api/v1/backtest/simulate-trade-execution`** — prices a single
  simulated fill (`NEXT_OPEN` or `SAME_CLOSE_SLIPPAGE`) with configurable
  slippage/commission/fee, for sanity-checking a signal's realistic entry.
- **`POST /api/v1/alerts/dispatch`** — sends a message to whichever of
  Telegram / Discord / a generic webhook are configured; on-demand only,
  nothing auto-fires from a scan.
- **`POST /api/v1/execution/orders`**, **`/close-all`**, **`GET
  /api/v1/execution/account`** — Alpaca **paper-trading** order placement
  (market/limit/bracket) and an emergency flatten-everything switch. 503 if
  no Alpaca credentials are configured.
- **`GET /api/v1/journal/summary`**, **`/decay`**, **`POST
  /api/v1/journal/mae-mfe`** — trade-journal analytics: win rate/expectancy,
  30/60/90-day performance decay, and per-trade max adverse/favorable
  excursion.
- **`GET /api/v1/data/sync/status`** / **`POST /api/v1/data/sync`** — kicks
  off (and reports progress on) a background Parquet cache refresh; the
  frontend's header sync banner polls this.
- **`GET /api/v1/health`** — liveness + which optional data providers are
  configured.

## Project layout

```text
backend/
  app/
    main.py              FastAPI app, CORS, health check
    config.py             Environment-driven Settings (.env aware)
    api/                  Routes: backtest.py, screener.py, signals.py,
                           replay.py, alerts.py, execution.py, journal.py,
                           data_sync.py, schemas.py, deps.py
    quant/                Strategy engine: strategies/ (10 registered),
                           engine.py, risk.py, regime.py, indicators.py,
                           setups.py, screener.py, backtest.py (the $1,000
                           benchmark), metrics.py
    alerts/                Telegram/Discord/webhook dispatch (dispatcher.py)
    execution/             Alpaca paper-trading client wrapper
    data/                 Price loading/caching (loader.py, agent.py)
    journal/              Trade journal persistence + decay/MAE-MFE analytics
    analytics/             Console/report rendering
frontend/
  src/                    React + Vite + Tailwind + lightweight-charts UI
                           (Signal Matrix grid, sync status banner, toasts,
                           Framer Motion tab transitions)
  e2e/                    Playwright smoke test
docs/
  architecture.md          Data-flow diagram, backend/frontend separation
  api.md                   Full endpoint reference
  benchmark_math.md         Alpha/beta/Sharpe/information-ratio, explained
  code_review.md            Multi-persona review of this codebase
  media/                    Demo video recordings
scripts/
  generate_demo_videos.py  Playwright walkthrough recorder
tests/                     pytest suite, one file per backend/app module
config/
  watchlist.txt             Ticker universe the screener scans
data/raw/                  Cached OHLCV parquet files
docker-compose.yml          One-command local run via Docker
AGENTS.md                  Engineering contract for strategies/risk/engines
```

## Learn more

- **[`docs/architecture.md`](docs/architecture.md)** — full data-flow diagram
- **[`docs/api.md`](docs/api.md)** — every endpoint, request/response shapes, curl examples
- **[`docs/benchmark_math.md`](docs/benchmark_math.md)** — exactly how alpha/beta/Sharpe are computed
- **[`docs/code_review.md`](docs/code_review.md)** — a multi-persona review of this codebase
- **[`AGENTS.md`](AGENTS.md)** — the engineering contract for strategies, risk sizing, and the backtest/screener engines

## Status

The project began as a Yahoo Finance historical-data pipeline and evolved
into a systematic momentum/swing-trading research platform: a stock
universe of 500+ tickers cached locally as Parquet, a modular strategy
framework, a risk manager, full-history and live-screener engines, and now
a FastAPI + React application on top of all of it. The target account size
for eventual live deployment is small (low four figures); risk management
takes priority over raw historical return, and a backtest is treated as
evidence for further investigation, not proof of anything.

**Where this stands today**, roughly in the order it was built:

1. Historical data pipeline, modular strategy framework, risk manager,
   backtest/forward-test engines, analytics (original CLI-era foundation).
2. Risk engine & position sizing, market-regime detection, first strategy
   set (`donchian_breakout`, `moving_average_cross`, `vcp_breakout`,
   `relative_strength`).
3. Full-stack refactor onto FastAPI + React, the $1,000-vs-buy&hold-vs-SPY
   benchmark backtester, and the live setup screener (REST + WebSocket).
4. **Scanner reliability + strategy library expansion**: the live screener
   silently truncated a full watchlist to 60 tickers and loaded them one at
   a time; fixed with a raised, transparent cap, concurrent thread-pooled
   loading with retry/backoff, and explicit skip-reason categorization
   (`INSUFFICIENT_HISTORY` / `DATA_STALE` / `VOLUME_FILTER_FAILED` /
   `ZERO_LIQUIDITY`). Strategy count went from 4 to 10, adding volatility
   (`bollinger_keltner_squeeze`, `kama_trend`), mean-reversion
   (`zscore_mean_reversion`, Hurst-filtered), momentum
   (`supertrend_psar`, `dual_momentum`), and market-structure
   (`obv_divergence`) coverage.
5. **This round** — cross-strategy signal matrix, point-in-time historical
   replay (zero lookahead by construction), trade execution simulation,
   multichannel alerts (Telegram/Discord/generic webhook), Alpaca
   paper-trading order placement + emergency close-all, trade-journal decay
   analytics (MAE/MFE, 30/60/90-day win-rate/expectancy windows), and the
   matching frontend surface: a real-time data-sync banner, the Signal
   Matrix grid (sortable/filterable, one-click paper-trade action), toast
   notifications, and animated tab transitions.

**Deliberately not done yet** (so the next session doesn't have to rediscover
this by reading code):

- `win_probability` on the signal matrix is hard-coded `null` everywhere —
  no model-backed estimate exists, and a fabricated number would be
  actively misleading for a real trading decision.
- No UI yet for configuring alert channels, viewing the Alpaca account /
  positions, or the journal's decay analytics — the backend endpoints exist
  and are tested, the frontend panels don't yet.
- Only one Playwright smoke test exists (app loads, all tabs render, no
  thrown errors against a mocked backend) — the full
  success/failure/network-degradation/edge-case e2e matrix from the original
  spec is still a fast-follow.
- Statistical-arbitrage pairs trading (cointegration/Johansen) and the
  macro/cross-asset strategies (yield curve, VIX term structure, COT
  positioning, intermarket lead-lag, seasonality) were scoped out of the
  strategy library expansion — they need a multi-symbol architecture and new
  data sources (COT reports, yield curves) beyond the current single-ticker
  `BaseStrategy` interface and Parquet/yfinance pipeline.
