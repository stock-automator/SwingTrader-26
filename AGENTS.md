# AGENTS.md

Engineering contracts for the strategy, risk, backtesting and analytics
layers: how they fit together and how to extend each one. Product/research
background is in `README.md`.

---

## 1. Architecture Overview

```
data/raw/<TICKER>.parquet
        |
        v
BaseStrategy.generate_signals(df) -> signals DataFrame   [backend/app/quant/strategies/]
        |
        v
RiskManager.build_order(...) -> Order (shares, SL, TP)   [backend/app/quant/risk.py]
        |
        v
   +-------------------+-------------------+
   |                                       |
backend/app/quant/engine.py        backend/app/quant/forward_tester.py
(full-history replay via                (bar-by-bar paper trading,
 the `backtesting` library)               in-memory order book)
   |                                       |
   +-------------------+-------------------+
        |
        v
backend/app/quant/metrics.py (Sharpe, Sortino, MaxDD, win rate, expectancy,
                              profit factor, CAGR; CSV + equity-curve chart export)
```

**Separation of concerns - do not blur these boundaries:**

- A **strategy** only looks at price history and returns *relative* signal
  intent (direction + how far away SL/TP should be). It never knows about
  account size, currency, or share counts.
- **`RiskManager`** is the only place that knows about account equity and
  converts relative SL/TP into absolute prices and share counts.
- **Engines** (`quant/engine.py`, `quant/forward_tester.py`) are the only
  places that simulate order execution, fills, and P&L over time.
- **Analytics** (`quant/metrics.py`, `analytics/expectancy.py`) only consumes
  the trade log / equity curve the engines produce - it never re-derives
  trading logic.

---

## 2. Strategy Signal Schema

Every strategy subclasses `BaseStrategy`
(`backend/app/quant/strategies/base.py`) and implements:

```python
def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
    ...
```

`df` is an OHLCV DataFrame (see §4 for the exact input format). The
returned DataFrame must have the **same index** as `df`, plus these columns:

| Column     | Type  | Values                              | Meaning                                   |
|------------|-------|--------------------------------------|--------------------------------------------|
| `signal`   | int   | `1` (BUY), `-1` (SELL), `0` (HOLD)   | Trade direction intent for this bar        |
| `sl_type`  | str   | `'PERCENTAGE'`, `'FIXED'`, `'ATR'`   | How to interpret `sl_value`                |
| `sl_value` | float | e.g. `0.02`, or `1.5`                | 2% below entry, or 1.5x ATR below entry    |
| `tp_type`  | str   | `'PERCENTAGE'`, `'FIXED'`, `'ATR'`   | How to interpret `tp_value`                |
| `tp_value` | float | e.g. `0.05`, or `3.5`                | 5% above entry, or 3.5x ATR above entry    |

**Convention:** on `HOLD` rows (`signal == 0`), set `sl_value`/`tp_value` to
`NaN` and `sl_type`/`tp_type` to `None` - there is no trade to size. Every
strategy in this repo follows this convention; keep it, so `RiskManager`
and the engines never have to special-case a strategy.

Level-type semantics (resolved by `RiskManager`):

- `'PERCENTAGE'`: offset = `entry_price * value` (e.g. `0.02` -> 2% of entry price)
- `'FIXED'`: offset = `value` (an absolute price distance, e.g. `1.50` -> $1.50)
- `'ATR'`: offset = `value * atr`, where `atr` is the strategy's own ATR
  column at that bar (e.g. `1.5` -> 1.5x ATR). Requires the strategy's
  output DataFrame to include an `atr` column if any row uses `'ATR'`.

In tests, call the static `BaseStrategy.validate_output(df)` on your output;
it raises `ValueError` with a specific reason if the schema is violated (see
`tests/test_moving_average_cross.py`).

### Risk/execution payload (produced by `RiskManager`, consumed by engines)

`RiskManager.build_order(...)` (`backend/app/quant/risk.py`) returns an
`Order`:

```python
@dataclass(frozen=True)
class Order:
    shares: int             # whole shares, 0 if risk-per-share is 0
    entry_price: float
    stop_loss: float        # absolute price
    take_profit: float      # absolute price
    risk_amount: float      # account_equity * risk_per_trade_pct
    risk_per_share: float   # abs(entry_price - stop_loss)
    reward_risk_ratio: float = 0.0       # only set by build_risk_managed_order
    rejected: bool = False               # only set by build_risk_managed_order
    rejection_reason: str | None = None  # only set by build_risk_managed_order
```

The `resolve_stop_loss` / `resolve_take_profit` / `position_size` /
`build_order` signatures and default behaviour are a contract: new sizing
methods or parameters must be additive and opt-in.

---

## 3. Adding a New Strategy

1. Create `backend/app/quant/strategies/<your_strategy_name>.py`.
2. Subclass `BaseStrategy`, implement `generate_signals(self, df) -> pd.DataFrame`
   following the schema in §2. Use `moving_average_cross.py` in the same
   folder as your starting template - it's the minimal reference
   implementation.
3. Engines take a strategy *instance* directly
   (`run_backtest(MyStrategy(), df, risk_manager)`), so a new strategy is
   usable from Python the moment it's imported. To expose it to the API and
   screener, add it to `REGISTRY` in
   `backend/app/quant/strategies/__init__.py` (and to `REQUIRES_BENCHMARK` if
   it needs a benchmark frame via `set_benchmark`).
4. Add `tests/test_<your_strategy_name>.py`, copying the structure of
   `tests/test_moving_average_cross.py`:
   - a `sample_data` fixture with synthetic OHLCV data,
   - a schema test that calls `BaseStrategy.validate_output(result)`,
   - assertions on `sl_type`/`tp_type`/`sl_value`/`tp_value` matching your
     strategy's configured risk parameters on active rows,
   - assertions that inactive (`signal == 0`) rows have NaN SL/TP values,
   - at least one behavioral test (e.g. "no signals during indicator warm-up").
5. Run `pytest tests/test_<your_strategy_name>.py -v` and
   `pytest tests/ -v --cov=backend` before considering the strategy done.
6. Optionally smoke-test it end-to-end:
   ```python
   from backend.app.quant.engine import run_backtest
   from backend.app.quant.risk import RiskManager
   from backend.app.quant.strategies.<your_strategy_name> import <YourStrategy>

   result = run_backtest(<YourStrategy>(), df, RiskManager(account_equity=5000))
   print(result.stats)
   ```

---

## 4. Data Ingestion Format

Strategies and engines expect an OHLCV DataFrame with:

- A `DatetimeIndex` (ascending, no duplicate timestamps).
- Columns `Open`, `High`, `Low`, `Close`, `Volume` (capitalized, exact
  names) with numeric dtypes.

### Reading cached data from `data/raw/`

Files in `data/raw/<TICKER>.parquet` are raw `yfinance` dumps written by
`backend/app/quant/data/parquet_manager.py`; `yfinance` stores columns as a
**MultiIndex** (`(Price, Ticker)`, e.g. `('Close', 'AAPL')`). Don't read
them by hand - use `backend/app/data/loader.py`, which flattens the columns,
sorts and de-duplicates the index, and applies split/dividend adjustment:

```python
from backend.app.data.loader import load_prices

df = load_prices("AAPL", allow_download=False)   # cache only, no network
```

`load_prices` falls back to a live yfinance fetch for anything not cached
unless `allow_download=False`. When comparing tickers, keep timezone
handling consistent and restrict to a common date range.

---

## 5. Engines

### `backend/app/quant/engine.py` - full-history replay

```python
from backend.app.quant.engine import run_backtest
from backend.app.quant.risk import RiskManager
from backend.app.quant.strategies.donchian_breakout import DonchianBreakout

result = run_backtest(
    strategy=DonchianBreakout(),
    df=df,                                   # flattened OHLCV, see §4
    risk_manager=RiskManager(account_equity=5000, risk_per_trade_pct=0.02),
    commission=0.001,
    slippage_pct=0.0005,
)
result.stats          # pandas Series - Sharpe, Return %, # Trades, etc. (from `backtesting`)
result.trades         # DataFrame - one row per closed trade
result.equity_curve   # DataFrame - Equity/DrawdownPct per bar
```

Wraps the `backtesting` PyPI library: the strategy's full signal DataFrame
is precomputed once, then replayed bar-by-bar so `backtesting` handles
fills, commission, and stop/target order mechanics. Optional, opt-in cost
and fill parameters (`fee_per_share`, `atr_slippage_multiple`,
`execution_mode`) are documented in the `run_backtest` docstring.

`result.trades` is the `backtesting` library's native trades DataFrame
(`Size`, `EntryPrice`, `ExitPrice`, `PnL`, `ReturnPct`, `EntryTime`,
`ExitTime`, ...) **plus a lowercase `pnl` alias column** (`pnl == PnL`).
`quant.metrics.compute_metrics` and `ForwardTester.get_trade_log()` both
key off lowercase `pnl` - keep using that column (not `PnL`) when feeding
trades into analytics, so the two engines stay interchangeable there.

**Direction:** by default (`direction=None` or `1`) the engine is
long-only: `signal == 1` opens a long if flat, `signal == -1` closes an
open long. `direction=-1` also allows shorts (`signal == -1` opens a short
when flat, `signal == 1` closes it). The logic lives in `_SignalAdapter.next()`.

### `backend/app/quant/forward_tester.py` - bar-by-bar paper trading

```python
from backend.app.quant.forward_tester import ForwardTester

tester = ForwardTester(strategy, risk_manager, symbol="AAPL", lookback=252)
for _, row in df.iterrows():
    event = tester.step(row)   # row.name must be the bar's timestamp
    # event = {'timestamp', 'action': 'ENTRY'|'EXIT'|'HOLD', ...}

tester.get_open_positions()   # list[dict], 0 or 1 entries (single-position engine)
tester.get_trade_log()        # DataFrame of closed trades
tester.equity_curve           # Series, mark-to-market equity after each bar
```

On each `step()`, the tester (1) checks any open position's SL/TP against
the new bar's High/Low and closes on touch, then (2) if flat, re-runs
`strategy.generate_signals()` over the trailing `lookback` bars and opens a
position if the latest bar signals `1`.

**Out of scope:** multi-symbol orchestration (run one `ForwardTester` per
symbol) and disk persistence (state is in-memory only).

---

## 6. Analytics

### `backend/app/quant/metrics.py`

```python
from backend.app.quant.metrics import (
    compute_metrics,
    export_trades_csv,
    save_equity_curve_chart,
)

metrics = compute_metrics(result.trades, result.equity_curve["Equity"])
# {total_trades, win_rate, expectancy, profit_factor, sharpe_ratio,
#  sortino_ratio, max_drawdown_pct, cagr_pct, max_r_multiple}

export_trades_csv(result.trades, "results/trades.csv")
save_equity_curve_chart(result.equity_curve["Equity"], "results/equity_curve.png")
```

`backend/app/analytics/expectancy.py` (`estimate_win_probability`) builds
the live setup grid's win-probability estimate from regime-matched
backtests; it consumes `run_backtest` output and never re-implements it.

---

## 7. Testing Conventions

- Every new module gets a matching `tests/test_<module>.py`.
- Strategies: synthetic OHLCV fixtures, schema validated via
  `BaseStrategy.validate_output`.
- `RiskManager`: table-driven tests over all three SL/TP types plus edge
  cases (zero risk-per-share, long vs short sign correctness).
- Engines: run a real strategy (`MovingAverageCross`) over a synthetic
  trending fixture and assert the result shape; for `ForwardTester`, a
  scripted stub strategy (`ScriptedStrategy` in
  `tests/test_forward_tester.py`) exercises the state machine (entry / SL
  exit / TP exit) deterministically.
- Tests never require the network (mock `requests`/`yfinance`) and never
  write into tracked `data/raw/` - use `tmp_path`.
- Run the full suite with `pytest tests/ -v --cov=backend` before considering
  work complete.

## 8. Code Review Workflow

Before merging any change into `main` - whether written by a human or an AI
agent - invoke the `reviewer` subagent (`.claude/agents/reviewer.md`) on the
diff:

```
git diff main...HEAD
```

(In Claude Code: the `Agent` tool with `subagent_type: "reviewer"`, or
`claude --agent reviewer`.) It reports findings and does not apply them. Its
`Bash` tool can still write, so after a review run confirm the tree is clean
(`git status --porcelain backend/ tests/`) before trusting the diff.

It checks contracts (`BaseStrategy`, `RiskManager`), type hints, test
coverage, edge-case math (division by zero, ATR = 0, empty frames, NaN
warm-up, lookahead), security and performance (row-wise
`.apply()`/`.iterrows()` on OHLCV).

Address every `BLOCKER` and `WARNING`, or note in the PR/commit why one is
consciously dismissed. `NOTE`s don't block. This applies to human and AI
changes alike and is the step between "tests pass" and "push to `main`", not
a replacement for §7.
