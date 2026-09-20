# AGENTS.md

Instructions for AI coding agents (and humans) working on the strategy,
risk, backtesting, and analytics layers of this repository.

For product/research background (universe, data provenance, trading
philosophy) see `README.md`. This document covers the **engineering
contracts**: how strategies, risk sizing, execution engines, and analytics
fit together, and how to extend each one.

> **Paths below predate the FastAPI/React refactor.** Every `src/...` and
> `engine/...` path in this document is the pre-refactor layout; the
> **contracts and separation of concerns are still accurate**, only the
> file locations moved. Translate as you read:
>
> | This document says | Now lives at |
> |---|---|
> | `src/strategies/` | `backend/app/quant/strategies/` |
> | `src/strategies/base_strategy.py` | `backend/app/quant/strategies/base.py` |
> | `src/core/risk.py` | `backend/app/quant/risk.py` |
> | `engine/backtester.py` | `backend/app/quant/engine.py` |
> | `engine/forward_tester.py` | `backend/app/quant/forward_tester.py` |
> | `analytics/metrics.py`, `console.py`, `llm_reporter.py` | `backend/app/analytics/` |
> | `src/agent.py` / `src/data/update_data.py` | `backend/app/data/agent.py` / `backend/app/data/loader.py` |
> | `pytest tests/ -v --cov=src` | `pytest tests/ -v --cov=backend` |
>
> A full pass rewriting every code sample below to the new paths is a
> tracked fast-follow, not done in this change — the sections were written
> against a single-ticker CLI/notebook workflow (`src/strategies`,
> in-process `ForwardTester`) that has no direct FastAPI-era equivalent to
> point at yet (the live-screener/backtest engines in `backend/app/quant/`
> cover the same ground but with a different calling convention). Treat
> every path below as "conceptually here, mechanically moved," not literal.

---

## 1. Architecture Overview

```
data/raw/<TICKER>.parquet
        |
        v
BaseStrategy.generate_signals(df) -> signals DataFrame   [src/strategies/]
        |
        v
RiskManager.build_order(...) -> Order (shares, SL, TP)   [src/core/risk.py]
        |
        v
   +-------------------+-------------------+
   |                                       |
engine/backtester.py                engine/forward_tester.py
(full-history replay via                (bar-by-bar paper trading,
 the `backtesting` library)               in-memory order book)
   |                                       |
   +-------------------+-------------------+
        |
        v
analytics/metrics.py (Sharpe, Sortino, MaxDD, win rate, expectancy,
                       profit factor, CAGR; CSV + equity-curve chart export)
        |
        v
analytics/llm_reporter.py (plain-English performance report via Ollama)
```

**Separation of concerns - do not blur these boundaries:**

- A **strategy** only looks at price history and returns *relative* signal
  intent (direction + how far away SL/TP should be). It never knows about
  account size, currency, or share counts.
- **`RiskManager`** is the only place that knows about account equity and
  converts relative SL/TP into absolute prices and share counts.
- **Engines** (`backtester.py`, `forward_tester.py`) are the only places
  that simulate order execution, fills, and P&L over time.
- **Analytics** only consumes the trade log / equity curve the engines
  produce - it never re-derives trading logic.

---

## 2. Strategy Signal Schema

Every strategy subclasses `BaseStrategy` (`src/strategies/base_strategy.py`)
and implements:

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

Level-type semantics (resolved by `RiskManager`, see §3):

- `'PERCENTAGE'`: offset = `entry_price * value` (e.g. `0.02` -> 2% of entry price)
- `'FIXED'`: offset = `value` (an absolute price distance, e.g. `1.50` -> $1.50)
- `'ATR'`: offset = `value * atr`, where `atr` is the strategy's own ATR
  column at that bar (e.g. `1.5` -> 1.5x ATR). Requires the strategy's
  output DataFrame to include an `atr` column if any row uses `'ATR'`.

Call `BaseStrategy.validate_output(df)` (a static method) on your strategy's
output in tests - it asserts the schema above and raises `ValueError` with a
specific reason if it's violated. See `tests/test_donchian.py` /
`tests/test_moving_average_cross.py` for usage.

### Risk/execution payload (produced by `RiskManager`, consumed by engines)

`RiskManager.build_order(...)` returns an `Order`:

```python
@dataclass(frozen=True)
class Order:
    shares: int             # whole shares, 0 if risk-per-share is 0
    entry_price: float
    stop_loss: float        # absolute price
    take_profit: float      # absolute price
    risk_amount: float      # account_equity * risk_per_trade_pct
    risk_per_share: float   # abs(entry_price - stop_loss)
```

---

## 3. Adding a New Strategy

1. Create `src/strategies/<your_strategy_name>.py`.
2. Subclass `BaseStrategy`, implement `generate_signals(self, df) -> pd.DataFrame`
   following the schema in §2. Reuse `src/strategies/moving_average_cross.py`
   as your starting template - it's the minimal reference implementation.
3. No registry, no factory, no config file to edit - engines take a strategy
   *instance* directly (`run_backtest(MyStrategy(), df, risk_manager)`), so a
   new strategy is usable the moment it's imported.
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
   from backend.app.quant.risk import RiskManager
   from backend.app.quant.engine import run_backtest
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

Files in `data/raw/<TICKER>.parquet` are written by `src/agent.py` /
`src/data/update_data.py` via `yfinance`, which stores columns as a
**MultiIndex** (`(Price, Ticker)`, e.g. `('Close', 'AAPL')`). Flatten this
before passing data to any strategy/engine:

```python
import pandas as pd

df = pd.read_parquet("data/raw/AAPL.parquet")
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.droplevel(1)   # ('Close', 'AAPL') -> 'Close'
df = df.sort_index()
```

This mirrors the loader pattern already used in the repo's exploratory
backtest scripts (e.g. `backtest_with_exits.py`).

Other data-prep notes (see `README.md` §6 for full detail): remove
duplicate dates, handle timezone-naive vs -aware indices consistently
across tickers, and restrict to a common research date range when
comparing multiple tickers.

---

## 5. Engines

### `engine/backtester.py` - full-history replay

```python
from src.core.risk import RiskManager
from src.engine.backtester import run_backtest
from src.strategies.donchian_breakout import DonchianBreakout

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
fills, commission, and stop/target order mechanics.

`result.trades` is the `backtesting` library's native trades DataFrame
(`Size`, `EntryPrice`, `ExitPrice`, `PnL`, `ReturnPct`, `EntryTime`,
`ExitTime`, ...) **plus a lowercase `pnl` alias column** (`pnl == PnL`).
`analytics.metrics.compute_metrics` and `ForwardTester.get_trade_log()`
both key off lowercase `pnl` - keep using that column (not `PnL`) when
feeding trades into analytics, so the two engines stay interchangeable
there.

**Current scope: long-only.** `signal == 1` opens a long if flat;
`signal == -1` closes an open long. It does not open short positions. To
add short support, extend the `_SignalAdapter.next()` logic in
`engine/backtester.py` to call `self.sell(...)` and pass `direction=-1` to
`RiskManager`.

### `engine/forward_tester.py` - bar-by-bar paper trading

```python
from src.engine.forward_tester import ForwardTester

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

**Explicitly out of scope** (documented here so an agent doesn't assume
otherwise): multi-symbol portfolio orchestration (run one `ForwardTester`
per symbol), and disk persistence (state lives in memory for the process
lifetime only).

---

## 6. Analytics

### `analytics/metrics.py`

```python
from src.analytics.metrics import compute_metrics, export_trades_csv, save_equity_curve_chart

metrics = compute_metrics(result.trades, result.equity_curve["Equity"])
# {total_trades, win_rate, expectancy, profit_factor,
#  sharpe_ratio, sortino_ratio, max_drawdown_pct, cagr_pct}

export_trades_csv(result.trades, "results/trades.csv")
save_equity_curve_chart(result.equity_curve["Equity"], "results/equity_curve.png")
```

### `analytics/llm_reporter.py`

```python
from src.analytics.llm_reporter import LLMReporter

reporter = LLMReporter()  # provider="ollama" by default, model="phi"
report_text = reporter.generate_report(metrics, result.trades, equity_curve)
```

Requires a local Ollama server (`ollama serve`, matching `src/agent.py`'s
existing usage) with the configured model pulled. If Ollama is unreachable,
`generate_report` returns a human-readable "LLM report unavailable: ..."
string rather than raising, so pipelines don't crash on a missing local
LLM. `provider="anthropic"`/`"openai"` are defined but raise
`NotImplementedError` - wire up the corresponding SDK in
`_call_anthropic`/`_call_openai` if/when needed.

### `analytics/console.py` - CLI output formatting

```python
from src.analytics.console import render_table, format_metrics_table

print(render_table(["Ticker", "Return"], [["AAPL", "+1.2%"]], title="RESULTS"))
print(format_metrics_table(metrics, title="PERFORMANCE METRICS"))  # compute_metrics() dict -> table
```

Pure stdlib string formatting - no `tabulate`/`rich` dependency. Use this
for any new CLI script's tabular output instead of hand-rolled `print`
alignment, so terminal output stays consistent across scripts.

**CLI logging convention:** root-level scripts (`backtest_donchian.py`,
`backtest_with_exits.py`) configure `logging.basicConfig(level=logging.INFO)`
and use `log.warning(...)`/`log.error(..., exc_info=True)` for skipped
tickers/failures instead of silent `print`/`continue`. Keep this at the
script layer, not inside `RiskManager` or the engines - those run per-bar
and per-trade, so per-call logging there would be noisy and would blur the
separation of concerns in §1.

---

## 7. Testing Conventions

- Every new module gets a matching `tests/test_<module>.py`.
- Strategies: synthetic OHLCV fixtures, schema validated via
  `BaseStrategy.validate_output`.
- `RiskManager`: table-driven tests over all three SL/TP types plus edge
  cases (zero risk-per-share, long vs short sign correctness).
- Engines: run a real strategy (`MovingAverageCross`) over a synthetic
  trending fixture and assert the result shape; for `ForwardTester`, a
  scripted stub strategy (see `tests/test_forward_tester.py`) exercises the
  state machine (entry / SL exit / TP exit) deterministically.
- `analytics/llm_reporter.py`: mock `requests.post` - tests must never
  require a live Ollama server.
- Run the full suite with `pytest tests/ -v --cov=backend` before considering
  work complete.

## 8. Code Review Workflow

Before merging any change into `main` - whether written by a human or an AI
agent - invoke the `code-reviewer` subagent (`.claude/agents/code_reviewer.md`)
on the diff:

```
git diff main...HEAD
```

Pass that diff to the subagent (in Claude Code: the `Agent` tool with
`subagent_type: "code-reviewer"`, or `claude --agent code-reviewer` from the
CLI). Its brief is to report findings, not apply them, keeping review
separate from mutation.

Note that this separation is a convention, not a sandbox: the reviewer's
toolset is `Read`, `Grep`, `Glob`, `Bash`, and `Bash` can write. It may
legitimately mutate the tree while probing - e.g. flipping an index to check
whether a test actually fails - so after a review run, confirm the working
tree is clean (`git status --porcelain backend/ tests/`) before trusting the
diff you are about to merge.

It checks typing/contract compliance against `BaseStrategy` and
`RiskManager`, test coverage for new/changed logic, edge-case math (division
by zero, ATR = 0, empty DataFrames, NaN propagation through indicator
warm-up windows), and performance red flags (row-wise `.apply()`/`.iterrows()`
on OHLCV data instead of vectorized ops).

Address every `BLOCKER` and `WARNING` finding it reports, or leave a comment
in the PR/commit explaining why a finding is being consciously dismissed.
`NOTE` findings are informational and don't block a merge. This applies to
both human and AI-driven contributions - it's the expected step between
"tests pass" and "push to `main`," not a replacement for §7's test suite.
