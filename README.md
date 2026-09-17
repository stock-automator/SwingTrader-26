# Stock Data & Swing Trading Research System

A Python-based stock market data and quantitative research system for building, testing, and validating systematic swing-trading strategies across a large stock universe.

---

# How to Use This Repository

This section is the practical quick-start. For the underlying engineering
contracts (strategy signal schema, risk sizing, engine internals) see
`AGENTS.md` - that's the reference future strategies and AI coding agents
should follow.

## Setup

```bash
pip install -r requirements.txt
```

## Run the test suite

```bash
pytest tests/ -v --cov=src
```

## Run a backtest (full historical replay)

```python
import pandas as pd
from src.core.risk import RiskManager
from src.engine.backtester import run_backtest
from src.strategies.donchian_breakout import DonchianBreakout
from src.analytics.metrics import compute_metrics, export_trades_csv, save_equity_curve_chart

df = pd.read_parquet("data/raw/AAPL.parquet")
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.droplevel(1)   # cached files store (field, ticker) columns
df = df.sort_index()

risk_manager = RiskManager(account_equity=5000, risk_per_trade_pct=0.02)
result = run_backtest(DonchianBreakout(), df, risk_manager, commission=0.001, slippage_pct=0.0005)

print(result.stats)                                          # Sharpe, Return %, # Trades, ...
metrics = compute_metrics(result.trades, result.equity_curve["Equity"])
print(metrics)                                                # win_rate, expectancy, cagr_pct, ...

export_trades_csv(result.trades, "results/trades.csv")
save_equity_curve_chart(result.equity_curve["Equity"], "results/equity_curve.png")
```

Two ready-to-run CLI scripts wrap this for the cached ticker universe:

```bash
python backtest_donchian.py       # summary return/Sharpe/drawdown across 20 cached tickers
python backtest_with_exits.py     # per-trade entry/exit detail across 10 cached tickers
```

## Run a forward test (bar-by-bar paper trading)

```python
from src.engine.forward_tester import ForwardTester
from src.core.risk import RiskManager
from src.strategies.moving_average_cross import MovingAverageCross

tester = ForwardTester(
    strategy=MovingAverageCross(),
    risk_manager=RiskManager(account_equity=5000),
    symbol="AAPL",
    lookback=252,
)

for _, bar in df.iterrows():
    event = tester.step(bar)      # {'timestamp', 'action': 'ENTRY'|'EXIT'|'HOLD', ...}

print(tester.get_trade_log())     # closed trades
print(tester.get_open_positions())
print(tester.equity_curve)
```

## Generate a plain-English performance report

Requires a local Ollama server (`ollama serve`) with a model pulled (default `phi`).
If Ollama isn't reachable, this returns a friendly "unavailable" string instead of raising.

```python
from src.analytics.llm_reporter import LLMReporter

reporter = LLMReporter()  # provider="ollama" by default
report = reporter.generate_report(metrics, result.trades, result.equity_curve["Equity"])
print(report)
```

## Run the dashboard

```
streamlit run run_ui.py
```

Three tabs, all backed by the existing `src/core` / `src/engine` / `src/journal`
pipeline (no logic is duplicated between the CLI scripts and the dashboard):

- **Daily Signal Scanner** - pick a strategy, scan `config/watchlist.txt` for
  the latest-bar signals. `BUY` rows carry a resolved
  entry/stop-loss/take-profit and share count; `EXIT LONG` rows are unsized,
  because the engines are long-only and a bearish signal closes an open long
  rather than opening a short. Tickers that can't be scanned (no cached data,
  short history, unsizable signal) are counted in an expander instead of
  being dropped silently.
- **Interactive Backtester** - pick a strategy, symbol, and date range; view
  the equity curve plus Sharpe/drawdown/win-rate metrics.
- **Trade Journal & Analytics** - view `data/trades_live.csv` and profit
  attribution by exit reason (SL/TP1/TP2/TIMEOUT/MANUAL).

## Adding a new strategy

See `AGENTS.md` §3 - subclass `BaseStrategy` in `src/strategies/`, implement
`generate_signals`, copy `tests/test_moving_average_cross.py` as your test
template. No registry to update; strategies are passed to the engine directly.

## Directory map

```text
src/
├── strategies/       Signal generation. BaseStrategy contract + concrete
│                      strategies (DonchianBreakout, MovingAverageCross).
├── core/              Risk sizing. RiskManager resolves SL/TP and position
│                      size - the only module that knows about account equity.
├── engine/            Execution. backtester.py (full historical replay via
│                      backtesting.py) and forward_tester.py (bar-by-bar
│                      paper trading).
├── analytics/         Reporting. metrics.py (Sharpe/Sortino/drawdown/CAGR +
│                      equity chart), console.py (ASCII table rendering),
│                      llm_reporter.py (plain-English report via Ollama).
├── journal/           Trade journal persistence and analytics (executor.py).
├── ml/                Signal classifier experiments (signal_classifier.py).
├── data/              Historical data download/update/regime detection.
└── utils/             Standalone daily trade-signal scanning script.

tests/                 One test file per src/ module; test_moving_average_cross.py
                        is the template to copy for a new strategy.
config/                watchlist.txt (ticker universe) and trading_config.json.
data/raw/               Cached OHLCV parquet files, one per ticker (gitignored).
analysis/               One-off research scripts from the SuperTrend+RSI study (§9-15).
results/                Generated CSVs/PNGs from backtests (gitignored, not tracked).
backtest_donchian.py    CLI: Donchian summary across the cached universe.
backtest_with_exits.py  CLI: Donchian per-trade entry/exit detail.
AGENTS.md               Engineering contract for adding strategies - read this first.
```

The project started as a Yahoo Finance historical-data download agent and has evolved into the **data and backtesting foundation for a systematic momentum/swing-trading system**.

The system is designed around:

* A large stock universe (~500+ tickers)
* Cached historical market data
* Reproducible backtesting
* Realistic transaction costs and slippage
* Position sizing and risk controls
* Out-of-sample validation
* Strategy comparison
* Walk-forward / regime analysis
* Eventually, paper trading and small-scale live trading
* AI analysis only after a robust mechanical strategy has been identified

---

# 1. Project Objective

The goal is **not** to find a strategy that produces the largest historical return.

The goal is to identify a strategy that demonstrates a **repeatable edge** that survives:

1. Historical backtesting
2. Transaction costs
3. Slippage
4. Position sizing
5. Drawdown analysis
6. Different market regimes
7. Out-of-sample data
8. Walk-forward testing
9. Paper trading
10. Small live deployment

The intended trading style is:

> **Systematic momentum / swing trading across a diversified stock universe.**

The current target trading account is approximately **£5,000**, with risk management being more important than maximizing raw returns.

A historical backtest is considered evidence for further investigation, **not proof of future profitability**.

---

# 2. Current Project Status

## Data infrastructure

The data infrastructure is operational.

Current dataset:

* Watchlist: **514 tickers**
* Successfully prepared for research: **502 stocks**
* Historical range used in the latest research: **2015-01-02 → 2026-09-15**
* Universal trading calendar: **2,942 trading dates**
* Data stored locally as Parquet
* Historical data can therefore be reused without repeatedly downloading it

The cached dataset is now the foundation for strategy research.

---

# 3. Architecture

The project is moving toward the following architecture:

```text
                  STOCK UNIVERSE
                       │
                       ▼
              ┌─────────────────┐
              │ Cached Market   │
              │ Data (Parquet)  │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Data Validation │
              │ & Preparation   │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Strategy Engine │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Position Sizing │
              │ & Risk Engine   │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Backtest Engine │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ OOS Validation  │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Stress / Regime │
              │ Analysis        │
              └────────┬────────┘
                       │
                       ▼
                PAPER TRADING
                       │
                       ▼
                 MICRO LIVE
                       │
                       ▼
                   £5,000
```

---

# 4. Data Download Agent

The original `DownloadAgent` remains responsible for collecting and maintaining historical data.

## Features

### Intelligent rate limiting

Detects Yahoo Finance rate-limit conditions and can:

* Retry requests
* Delay between downloads
* Switch VPN locations when configured
* Resume interrupted downloads

### LLM-powered VPN selection

Ollama can be used to help select a VPN country when rate limiting occurs.

This is an infrastructure feature only.

The LLM is **not currently used to generate trading decisions**.

### Checkpoint system

Long downloads can be interrupted and resumed without starting from the beginning.

### Data validation

Downloaded files are checked for:

* Minimum row count
* Valid OHLC data
* Empty responses
* Corrupted Parquet files

### Hash verification

SHA256 hashes can be generated to verify file integrity.

---

# 5. Cached Historical Data

Historical data is stored locally.

```text
data/
└── raw/
    ├── AAPL.parquet
    ├── MSFT.parquet
    ├── NVDA.parquet
    ├── AMD.parquet
    └── ...
```

This is important because backtests should **not repeatedly download the same historical data**.

Once data is cached:

```text
Download once
     ↓
Store locally
     ↓
Backtest repeatedly
     ↓
Change strategy
     ↓
Backtest again
```

This makes large-scale strategy experimentation much faster and more reproducible.

---

# 6. Data Preparation

The backtesting system standardizes each stock into:

* Open
* High
* Low
* Close
* Volume
* Datetime index

It also:

* Removes duplicate dates
* Handles timezone information
* Sorts chronologically
* Restricts the research period
* Handles missing data
* Creates a universal trading calendar

The latest research uses a common calendar across the 502 usable stocks.

This prevents individual ticker calendars from creating inconsistent portfolio simulations.

---

# 7. Current Backtesting Framework

The backtesting framework supports:

* Multiple stocks
* Multiple simultaneous positions
* Next-day execution
* Commission
* Slippage
* Position sizing
* Equity tracking
* Trade logging
* Maximum drawdown
* Sharpe ratio
* Win rate
* Profit factor
* Average win/loss
* Holding periods
* Loss streaks
* Regime analysis
* Year-by-year analysis

The framework is designed so different strategies can use the same infrastructure.

This is important:

> **The strategy changes; the testing methodology should remain consistent.**

---

# 8. Current Risk Model

The intended portfolio framework is approximately:

```text
Initial capital:       £5,000
Risk per trade:        1%
Maximum positions:     5–10
Maximum position:      ~25%
Commission:            0.10%
Slippage:              0.05%
```

However, an important issue was discovered during the RSI research.

The previous RSI implementation used:

```text
5% stop distance
```

for position sizing, but **did not actually implement a hard 5% stop-loss exit**.

Therefore:

> The previous "1% risk per trade" should not be interpreted as a true maximum-loss guarantee.

Future strategies should use an actual stop mechanism, preferably an **ATR-based stop**, so that position sizing and actual trade risk are connected.

---

# 9. First Strategy Research: SuperTrend + RSI

The first major strategy experiment combined:

* SuperTrend
* RSI
* Position sizing
* Portfolio constraints
* Commission
* Slippage

The baseline SuperTrend-only strategy was compared with multiple RSI configurations.

The RSI search tested:

```text
RSI Period: 14

Overbought:
60
65
70
75
80
85
90

Oversold:
30
35
40
45
```

The SuperTrend baseline used:

```text
RSI Overbought = 100
RSI Oversold   = 0
```

which effectively disabled the RSI filter.

---

# 10. RSI Optimization Results

The best in-sample configuration was:

```text
RSI Overbought: 60
RSI Oversold:   45
```

Historical result over the development/full dataset:

```text
Initial capital:    £5,000
Final equity:        £9,766
Return:              +95.32%
Max drawdown:        37.07%
Sharpe:              0.40
Trades:              2,048
Win rate:            71.29%
Profit factor:       1.11
```

At first glance this looked promising.

However, the result was **not accepted as a production strategy**.

The reason was that in-sample performance alone is insufficient.

---

# 11. RSI Strategy Diagnostic Analysis

The 60/45 strategy was analysed in detail.

Important findings:

```text
Average winner:      +£33.11
Average loser:       -£74.12

Payoff ratio:        0.45

Median holding:      4 days
Average holding:     8.9 days

Maximum drawdown:    37.07%

Maximum consecutive losses: 7
```

The strategy had a relatively high win rate but relatively large losing trades.

The system also experienced a prolonged drawdown beginning in late 2021 and extending through 2022.

The 2022 bear-market period was particularly weak.

---

# 12. Out-of-Sample Validation

The most important test was then performed.

The strategy was frozen at:

```text
RSI OB = 60
RSI OS = 45
```

The development period was:

```text
2015–2022
```

The strict out-of-sample period was:

```text
2023–2026
```

The OOS period was not used to select the RSI parameters.

---

# 13. RSI OOS Result

Development:

```text
2015–2022

Return:          +23.22%
Final equity:    £6,160.77
Max drawdown:    31.98%
Sharpe:          0.25
Profit factor:   1.05
Trades:          1,954
```

Strict OOS:

```text
2023–2026

Return:          +10.23%
Final equity:    £5,511.54
Max drawdown:    23.32%
Sharpe:          0.26
Profit factor:   1.04
Trades:          1,046
Win rate:        69.02%
```

OOS trade characteristics:

```text
Average winner:      +£17.62
Average loser:       -£37.68
Payoff ratio:         0.47

Average holding:      8.7 days
Maximum holding:      72 days
Maximum loss streak:  10
```

---

# 14. RSI Strategy Conclusion

The RSI strategy was **not considered robust enough to continue optimizing**.

The important lesson was:

```text
In-sample:
+95.32%

OOS:
+10.23%
```

The large reduction in performance indicates that the original optimization result was not a sufficiently reliable estimate of future performance.

The OOS profit factor of approximately:

```text
1.04
```

also indicates only a very thin edge.

The conclusion is therefore:

> **Do not continue fine-tuning RSI parameters such as 59/44, 60/44, 61/45, etc.**

Doing so would risk fitting the strategy to historical noise rather than discovering a robust edge.

The RSI/SuperTrend strategy is now considered a **research result / low-priority candidate**, rather than the foundation of the final trading system.

---

# 15. What We Learned

The RSI experiment changed the research methodology.

We should no longer ask:

> "Which parameter produces the highest historical return?"

Instead we should ask:

> "Does a simple strategy demonstrate a meaningful edge that survives unseen data and realistic costs?"

The research process should therefore be:

```text
Strategy idea
     ↓
Simple baseline
     ↓
Backtest
     ↓
Risk / drawdown analysis
     ↓
Development period
     ↓
LOCK strategy
     ↓
Untouched OOS
     ↓
Stress / regime testing
     ↓
Paper trading
```

Only after a strategy passes these stages should optimization be considered.

---

# 16. Current Strategy Search

The next strategy family will move toward **momentum and breakout trading**.

This is more closely aligned with the intended trading style than the previous RSI oscillator approach.

The first candidate strategy is:

## Donchian Momentum Breakout

Initial version:

```text
20-day breakout
+
50-day EMA trend filter
+
3-month momentum
+
relative strength
+
volume confirmation
+
ATR-based stop
```

Conceptually:

```text
                     STOCK
                       │
                       ▼
                20-DAY BREAKOUT?
                       │
                 YES ──┴── NO
                  │          │
                  ▼          └── Ignore
              ABOVE 50 EMA?
                  │
             YES ─┴─ NO
              │       │
              ▼       └── Ignore
        POSITIVE MOMENTUM?
              │
         YES ─┴─ NO
          │         │
          ▼         └── Ignore
      VOLUME CONFIRMATION
          │
          ▼
       BUY NEXT OPEN
          │
          ▼
     ATR POSITION SIZE
          │
          ▼
      TRAILING STOP
```

Initial parameters should remain simple and should **not** be heavily optimized initially.

Proposed starting configuration:

```text
Universe:              ~500 stocks

Breakout:              20 trading days
Trend filter:          50 EMA
Momentum:              3 months
Volume:                20-day average
Stop:                  2.5 × ATR

Risk per trade:        1%
Maximum positions:     5–10

Commission:            0.10%
Slippage:              0.05%

Development:           2015–2022
OOS:                   2023–2026
```

The purpose of this first test is to determine whether the **strategy concept itself** has an edge.

We should not optimize dozens of parameters before this question is answered.

---

# 17. Potential Future Strategy Families

If the Donchian breakout does not demonstrate sufficient robustness, the next candidates can include:

### Strategy 2 — Trend Continuation

```text
20 EMA > 50 EMA
50 EMA > 200 EMA
Price > 20 EMA
Positive momentum
Pullback + continuation entry
```

### Strategy 3 — Relative Strength Momentum

Rank the stock universe using:

```text
3-month return
6-month return
12-month return
```

combined with:

```text
Price > 50 EMA
Price > 200 EMA
Liquidity filter
```

Then select the strongest candidates.

### Strategy 4 — Volatility Breakout

Look for:

```text
Volatility contraction
+
volume expansion
+
price breakout
+
trend confirmation
```

These strategies should be tested using the same backtesting and validation infrastructure.

---

# 18. Strategy Evaluation Criteria

A strategy should not be selected based on return alone.

The research reports should examine:

### Return

```text
Total return
Annualized return
```

### Risk

```text
Maximum drawdown
Drawdown duration
Volatility
Worst year
Worst month
```

### Risk-adjusted performance

```text
Sharpe
Sortino
Calmar
```

### Trade quality

```text
Number of trades
Win rate
Profit factor
Average winner
Average loser
Payoff ratio
Maximum loss streak
Average holding period
```

### Robustness

```text
Development performance
OOS performance
Different market regimes
Different years
Parameter sensitivity
```

A strategy with a lower historical return but a stronger and more stable OOS edge may be more useful for further research than a strategy with spectacular in-sample returns.

No strategy should be considered "production ready" from backtesting alone.

---

# 19. Market Regime Testing

Strategies should be tested across different market environments.

Important periods include:

```text
2015–2019    Normal / mixed markets
2020         COVID crash and recovery
2021         Strong bull market
2022         Bear market
2023         Recovery
2024         Bull / mixed
2025         Recent market conditions
2026         Current period
```

A strategy that only works in one environment should be treated cautiously.

---

# 20. Survivorship Bias

The current ~500-stock universe must be treated carefully.

A present-day watchlist does not necessarily represent the stocks that existed in the universe during every historical period.

For example:

```text
Today's universe
        ↓
Backtest 2015
```

can introduce survivorship bias because companies that failed, were acquired, or left an index may be missing.

Therefore, future research should consider historical constituents where possible.

A survivorship-bias-aware dataset or historical constituent universe should eventually be added for more rigorous validation.

---

# 21. Data Quality Considerations

Historical data is sourced primarily from Yahoo Finance through `yfinance`.

Potential issues include:

* Missing data
* Delisted securities
* Corporate actions
* Ticker changes
* Survivorship bias
* Data revisions
* API availability
* Adjusted vs unadjusted prices

Results should therefore be interpreted as research results rather than institutional-grade historical simulation.

---

# 22. AI / LLM Integration

AI is intentionally **not the first layer of the trading system**.

The intended architecture is:

```text
Mechanical Strategy
        ↓
Generate Candidates
        ↓
Risk Filters
        ↓
Optional AI Analysis
        ↓
Final Candidate Ranking
```

Potential future tools include:

* TradingAgents
* Ollama/local LLMs
* Hermes Agent
* OmniRoute
* Other open-source agent frameworks

AI should only be added if it produces a **measurable improvement** over the mechanical baseline.

For example:

```text
Mechanical baseline:
PF = 1.30

Mechanical + AI:
PF = 1.38
```

would justify further investigation.

Simply adding an LLM does not constitute an improvement.

---

# 23. Cost Philosophy

The system is designed to minimize recurring costs.

Preferred approach:

```text
Open-source software
+
Cached data
+
Free/low-cost market data
+
Local computation
+
Local LLMs where practical
```

Paid data providers should only be introduced when the improvement in data quality materially improves the research or trading system.

---

# 24. Data Update System

Existing Parquet files can be incrementally updated.

The update system:

* Reads the latest available date
* Requests newer data
* Merges old and new data
* Removes duplicate dates
* Writes updated Parquet
* Maintains the local historical dataset

Typical workflow:

```bash
python agent.py
```

for initial collection and:

```bash
python agent.py --update
```

for updates, depending on the current implementation.

---

# 25. Installation

## Requirements

Python 3.8+

Core packages:

```bash
pip install pandas yfinance pyarrow requests
```

Additional research packages may include:

```bash
pip install numpy scipy scikit-learn
```

and strategy-specific packages as required.

---

# 26. Initial Data Download

Create a watchlist:

```text
AAPL
MSFT
GOOGL
AMZN
NVDA
AMD
...
```

Then run:

```bash
python agent.py
```

The data will be stored under:

```text
data/raw/
```

---

# 27. Updating Data

Run:

```bash
python agent.py --update
```

The system should update existing files rather than downloading the entire historical dataset again.

---

# 28. Verification

Data can be verified through the agent:

```python
agent = DownloadAgent()

results = agent.verify_all_data()
```

Results include information such as:

* Status
* Number of rows
* Date range
* File size
* SHA256 hash

---

# 29. Testing

Run:

```bash
pytest tests/ -v
```

Coverage:

```bash
pytest tests/ -v --cov=src --cov-report=html
```

This covers the data agent (`tests/test_agent.py`, `tests/test_journal.py`)
as well as the strategy/risk/engine/analytics layer added in `src/`
(`tests/test_donchian.py`, `tests/test_moving_average_cross.py`,
`tests/test_risk.py`, `tests/test_backtester.py`,
`tests/test_forward_tester.py`, `tests/test_metrics.py`,
`tests/test_llm_reporter.py`). See `AGENTS.md` §7 for testing conventions
when adding new modules.

---

# 30. Project Development Rules

The following rules should be followed going forward.

### Rule 1 — Do not optimize blindly

Do not search hundreds of parameter combinations simply to find the highest historical return.

### Rule 2 — Protect the OOS period

Once an OOS period has been defined, it should not be used to select parameters.

### Rule 3 — Keep the first strategy simple

Start with a small number of understandable rules.

### Rule 4 — Use realistic costs

Always include:

```text
Commission
Slippage
```

### Rule 5 — Implement real risk controls

Position sizing and actual stop-loss logic must correspond.

### Rule 6 — Analyse drawdowns

Do not focus only on total return.

### Rule 7 — Compare against a benchmark

A strategy should be compared with appropriate passive benchmarks.

### Rule 8 — Check different regimes

A strategy should not depend entirely on one particular market environment.

### Rule 9 — Avoid unnecessary AI

AI should enhance an existing edge, not replace one.

### Rule 10 — Backtest → OOS → Paper → Micro Live

Never jump directly from an attractive backtest to full capital.

---

# 31. Intended Deployment Path

The eventual deployment process is:

```text
                     RESEARCH
                        │
                        ▼
                  BACKTEST
                        │
                        ▼
                 OOS VALIDATION
                        │
                        ▼
                 STRESS TESTING
                        │
                        ▼
                  PAPER TRADING
                        │
                        ▼
                   £250 LIVE
                        │
                        ▼
                  £1,000 LIVE
                        │
                        ▼
                  £2,500 LIVE
                        │
                        ▼
                  £5,000 LIVE
```

Each stage should have predefined risk and performance checks.

---

# 32. Current Status Summary

## Completed

* [x] Large stock universe
* [x] Historical Yahoo Finance data collection
* [x] Local Parquet caching
* [x] Incremental data updates
* [x] Data validation
* [x] Universal trading calendar
* [x] Multi-stock backtesting
* [x] Transaction costs
* [x] Slippage
* [x] Position sizing
* [x] SuperTrend strategy
* [x] RSI optimization
* [x] Detailed RSI diagnostics
* [x] Development/OOS validation
* [x] Market-regime analysis
* [x] Identification of RSI overfitting / weak OOS edge

## Current conclusion

The RSI/SuperTrend strategy is **not being taken forward as the primary trading strategy**.

The +95.32% historical result was not sufficiently supported by the +10.23% OOS result.

The system should therefore **stop optimizing RSI parameters** and move to a different strategy family.

## Current research target

**Momentum / breakout strategies.**

First candidate:

```text
20-day Donchian breakout
+
50 EMA trend filter
+
3-month momentum
+
relative strength
+
volume confirmation
+
real ATR-based stop
```

The first objective is not to maximize return.

The objective is to determine whether this strategy produces a **stronger and more robust OOS edge than the RSI/SuperTrend strategy**.

---

# 33. Long-Term Vision

The eventual system is intended to become:

```text
             500+ STOCK UNIVERSE
                     │
                     ▼
              MARKET DATA
                     │
                     ▼
            STRATEGY SCANNERS
                     │
                     ▼
          MOMENTUM / TREND SIGNALS
                     │
                     ▼
              RISK ENGINE
                     │
                     ▼
         ┌──────────────────────┐
         │ Optional AI Research │
         │ / Candidate Analysis │
         └──────────┬───────────┘
                    │
                    ▼
              TRADE SIGNAL
                    │
                    ▼
            PORTFOLIO CONTROL
                    │
                    ▼
             PAPER TRADING
                    │
                    ▼
              LIVE EXECUTION
```

The system should remain modular so that strategies, risk models, data sources, AI components, and execution systems can be replaced independently.

The fundamental principle is:

> **Find a robust mechanical edge first. Add complexity only when testing demonstrates that the complexity improves the result.**
