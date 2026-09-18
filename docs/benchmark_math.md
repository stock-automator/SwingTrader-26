# The $1,000 Benchmark: How the Math Works

This is the plain-language companion to `backend/app/quant/backtest.py`.
Every formula below is implemented there (`summarize_curve`,
`relative_metrics`, `align_curves`, `rebase`) — this page explains *why*
each step exists, not a different or simplified version of it. If this page
and the code ever disagree, the code is right; fix this page.

The question the platform exists to answer: **if you'd put $1,000 into this
strategy over this window, what would you have now — versus putting the
same $1,000 into the ticker itself (buy & hold), or into SPY?**

## Why a shared baseline and a shared date index

Three dollar equity curves are built for every backtest run:

- `strategy` — the signal-driven backtest replay (`quant/engine.py`)
- `buy_and_hold` — the target ticker(s), bought once at the window's first
  close and held
- `spy` — SPY, bought once at the window's first close and held

Two things have to be true before these three curves can be compared
honestly:

1. **They must cover the same dates.** A strategy that only traded 2022
   compared against an SPY curve spanning 2020–2024 would show a dollar gap
   that's partly just a longer calendar, not better performance.
   `align_curves` fixes this by treating the strategy's own date index as
   authoritative, reindexing every benchmark onto it, and forward-filling a
   benchmark's holiday/halt gaps (the last close is the honest mark for a
   day nothing traded). A benchmark that starts *after* the strategy
   truncates the whole comparison window, because there's no defensible
   price to mark it at before its first quote.

2. **They must start at the same dollar value.** Truncating the window in
   step 1 shifts each benchmark curve off whatever its original anchor was.
   `rebase` scales every column so all three start at exactly
   `initial_capital` (default **$1,000**) on the first shared date. Only
   after both steps does "$1,000 became $X" mean the same thing across all
   three curves.

## Buy & hold curve construction

`buy_and_hold_curve(prices, initial_capital)` computes
`prices / prices.iloc[0] * initial_capital` — i.e. it assumes **fractional
shares**. This is deliberate: forcing whole shares on a $1,000 baseline
would leave an arbitrary cash remainder (on a $400 stock, 20% of the
account sitting idle for reasons that have nothing to do with the
strategy) and would understate the benchmark. The strategy curve, by
contrast, *does* use whole-share sizing via `RiskManager` — because real
trade execution can't buy fractional shares — so the benchmark is
deliberately the more generous, "idealized" version of itself.

When more than one ticker is requested, each is its own equally-weighted
sleeve (`initial_capital / n`, no capital shared between sleeves), and the
sleeve curves are summed. This is a floor on what a real pooled portfolio
would do, not an estimate of it — sleeves can't fund each other's trades.

## Absolute curve stats (`summarize_curve`)

For each of the three curves independently:

- **Total return %**: `(final / initial - 1) * 100`.
- **CAGR %**, **Sharpe ratio**, **max drawdown %**: delegated to
  `analytics/metrics.compute_metrics` (the same function the CLI trade
  reports use) so these never drift between the API and any other report
  surface.
- **Volatility %**: annualised standard deviation of daily returns,
  `returns.std(ddof=1) * sqrt(252) * 100`. `ddof=1` (sample, not
  population, variance) is used consistently everywhere in this module.

252 is `TRADING_DAYS_PER_YEAR`, the daily-bars annualisation factor used
throughout.

## Relative metrics (`relative_metrics`) — strategy vs. one benchmark

Computed once for `strategy vs spy` and once for `strategy vs buy_and_hold`.
Both curves are first turned into daily return series and restricted to
their common index (they already share one after `align_curves`, so this is
mostly a no-op safety net).

Let `r_s` = strategy daily returns, `r_b` = benchmark daily returns,
`rf` = `risk_free_rate / 252` (daily risk-free rate). Excess returns:
`x_s = r_s - rf`, `x_b = r_b - rf`.

- **Beta**: `Cov(x_s, x_b) / Var(x_b)` (both `ddof=1`). Sensitivity of the
  strategy's excess returns to the benchmark's. `1.0` means it moved with
  the benchmark; `None` if the benchmark's variance is zero (a flat
  benchmark — beta is undefined, not zero).

- **Jensen's alpha (annualized %)**: with beta known,
  `daily_alpha = mean(x_s) - beta * mean(x_b)`, then
  `alpha_annual_pct = daily_alpha * 252 * 100`. This is the return the
  strategy produced that its benchmark *exposure* (as captured by beta)
  does not explain — the actual "did the strategy add value beyond just
  being correlated with the market" number.

- **Sharpe ratio (strategy)** and **benchmark Sharpe ratio**: both computed
  by the same helper (`_annualized_sharpe`), so they're on an identical
  convention — `mean(excess) / std(raw returns, ddof=1) * sqrt(252)`. Note
  the denominator uses the *raw* return standard deviation, not the excess
  return's — matching `analytics/metrics._equity_curve_metrics` exactly, so
  the API and the CLI report the same Sharpe for the same curve.

- **Correlation** and **R²**: `strategy_returns.corr(benchmark_returns)`
  and its square. A low R² means the alpha/beta above explain little of
  the strategy's behavior — take them with more caution.

- **Active returns**: `r_s - r_b`, day by day.
  - **Tracking error %**: `std(active, ddof=1) * sqrt(252) * 100` — how
    much the strategy's daily return deviates from the benchmark's, in
    either direction.
  - **Information ratio**: `mean(active) / std(active, ddof=1) * sqrt(252)`
    — risk-adjusted skill *relative to the benchmark*, which is what
    "Sharpe vs SPY" informally means in conversation. Requires tracking
    error to be nonzero.

- **Excess return %**: `strategy_total_return_pct - benchmark_total_return_pct`.
  Because both curves start from the same `initial_capital`, this is also
  literally the dollar gap between them at the end of the window — the
  simplest possible answer to "did this beat SPY."

## Every value is finite or `None` — never `NaN`/`Infinity`

`json.dumps` emits bare `NaN` and `Infinity` tokens for non-finite floats,
which is invalid JSON per spec and `JSON.parse` rejects it in the browser.
Every metric that can be genuinely undefined mathematically — beta against
a benchmark that never moved, a Sharpe ratio with zero variance, a profit
factor with no losing trades — is passed through `_finite()`, which maps
any non-finite or unparseable value to `None` before it reaches the API
response. The frontend should treat `null` on any of these fields as "not
computable for this window," not as zero.

## Where this is tested

Per the module docstring in `backend/app/quant/backtest.py`, the in-house
Sharpe computed here is meant to be cross-checked against `quantstats`' own
Sharpe on the same curve (`tests/test_benchmark.py`), so the closed-form
math above doesn't silently drift from an independent implementation.
`save_tearsheet` uses `quantstats` for the full HTML report (presentation
only) but every number the API itself returns comes from the formulas on
this page, not from quantstats — a `quantstats` version bump can't move a
headline figure.
