# API Reference

Base URL (local dev): `http://localhost:8000`

All routes are defined in `backend/app/main.py` and `backend/app/api/*.py`;
request/response shapes come from `backend/app/api/schemas.py`. This
document mirrors those files exactly — if you change a route, update this
page in the same change.

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
| `strategy` | string | `"donchian_breakout"` | Must be a key of the strategy registry — see [Strategy registry](#strategy-registry) |
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

## `GET /api/v1/signals/live-today`

Cross-strategy live signal matrix: runs every registered strategy (or a
requested subset) over the watchlist and flattens every actionable
(`LONG`/`SHORT`) row into one grid tagged with which strategy produced it —
"what should I look at today," not the whole scanned universe (`FLAT`/
`EXIT_LONG` rows are dropped).

### Query parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `strategies` | string \| null | `null` | Comma-separated subset of the strategy registry; omit for all |
| `tickers` | string \| null | `null` | Comma-separated override for the watchlist |
| `account_equity` | float | `1000.0` | `> 0` |
| `risk_per_trade_pct` | float | `0.02` | `(0, 1]` |
| `earnings_blackout` | bool | `false` | Suppress long entries within 5 trading days of a known earnings date |

### Example request

```bash
curl "http://localhost:8000/api/v1/signals/live-today?strategies=donchian_breakout,vcp_breakout"
```

### Response body

```json
{
  "generated_at": "2026-09-18T14:03:00+00:00",
  "rows": [
    {
      "ticker": "AAPL", "strategy": "donchian_breakout", "direction": "LONG",
      "tradable": true, "as_of": "2026-09-17", "close": 231.42,
      "entry_price": 231.42, "stop_loss": 225.1, "take_profit": 240.0,
      "shares": 3, "risk_amount": 20.0, "reward_risk_ratio": 2.9,
      "notional_value": 694.26, "note": null,
      "win_probability": 0.58,
      "win_probability_method": "regime_matched_backtest",
      "win_probability_sample_size": 17,
      "win_probability_confidence_low": null,
      "win_probability_confidence_high": null,
      "win_probability_note": "17 historical trades entered in BULL_TREND regime."
    }
  ],
  "scanned": 120,
  "warnings": []
}
```

`win_probability` (`analytics/expectancy.py`) is a real historical estimate,
computed one of two ways depending on how much trade history backs it - see
`win_probability_method`:

- `"regime_matched_backtest"` - the win rate of the strategy's own
  historical closed trades entered in the *same* market regime
  (`quant/regime.py`) as this setup, once at least 12 such trades exist.
- `"block_bootstrap"` - a block-bootstrap Monte Carlo percentile (median of
  2,000 resampled win rates) over *all* the strategy's historical trades on
  this ticker, used when there aren't enough regime-matched trades.
  `win_probability_confidence_low`/`_high` are that resample's 5th/95th
  percentile band.
- `"insufficient_data"` - fewer than 8 total closed trades exist for this
  ticker/strategy; `win_probability` is `null` rather than a number with no
  real support behind it.

Always `null` (method `"insufficient_data"`) for `SHORT` rows: the backtest
engine is long-only, so there is no historical fill to estimate from.

A single strategy failing (bad params, no benchmark available for a
benchmark-dependent strategy) is recorded in `warnings` and skipped, not a
request failure — one broken strategy must not blank out the rest of the
grid.

### Errors

| Status | Cause |
|---|---|
| `422` | An unknown name in `strategies` |
| `503` | No usable price history for any watchlist/override ticker |

---

## `POST /api/v1/backtest/historical-date-scan`

Point-in-time replay: "what would the screener have shown on date X."
Zero-lookahead is structural, not conventional — every ticker's frame is
sliced to bars on or before `target_date` *before* the strategy ever sees
it (the same `load_frames(..., end=...)` path a normal date-ranged
backtest uses), so a strategy cannot see a bar that hadn't happened yet.

### Request body

| Field | Type | Default | Notes |
|---|---|---|---|
| `strategy` | string | `"donchian_breakout"` | A registered strategy id |
| `strategy_params` | object | `{}` | Passed as kwargs to the strategy constructor |
| `tickers` | string[] \| null | `null` | Omit to scan the full watchlist |
| `target_date` | string | — | Required. Inclusive ISO date, e.g. `"2023-06-15"`; must not be in the future |
| `account_equity` | float | `1000.0` | `> 0` |
| `risk_per_trade_pct` | float | `0.02` | `(0, 1]` |
| `earnings_blackout` | bool | `false` | Same as the live screener |

### Response body

Same shape as `GET /api/v1/screener/live`'s `setups`/`scanned`/`skipped`/
`skip_reasons`, plus `target_date` echoed back. A ticker whose history
doesn't reach far enough back to clear the strategy's warm-up comes back
`INSUFFICIENT_HISTORY` in `skip_reasons` — that's correct, not a bug.

### Errors

| Status | Cause |
|---|---|
| `422` | Unknown `strategy`, invalid `target_date`, or `target_date` in the future |
| `503` | No usable price history on or before `target_date` for any requested ticker |

---

## `POST /api/v1/backtest/simulate-trade-execution`

"What would this specific signal have actually filled at" — the same
realistic-cost model `POST /api/v1/backtest` applies in aggregate, applied
here to one trade in isolation so a `historical-date-scan` result can be
chained into "and if I'd taken it."

### Request body

| Field | Type | Default | Notes |
|---|---|---|---|
| `ticker` | string | — | Required |
| `entry_date` | string | — | Required. ISO date of the signal bar |
| `sl_type` / `sl_value` | string / float | — | Required, same semantics as `POST /api/v1/order-ticket` |
| `tp_type` / `tp_value` | string / float | — | Required |
| `direction` | int | `1` | `1` (long) or `-1` (short) |
| `account_equity` | float | `1000.0` | `> 0` |
| `risk_per_trade_pct` | float | `0.02` | `(0, 1]` |
| `execution_mode` | string | `"NEXT_OPEN"` | `"NEXT_OPEN"` (fills at the *next* bar's open) or `"SAME_CLOSE_SLIPPAGE"` (fills at `entry_date`'s own close) |
| `slippage_pct` | float | `0.0005` | Flat spread applied against the trader; ignored if `atr_slippage_multiple > 0` |
| `commission` | float | `0.001` | Round-trip commission rate, applied to notional |
| `fee_per_share` | float | `0.0` | Overrides `commission` with a fixed $/share fee when `> 0` |
| `atr_slippage_multiple` | float | `0.0` | When `> 0`, spread is priced per-bar off *this bar's* ATR/Close ratio (`quant/slippage_model.estimate_dynamic_spread_pct`) instead of the flat `slippage_pct` |
| `impact_coefficient` | float | `0.0` | When `> 0`, adds a square-root volume-based market-impact cost (`quant/slippage_model.estimate_market_impact_pct`) on top of spread. `0.0` disables market-impact modelling entirely |
| `avg_volume_lookback` | int | `20` | Trailing bar count averaged for the market-impact volume denominator |

Sizing goes through `RiskManager.build_risk_managed_order` — the same
minimum-2.5R and portfolio-risk gate every trader-facing order ticket in
this repo uses, so a structurally bad trade comes back `tradable: false`
with a `note`, not a share count. It runs twice: once off a spread-only fill
price to get a share count market impact can be computed from, then again
off the final (spread + impact) fill price, so the response's stop-loss/
take-profit/shares are all consistent with the `fill_price` it reports.

### Response body

```json
{
  "ticker": "AAPL",
  "execution_mode": "NEXT_OPEN",
  "fill_date": "2023-06-16",
  "fill_price": 187.34,
  "reference_price": 187.20,
  "slippage_pct_applied": 0.0005,
  "spread_pct": 0.0005,
  "market_impact_pct": 0.0,
  "spread_variance_pct": 0.00012,
  "slippage_cost": 0.42,
  "commission_cost": 0.19,
  "total_cost": 562.21,
  "shares": 3,
  "stop_loss": 181.0,
  "take_profit": 200.0,
  "risk_amount": 19.02,
  "reward_risk_ratio": 2.9,
  "notional_value": 562.02,
  "tradable": true,
  "note": null
}
```

`slippage_pct_applied` is `spread_pct + market_impact_pct` - the total
fraction actually applied to `reference_price` to produce `fill_price`.
`spread_variance_pct` is the ATR-implied spread's own trailing standard
deviation - a confidence read on `spread_pct` as a point estimate, not
another cost component.

### Errors

| Status | Cause |
|---|---|
| `404` | No bar for `ticker` on `entry_date` |
| `422` | Invalid `entry_date`, unknown `execution_mode`, or `entry_date` is the last available bar under `NEXT_OPEN` (no next bar to fill on) |
| `503` | No usable price history for `ticker` |

---

## `POST /api/v1/alerts/dispatch`

Sends one alert to every configured channel (Telegram, Discord, generic
webhook) — on-demand only; nothing in this API auto-fires an alert from a
screener or signal-matrix scan.

### Request body

| Field | Type | Notes |
|---|---|---|
| `title` | string | Required |
| `body` | string | Required |
| `ticker` | string \| null | Optional |
| `direction` | string \| null | Optional |
| `url` | string \| null | Optional |

### Response body

```json
{ "results": { "telegram": "sent", "discord": "failed: 401 Client Error" } }
```

`{"results": {}}` means no channel is configured (`TELEGRAM_BOT_TOKEN` +
`TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`, `GENERIC_WEBHOOK_URL`) — that's a
valid response, not an error. One channel failing never blocks another.

---

## `/api/v1/execution/*` — Alpaca paper trading

Every route 503s with a clear message if `ALPACA_API_KEY`/
`ALPACA_API_SECRET` aren't set. The underlying client is always
`paper=True` — there is no setting anywhere that makes this platform place
a live order.

### `POST /api/v1/execution/orders`

| Field | Type | Default | Notes |
|---|---|---|---|
| `ticker` | string | — | Required |
| `side` | string | — | `"buy"` or `"sell"` |
| `qty` | float | — | `> 0` |
| `order_type` | string | `"MARKET"` | `"MARKET"`, `"LIMIT"`, or `"BRACKET"` |
| `limit_price` | float \| null | `null` | Required for `LIMIT`; optional entry price for a `BRACKET` (omit for a market-entry bracket) |
| `stop_loss` / `take_profit` | float \| null | `null` | Required for `BRACKET` |

Before dispatching to Alpaca, this route runs `execution/guards.py`'s
`check_order_guards` (unless `EXECUTION_GUARDS_ENABLED=false`):

- **Session guard** — refuses to dispatch while the US market is closed,
  and by default refuses pre-market/after-hours too
  (`EXECUTION_ALLOW_EXTENDED_HOURS=true` opts into dispatching there, still
  flagged as illiquid).
- **Earnings/split lockout guard** — refuses a new entry within
  `EARNINGS_LOCKOUT_HOURS` (default 48) of a scheduled earnings release or
  stock split, on either side of it. Fails *open* (does not block) if the
  earnings/split calendar can't be fetched for the ticker — a data-provider
  gap is not treated as a clean bill of health, but it also must not brick
  order dispatch.

A rejected order never reaches Alpaca: guard failures come back as a `422`
with every reason that applied, not just the first one hit.

### `POST /api/v1/execution/close-all`

Emergency kill-switch: liquidates every open position and cancels every
open order. Body must be `{"confirm": true}` — `422` otherwise. This is
paper money, but treated with the same care as any account-wide destructive
action.

### `GET /api/v1/execution/account`

Returns `account_number`, `status`, `equity`, `cash`, `buying_power`,
`portfolio_value`.

### Errors

| Status | Cause |
|---|---|
| `422` | Alpaca isn't reached yet but the request is malformed: missing `limit_price`/`stop_loss`/`take_profit` for the chosen `order_type`, `close-all` without `confirm: true`, or (`/orders` only) a session/earnings-lockout guard rejected dispatch |
| `503` | Alpaca credentials aren't configured |

---

## `/api/v1/journal/*` — trade journal

Thin wrapper around the CSV-backed `journal.executor.TradeJournal`
(`JOURNAL_PATH`, default `data/trades_live.csv`). Logging entries/exits
into the journal is currently done via the `TradeJournal` class directly
(e.g. from a script or notebook) — these routes are the read side.

### `GET /api/v1/journal/summary`

All-time win rate / expectancy / drawdown. Returns `{"error": "No
completed trades yet"}` if nothing has been logged.

### `GET /api/v1/journal/decay?windows=30,60,90`

Rolling win-rate/expectancy over trailing day-count windows, to catch a
strategy's edge decaying before it's obvious in the all-time numbers:

```json
{
  "windows": {
    "30": { "trade_count": 4, "win_rate": 0.75, "avg_r_multiple": 1.8, "expectancy": 42.1 },
    "60": { "trade_count": 9, "win_rate": 0.56, "avg_r_multiple": 0.9, "expectancy": 11.4 },
    "90": { "trade_count": 15, "win_rate": 0.53, "avg_r_multiple": 0.7, "expectancy": 6.2 }
  }
}
```

A window with zero exited trades reports `null` for the rate fields, not a
division-by-zero error.

### `POST /api/v1/journal/mae-mfe`

Maximum Adverse/Favorable Excursion for one closed trade — how far it moved
against you and in your favor at any point during the holding period, not
just at exit. Body: `{"trade_id": 12, "ticker": "AAPL"}`.

### Errors

| Status | Cause |
|---|---|
| `422` | Unknown `trade_id`, or the trade isn't `TAKEN` with both entry and exit dates recorded |
| `503` | No usable price history for `ticker` |

---

## Strategy registry

Valid values for `strategy` / the `strategy` query param, from
`backend/app/quant/strategies/__init__.py`:

**Trend & momentum**
- `donchian_breakout` — Donchian channel breakout + trend + momentum + volume confirmation
- `moving_average_cross` — fast/slow moving-average crossover
- `dual_momentum` — absolute + relative momentum vs. a benchmark (`REQUIRES_BENCHMARK`)
- `supertrend_psar` — Supertrend flip confirmed by price above Parabolic SAR

**Volatility & regime**
- `vcp_breakout` — Volatility Contraction Pattern base + breakout
- `bollinger_keltner_squeeze` — Bollinger Band squeeze inside Keltner Channel + directional release
- `kama_trend` — Kaufman Adaptive Moving Average trend-following

**Mean reversion & market structure**
- `zscore_mean_reversion` — oversold z-score pullback, gated by a Hurst-exponent mean-reverting regime
- `obv_divergence` — bullish OBV/price divergence + turn confirmation

**Relative strength**
- `relative_strength` — RS-vs-benchmark leadership + pullback + resumption (`REQUIRES_BENCHMARK`)

Strategies tagged `REQUIRES_BENCHMARK` need a benchmark frame (SPY by
default) wired in — `POST /api/v1/backtest`, the analytics endpoints, and
`GET /api/v1/signals/live-today` all do this automatically; a strategy in
that set has no effect run standalone through `GET /api/v1/screener/live`.
