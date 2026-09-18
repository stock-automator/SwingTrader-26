# API Reference

Base URL (local dev): `http://localhost:8000`

All routes are defined in `backend/app/main.py`, `backend/app/api/backtest.py`
and `backend/app/api/screener.py`; request/response shapes come from
`backend/app/api/schemas.py`. This document mirrors those files exactly —
if you change a route, update this page in the same change.

---

## `GET /api/v1/health`

Liveness probe plus which optional data providers are configured.

```bash
curl http://localhost:8000/api/v1/health
```

```json
{
  "status": "ok",
  "finnhub_configured": false,
  "allow_downloads": true
}
```

| Field | Type | Meaning |
|---|---|---|
| `status` | string | Always `"ok"` if the process is up |
| `finnhub_configured` | bool | Whether `FINNHUB_API_KEY` is set (real-time quotes available on the screener) |
| `allow_downloads` | bool | Whether the API may fetch uncached tickers live (`ALLOW_DOWNLOADS`) |

---

## `POST /api/v1/backtest`

Runs a strategy over one or more tickers and compares the resulting $1,000
equity curve to buy & hold and SPY. See `docs/benchmark_math.md` for how
every number in the response is computed.

### Request body

| Field | Type | Default | Notes |
|---|---|---|---|
| `strategy` | string | `"donchian_breakout"` | Must be a key of the strategy registry (`donchian_breakout`, `moving_average_cross`) |
| `strategy_params` | object | `{}` | Passed as kwargs to the strategy constructor |
| `tickers` | string[] | `["AAPL"]` | One or more tickers, run as equally-weighted independent sleeves |
| `start` | string \| null | `null` | Inclusive ISO date, e.g. `"2022-01-01"` |
| `end` | string \| null | `null` | Inclusive ISO date |
| `initial_capital` | float | `1000.0` | Baseline every curve starts at; must be `> 0` |
| `risk_per_trade_pct` | float | `0.02` | Fraction of sleeve equity risked per trade; `(0, 1]` |
| `commission` | float | `0.001` | Round-trip commission rate; `>= 0` |
| `slippage_pct` | float | `0.0005` | Spread applied to fills; `>= 0` |
| `risk_free_rate` | float | `0.0` | Annualised risk-free rate used in Sharpe/alpha; `>= 0` |
| `include_buy_and_hold` | bool | `true` | Emit the buy & hold curve |
| `benchmark` | string | `"SPY"` | Ticker for the second benchmark curve |

Both `tickers` and `strategy` are validated by Pydantic: an empty/blank
ticker list or an unregistered strategy name is rejected before any data is
loaded.

### Example request

```bash
curl -X POST http://localhost:8000/api/v1/backtest \
  -H "Content-Type: application/json" \
  -d '{
        "strategy": "donchian_breakout",
        "tickers": ["AAPL"],
        "start": "2022-01-01",
        "end": "2024-01-01",
        "initial_capital": 1000,
        "risk_per_trade_pct": 0.02
      }'
```

### Response body

```json
{
  "strategy": "donchian_breakout",
  "tickers": ["AAPL"],
  "initial_capital": 1000.0,
  "start": "2022-01-03",
  "end": "2023-12-29",
  "headline": "$1,000 grown to $1,340 vs $1,210 in SPY vs $1,180 buying & holding AAPL",
  "summaries": {
    "strategy":     { "label": "Strategy", "initial_value": 1000.0, "final_value": 1340.0,
                       "total_return_pct": 34.0, "cagr_pct": 15.2, "sharpe_ratio": 1.05,
                       "max_drawdown_pct": -12.4, "volatility_pct": 18.3 },
    "buy_and_hold": { "label": "Buy & hold AAPL", "...": "..." },
    "spy":          { "label": "SPY (S&P 500 ETF)", "...": "..." }
  },
  "vs_spy": {
    "benchmark_label": "SPY",
    "alpha_annual_pct": 4.1,
    "beta": 0.92,
    "sharpe_ratio": 1.05,
    "benchmark_sharpe_ratio": 0.88,
    "information_ratio": 0.34,
    "excess_return_pct": 13.0,
    "tracking_error_pct": 9.7,
    "correlation": 0.81,
    "r_squared": 0.66
  },
  "vs_buy_and_hold": { "...": "same shape as vs_spy, benchmark_label \"Buy & hold AAPL\"" },
  "trade_metrics": { "win_rate": 0.55, "expectancy": 12.3, "profit_factor": 1.4, "...": "..." },
  "equity_curves": [
    { "date": "2022-01-03", "strategy": 1000.0, "buy_and_hold": 1000.0, "spy": 1000.0 },
    { "date": "2022-01-04", "strategy": 1002.1, "buy_and_hold": 998.4, "spy": 1001.0 }
  ],
  "trades": [
    { "ticker": "AAPL", "entry_time": "2022-03-01", "exit_time": "2022-04-12",
      "entry_price": 165.2, "exit_price": 172.4, "size": 6, "pnl": 43.2, "return_pct": 4.36 }
  ],
  "warnings": []
}
```

`vs_spy` and `vs_buy_and_hold` are `null` when that benchmark curve could
not be built (e.g. SPY unreachable, `include_buy_and_hold: false`) — check
`warnings` for why. Any non-finite metric (undefined beta, no losing
trades for profit factor, etc.) is emitted as JSON `null`, never `NaN` or
`Infinity`, so the response always parses.

### Errors

| Status | Cause |
|---|---|
| `422` | Unknown `strategy`, invalid `strategy_params`, empty `tickers`, or a `ValueError` from the comparison engine (e.g. no ticker produced a usable equity curve) |
| `503` | No usable price history for any of the requested tickers (`DataUnavailableError` for every one) |

---

## `GET /api/v1/screener/live`

Latest-bar setup for every ticker in the watchlist (or an explicit override
list), sized against a notional account.

### Query parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `strategy` | string | `"donchian_breakout"` | Registered strategy id |
| `account_equity` | float | `1000.0` | Notional account the screener sizes against; `> 0` |
| `risk_per_trade_pct` | float | `0.02` | `(0, 1]` |
| `tickers` | string \| null | `null` | Comma-separated override for the watchlist, e.g. `AAPL,MSFT,NVDA` |

### Example request

```bash
curl "http://localhost:8000/api/v1/screener/live?strategy=donchian_breakout&tickers=AAPL,MSFT"
```

### Response body

```json
{
  "setups": [
    {
      "ticker": "AAPL",
      "direction": "LONG",
      "tradable": true,
      "as_of": "2026-09-17",
      "close": 231.42,
      "regime": "BULL_TREND",
      "adx": 28.4,
      "atr": 4.12,
      "entry_price": 231.42,
      "stop_loss": 225.1,
      "take_profit": 240.0,
      "shares": 3,
      "risk_amount": 20.0,
      "relative_strength": 1.12,
      "rank": 4,
      "note": null
    }
  ],
  "scanned": 2,
  "skipped": 0,
  "skip_reasons": {},
  "warnings": []
}
```

`direction` is one of `LONG`, `SHORT`, `EXIT_LONG`, `FLAT` (see the module
docstring of `backend/app/quant/setups.py`):

- **`LONG`** — buy signal, sized, tradable by every engine in this repo.
- **`EXIT_LONG`** — sell signal, but as an exit for existing holders, not a
  short entry; unsized (`shares`/`entry_price`/etc. are `null`).
- **`SHORT`** — screening-only: a bearish signal confirmed by a bear-trend
  regime. `tradable: false`, no share count — no engine here can execute a
  short.
- **`FLAT`** — no signal on the latest bar.

### Errors

| Status | Cause |
|---|---|
| `422` | Unknown `strategy` |
| `503` | No usable price history for any watchlist/override ticker |

An empty watchlist is not an error — it returns
`{"setups": [], "scanned": 0, "skipped": 0, "skip_reasons": {}}`.

---

## `WS /ws/screener`

Pushes a fresh screener scan — the same payload shape as
`GET /api/v1/screener/live`, plus a top-level `"warnings"` key — on
`settings.ws_poll_seconds` (default 15s), starting immediately on connect.

### Query parameters

Same as the REST endpoint except there is no `tickers` override: `strategy`,
`account_equity`, `risk_per_trade_pct`.

### Example client

```bash
# using websocat, or any WebSocket client
websocat "ws://localhost:8000/ws/screener?strategy=donchian_breakout"
```

### Frames

A normal push is the scan payload:

```json
{ "setups": [ "..." ], "scanned": 60, "skipped": 2, "skip_reasons": {"NFLX": 1}, "warnings": [] }
```

A scan error (e.g. an unknown strategy passed as a query param) is sent as
an error frame instead of closing the socket, so a single bad scan doesn't
require the client to reconnect:

```json
{ "error": "Unknown strategy 'not_real'. Available: donchian_breakout, moving_average_cross" }
```

The connection stays open until the client disconnects (`WebSocketDisconnect`
is caught server-side and logged, not treated as an error).

---

## Strategy registry

Valid values for `strategy` / the `strategy` query param, from
`backend/app/quant/strategies/__init__.py`:

- `donchian_breakout`
- `moving_average_cross`
