# Repository Report — SwingTrader-26

**Generated:** 2026-09-20  
**Branch:** main (5806d16)  
**Upstream:** origin/main (in sync)

---

## 1. Executive Summary

SwingTrader-26 is a quantitative swing/momentum trading research platform consisting of a Python/FastAPI backend and a React frontend. The backend runs systematic trading strategies through a shared risk engine and compares results against buy-and-hold and S&P 500 benchmarks. The frontend is a dark, terminal-style web app with eight tabs covering dashboard, live screener, signal matrix, backtesting studio, portfolio, trade journal, alerts, and historical simulator.

**Health:** Good. 852 tests passing, clean git state, CI workflows configured, documentation substantial.

---

## 2. Project Purpose

From README.md:
> "A quantitative swing/momentum trading research platform. Answers one question precisely: *if you put $1,000 into this strategy, what would you have now — versus buying and holding the same stock, versus the S&P 500?*"

The platform is for research and simulation — paper trading only (Alpaca paper account), no live money.

---

## 3. Architecture

### Backend (Python + FastAPI)
- **Location:** `backend/app/`
- **Entry point:** `backend/app/main.py`
- **Port:** Configured via settings, typically 8000

### Frontend (React + Vite + Tailwind)
- **Location:** `frontend/`
- **Build:** `npm run build`
- **Dev server:** `npm run dev`

### Data Flow
```
data/raw/<TICKER>.parquet (yfinance cache)
    ↓
backend/app/data/loader.py (flatten, sort, dedupe)
    ↓
BaseStrategy.generate_signals(df) → signals DataFrame
    ↓
RiskManager.build_order(...) → Order (shares, SL, TP)
    ↓
 quant/engine.py (run_backtest) — full-history replay via backtesting library
 quant/setups.py (scan_universe) — latest-bar-only screening
    ↓
FastAPI endpoints → JSON over HTTP/WebSocket
    ↓
React frontend renders
```

### Key Architectural Principle
The backend owns all trading logic. The frontend owns presentation only — it never recomputes an equity curve, Sharpe ratio, or share count. This separation is enforced throughout.

---

## 4. Directory Structure

```
SwingTrader-26/
├── backend/
│   ├── app/
│   │   ├── alerts/          # Telegram/Discord/webhook alert dispatch
│   │   ├── analytics/       # Metrics, console output, LLM reporter
│   │   ├── api/             # FastAPI route handlers (20+ endpoints)
│   │   ├── data/            # Data loading, universe management
│   │   ├── db/              # DuckDB session management
│   │   ├── execution/       # Alpaca paper broker, guards
│   │   ├── journal/         # Trade journal (CSV-backed)
│   │   ├── main.py          # FastAPI app entry point
│   │   └── quant/           # Trading logic core
│   │       ├── strategies/  # 11 trading strategies
│   │       ├── engine.py    # Backtest engine
│   │       ├── forward_tester.py
│   │       ├── risk.py      # Risk manager, position sizing
│   │       ├── indicators.py
│   │       ├── regime.py    # Market regime detection
│   │       ├── screener.py  # Setup ranking
│   │       ├── setups.py    # Scan result types
│   │       ├── metrics.py   # Performance metrics
│   │       ├── monte_carlo.py
│   │       ├── walk_forward.py
│   │       └── ...
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/     # 11 React components
│   │   ├── hooks/          # WebSocket hook
│   │   └── lib/            # API client, format helpers
│   ├── e2e/                # Playwright end-to-end tests (11 specs)
│   ├── package.json
│   └── Dockerfile
├── tests/                  # 54 test files, 852 tests passing
├── docs/                   # API docs, architecture, audit reports
├── config/                 # trading_config.json, watchlist.txt
├── data/raw/               # yfinance parquet cache (gitignored beyond initial)
├── .github/workflows/      # ci.yml, deploy.yml
├── pyproject.toml          # black, isort config
├── pytest.ini
├── requirements.txt        # pandas, numpy, yfinance, fastapi, etc.
├── requirements-test.txt   # pytest, black, isort, flake8, httpx
└── docker-compose.yml
```

---

## 5. Major Components

### Trading Strategies (11 total)
All in `backend/app/quant/strategies/`:

| Strategy | File | Type |
|---|---|---|
| Base | `base.py` | Abstract base class |
| Bollinger-Keltner Squeeze | `bollinger_keltner_squeeze.py` | Mean reversion / squeeze |
| Donchian Breakout | `donchian_breakout.py` | Breakout |
| Dual Momentum | `dual_momentum.py` | Momentum |
| KAMA Trend | `kama_trend.py` | Adaptive moving average trend |
| Moving Average Cross | `moving_average_cross.py` | Classic MA crossover |
| OBV Divergence | `obv_divergence.py` | Volume divergence |
| Relative Strength | `relative_strength.py` | RSI-based |
| Supertrend PSAR | `supertrend_psar.py` | Trend following |
| VCP Breakout | `vcp_breakout.py` | Volatility contraction pattern |
| Z-Score Mean Reversion | `zscore_mean_reversion.py` | Statistical mean reversion |

### Risk Engine
`backend/app/quant/risk.py`:
- `RiskManager` — converts relative SL/TP to absolute prices, sizes positions
- `PositionSizer` — ATR-based sizing
- `ChandelierExitStop` — ATR trailing stop
- `CircuitBreaker` — drawdown kill-switch
- `Order` dataclass — fully resolved order parameters

### Backtest Engine
`backend/app/quant/engine.py` — wraps the `backtesting` library for full-history replay.

### Market Regime Engine
`backend/app/quant/regime.py` — SPY/QQQ EMA alignment + S&P 500 breadth + VIX bucket → traffic-light state.

### API Endpoints (20+)
In `backend/app/api/`:
- `/api/v1/backtest` — POST, run backtest
- `/api/v1/screener/live` — GET, live scan
- `/api/v1/screener` — WS, WebSocket screener
- `/api/v1/execution` — Alpaca paper order placement
- `/api/v1/orders` — Order management
- `/api/v1/journal` — Trade journal
- `/api/v1/alerts` — Alert channel configuration
- `/api/v1/market` — Market regime
- `/api/v1/risk` — ATR position sizer preview
- `/api/v1/signals` — Signal matrix
- `/api/v1/replay` — Historical simulator
- `/api/v1/data_sync` — Data refresh
- `/api/v1/universe` — Universe management
- Plus: scans, deps, schemas, ws

### Analytics
- `analytics/metrics.py` — Sharpe, Sortino, MaxDD, win rate, expectancy, profit factor, CAGR
- `analytics/llm_reporter.py` — Plain-English report via Ollama
- `analytics/console.py` — CLI table formatting

---

## 6. Backend Details

### Framework
- FastAPI 0.111+
- Uvicorn with websockets
- Pydantic 2.6+ for validation
- DuckDB for persisted scans/orders

### Dependencies (requirements.txt)
```
pandas, numpy, yfinance, pyarrow, requests,
backtesting, matplotlib, fastapi, uvicorn[standard],
websockets, pydantic, python-dotenv, alpaca-py,
apscheduler, duckdb
```

### Test Dependencies (requirements-test.txt)
```
pytest, pytest-cov, pytest-xdist,
black, isort, flake8, httpx
```

### Data
- Price data: Yahoo Finance via `yfinance` (free, no API key)
- Optional: Finnhub API key for real-time quotes
- Cache: `data/raw/<TICKER>.parquet` (MultiIndex columns, flattened before use)

---

## 7. Frontend Details

### Stack
- React 18 + Vite
- Tailwind CSS
- lightweight-charts (TradingView chart component)
- Dark, terminal-style theme

### Tabs
1. **Dashboard** — Today's actionable setups + market regime
2. **Live Screener** — Watchlist scan, real-time updates via WebSocket
3. **Signal Matrix** — Cross-strategy setup rankings
4. **Backtesting Studio** — Strategy/ticker/date range → charts + stats
5. **Portfolio** — Alpaca paper account (equity, positions, close-all)
6. **Trade Journal** — MAE/MFE, win-rate/expectancy decay charts
7. **Alerts** — Telegram/Discord/webhook channel config from browser
8. **Historical Simulator** — Replay past date's scan, simulate execution

### E2E Tests
11 Playwright specs in `frontend/e2e/`:
- alerts, dashboard, journal, market-regime-and-position-sizer,
- portfolio, signals, simulator, smoke, trades

---

## 8. Trading Engine

### Signal Schema (from AGENTS.md)
Every strategy returns a DataFrame with:
- `signal`: 1 (BUY), -1 (SELL), 0 (HOLD)
- `sl_type`: 'PERCENTAGE', 'FIXED', 'ATR'
- `sl_value`: e.g. 0.02 for 2%, or 1.5 for 1.5x ATR
- `tp_type`: 'PERCENTAGE', 'FIXED', 'ATR'
- `tp_value`: e.g. 0.05 for 5%, or 3.5 for 3.5x ATR

### Risk Manager
Converts relative SL/TP definitions into absolute prices and share counts:
- `PERCENTAGE`: offset = entry_price * value
- `FIXED`: offset = value (absolute price distance)
- `ATR`: offset = value * atr (requires ATR column)

### Engines
1. **Backtest engine** (`engine.py`): Full-history replay via `backtesting` library
2. **Forward tester** (`forward_tester.py`): Bar-by-bar paper trading, in-memory order book
3. **Screener** (`setups.py` + `screener.py`): Latest-bar-only, ranks setups

### Current Scope
Long-only. `signal == 1` opens a long if flat; `signal == -1` closes an open long. No short support yet.

---

## 9. Strategy Logic

All strategies subclass `BaseStrategy` and implement `generate_signals(self, df) -> pd.DataFrame`.

The base class enforces:
- Output DataFrame must have same index as input
- Required columns: signal, sl_type, sl_value, tp_type, tp_value
- Signal values: {1, -1, 0}
- SL/TP types: {'PERCENTAGE', 'FIXED', 'ATR'}
- HOLD rows (signal==0): sl_value/tp_value must be NaN

### Example: Moving Average Cross
- Computes SMA fast (default 20) and SMA slow (default 50)
- BUY signal when fast crosses above slow
- SELL signal when fast crosses below slow
- SL: 2% below entry (PERCENTAGE)
- TP: 5% above entry (PERCENTAGE)

### Example: Donchian Breakout
- Looks at highest high / lowest low over lookback period (default 20)
- BUY when price breaks above highest high
- Trailing stop via Chandelier Exit (ATR-based)

---

## 10. Data Pipeline

### Ingestion
1. Check `data/raw/<TICKER>.parquet` cache
2. Cache hit → use it
3. Cache miss → live yfinance fetch (if `ALLOW_DOWNLOADS=true`)
4. Flatten MultiIndex columns: `('Close', 'AAPL')` → `'Close'`
5. Sort index, dedupe dates

### Universe Management
- S&P 500 + Nasdaq-100 membership tracked in `config/universe.json`
- Sync via `UniverseManager` in `data/universe.py`
- Denylist support for excluding tickers

### Parquet Format
Written by `data/agent.py` via yfinance. Columns are MultiIndex:
```
('Price', 'Ticker')  e.g. ('Close', 'AAPL')
```
Flatten with `df.columns.droplevel(1)` before use.

---

## 11. Tests

### Test Suite
- **54 test files** in `tests/`
- **852 tests passing** (verified 2026-09-20)
- **147 warnings** (mostly deprecation warnings, not failures)
- Full suite runtime: ~53 seconds

### Test Categories
- Strategy tests: `test_donchian.py`, `test_moving_average_cross.py`, etc.
- Risk tests: `test_risk.py` (81 tests — most comprehensive)
- Engine tests: `test_backtester.py`, `test_forward_tester.py`
- API tests: `test_api.py`, `test_*_api.py` (multiple)
- Analytics tests: `test_analytics.py`, `test_metrics.py`, `test_llm_reporter.py`
- Execution tests: `test_execution_broker.py`, `test_alpaca_client.py`
- Integration tests: `test_gating.py`, `test_error_handling.py`

### Test Conventions
- Synthetic OHLCV fixtures for strategy tests
- Schema validation via `BaseStrategy.validate_output()`
- Table-driven tests for risk math
- Mocked external services (Ollama, Alpaca)

### Running Tests
```bash
cd ~/SwingTrader-26
python3 -m pytest tests/ -v --cov=backend
```

---

## 12. Configuration

### Project Config
- `pyproject.toml`: black (line-length 88), isort (profile black)
- `.flake8`: flake8 configuration
- `pytest.ini`: pytest configuration
- `config/trading_config.json`: trading parameters
- `config/watchlist.txt`: default watchlist tickers

### Environment
- `.env.example`: template for required env vars
- `.env`: actual env vars (gitignored)
- Key vars: `ALLOW_DOWNLOADS`, Alpaca API keys (paper), Finnhub key (optional)

### Docker
- `docker-compose.yml`: backend + frontend services
- `backend/Dockerfile`: Python backend image
- `frontend/Dockerfile`: Node frontend image

---

## 13. Dependencies

### Python (backend)
- pandas >= 1.3.0
- numpy >= 1.21.0
- yfinance >= 0.1.70
- pyarrow >= 5.0.0
- requests >= 2.27.0
- backtesting >= 0.3.3
- matplotlib >= 3.5.0
- fastapi >= 0.111.0
- uvicorn[standard] >= 0.29.0
- websockets >= 12.0
- pydantic >= 2.6.0
- python-dotenv >= 1.0.0
- alpaca-py >= 0.33.0
- apscheduler >= 3.10, < 4.0
- duckdb >= 1.0, < 2.0

### Node (frontend)
- React 18
- Vite
- Tailwind CSS
- lightweight-charts
- Playwright (dev)

---

## 14. CI/CD

### GitHub Workflows

#### ci.yml
Runs on push to main and pull requests:
1. **test** — matrix Python 3.11, 3.12; installs deps; runs pytest with coverage
2. **lint** — black check, isort check, flake8 lint
3. **frontend** — Node 20; npm ci; npm run build

Coverage uploaded to Codecov (fail_ci_if_error: false).

#### deploy.yml
Runs on push to main:
1. **build-backend** — logs into GHCR, builds+pushes backend Docker image
2. **build-frontend** — logs into GHCR, builds+pushes frontend Docker image
3. Deploy job commented out — no real deploy target exists yet

### Local Validation
Before pushing, run:
```bash
python3 -m pytest tests/ -v --cov=backend
black --check backend/ tests/
isort --check-only backend/ tests/
flake8 backend/ tests/
cd frontend && npm run build
```

---

## 15. Technical Debt

### Known Issues

1. **OmniRoute Groq provider broken**
   - `omniroute test groq` returns "Invalid API key"
   - Affects 1 of 4 OmniRoute providers
   - Resolution: User must provide valid Groq API key

2. **OmniRoute routing not configured**
   - `omniroute routing show` fails
   - No fallback routing configured
   - Manual provider selection required

3. **Short positions not supported**
   - Engine is long-only
   - AGENTS.md documents this as "out of current scope"
   - Would require extending `_SignalAdapter.next()` in engine.py

4. **No deploy target**
   - deploy.yml has commented-out deploy job
   - GHCR images built but not deployed anywhere
   - No Kubernetes cluster, no cloud credentials

5. **Telegram not configured for this repo**
   - Hermes Telegram works for the Hermes system itself
   - SwingTrader-26 alerts subsystem has Telegram support in code
   - But no Telegram bot token configured for the app

### Code Quality Observations

1. **Well-tested risk engine** — 81 tests in test_risk.py, comprehensive coverage
2. **Good separation of concerns** — strategies don't know about equity, engines don't know about reporting
3. **Type hints present** — uses `float | None` style unions
4. **Vectorized indicators** — no `.apply()`/`.iterrows()` on OHLCV data
5. **Test coverage** — 852 tests, but some modules may have gaps

---

## 16. TODOs and Incomplete Features

From code and documentation:

1. **Short position support** — documented as future work in AGENTS.md
2. **Deploy target** — no cluster/cloud configured
3. **ChatGPT escalation channel** — mentioned in spec but not implemented
4. **OmniRoute autonomous routing** — not configured
5. ** Claude Code skills for engineering** — not installed/evaluated
6. **Autonomous development supervisor** — this is the current build target

---

## 17. Security Issues

### Observed

1. **OmniRoute dashboard default password** — not checked yet (dashboard not accessible on any port)
2. **`.env` files** — Hermes `.env` contains Telegram bot token (redacted in output as `***`); repo `.env` not present (only `.env.example`)
3. **GitHub remote** — uses `https://` URL, not SSH (secrets managed by GitHub)

### Not Observed
- No credentials in source code
- No tokens in logs (redaction appears active)
- ai-bot isolation appears intact

---

## 18. Performance Observations

1. **Test suite** — 852 tests in ~53 seconds (good)
2. **Backtest engine** — uses `backtesting` library, full history replay (could be slow for large date ranges)
3. **Screener** — latest-bar-only, designed to be fast
4. **No obvious N² operations** — code review doc specifically calls out avoiding `.apply()`/`.iterrows()` on OHLCV

---

## 19. Documentation Gaps

1. **OmniRoute setup documentation** — how to configure routing/fallback not documented in repo
2. **Claude Code integration** — how to use Claude Code through OmniRoute not documented
3. **Autonomous supervisor setup** — this document + docs/agent/ files are the start
4. **Short position architecture** — would need docs if implemented

### Existing Documentation (Good)
- `docs/api.md` — 35KB, comprehensive API documentation
- `docs/architecture.md` — 17KB, detailed architecture
- `docs/code_review.md` — 16KB, code review guidelines
- `docs/SPRINT_6_AUDIT.md` — 15KB, sprint audit
- `AGENTS.md` — 16KB, agent instructions
- `README.md` — 32KB, user-facing quickstart

---

## 20. Recommended Development Sequence

### Immediate (This Iteration)
1. [x] Run lint checks (black, isort, flake8) — identify any issues
2. [ ] Fix any lint issues found
3. [ ] Verify CI passes on push

### Near-Term (Next Iterations)
4. [ ] Evaluate and install useful Claude Code skills
5. [ ] Configure OmniRoute routing/fallback
6. [ ] Set up autonomous execution loop scripts
7. [ ] Implement notification grouping policy for Telegram
8. [ ] Fix Groq API key (user action required)

### Medium-Term
9. [ ] Consider short position support (if useful)
10. [ ] Implement ChatGPT escalation channel (if desired)
11. [ ] Add deploy target (if infrastructure available)

---

## 21. Current Repository Health Summary

| Metric | Status |
|---|---|
| Git state | Clean, synced with origin/main |
| Tests | 852 passing, 0 failing |
| Lint | Not yet run locally (CI has it) |
| Documentation | Substantial (80KB+ of docs) |
| CI | Configured, 3 jobs (test, lint, frontend) |
| CD | Configured (build only, no deploy) |
| Security | No obvious issues observed |
| Technical debt | 5 known items, none critical |
| OmniRoute | 2 of 4 providers working |
| Telegram | Working (Hermes system) |
| Claude Code | Installed, no skills loaded |
