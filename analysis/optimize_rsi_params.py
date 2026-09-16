#!/usr/bin/env python3

"""
SUPERTrend + RSI Portfolio Optimizer v4

Purpose:
    Backtest SuperTrend + RSI entry/exit combinations across a stock universe.

Key features:
    - Uses cached Parquet data from data/raw/
    - £5,000 starting portfolio
    - 1% nominal risk per trade
    - Maximum 10 simultaneous positions
    - Maximum 25% of equity per position
    - Commission + slippage
    - Next-bar execution
    - Wilder RSI
    - Correct SuperTrend implementation
    - Correct Profit Factor
    - Maximum Drawdown
    - Sharpe Ratio
    - Buy & Hold benchmark
    - SuperTrend-only baseline
    - Multiprocessing across configurations
    - One universal progress bar
    - Signal sanity check
    - CSV output files

IMPORTANT:
    This is a research backtest, not a live trading system.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from tqdm import tqdm

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path("data/raw")
WATCHLIST_FILE = Path("watchlist.txt")
OUTPUT_DIR = Path("results")

START_DATE = "2015-01-01"

INITIAL_CAPITAL = 5_000.0

# Portfolio risk controls
RISK_PER_TRADE = 0.01
MAX_POSITIONS = 10
MAX_POSITION_PCT = 0.25

# Trading costs
COMMISSION_RATE = 0.0010
SLIPPAGE_RATE = 0.0005

# Used only for position sizing.
# NOTE:
# There is currently no hard 5% stop-loss exit.
# Therefore this is a sizing assumption, not a guaranteed 1% maximum loss.
STOP_DISTANCE_PCT = 0.05

# Indicators
ATR_PERIOD = 10
ATR_MULTIPLIER = 3.0
RSI_PERIOD = 14

# RSI optimisation grid
RSI_OVERBOUGHT_VALUES = [60, 65, 70, 75, 80, 85, 90]
RSI_OVERSOLD_VALUES = [30, 35, 40, 45]

# Baseline configuration
BASELINE_RSI_OB = 100
BASELINE_RSI_OS = 0

# Annualisation
TRADING_DAYS_PER_YEAR = 252


# =============================================================================
# DATA LOADING
# =============================================================================


def load_watchlist() -> List[str]:
    """Load ticker symbols from watchlist.txt."""

    if not WATCHLIST_FILE.exists():
        raise FileNotFoundError(f"Watchlist not found: {WATCHLIST_FILE.resolve()}")

    tickers = []

    with WATCHLIST_FILE.open("r", encoding="utf-8") as file:
        for line in file:
            ticker = line.strip().upper()

            if not ticker:
                continue

            if ticker.startswith("#"):
                continue

            tickers.append(ticker)

    # Remove duplicates while preserving order
    tickers = list(dict.fromkeys(tickers))

    if not tickers:
        raise RuntimeError("watchlist.txt contains no tickers.")

    return tickers


def load_from_cache(ticker: str) -> pd.DataFrame | None:
    """
    Load one ticker from cached Parquet data.

    Expected location:
        data/raw/TICKER.parquet
    """

    path = DATA_DIR / f"{ticker}.parquet"

    if not path.exists():
        return None

    try:
        df = pd.read_parquet(path)

        if df.empty:
            return None

        # Flatten MultiIndex columns if necessary
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [
                column[0] if isinstance(column, tuple) else column
                for column in df.columns
            ]

        # Standardise column names
        rename_map = {}

        for column in df.columns:
            name = str(column).strip()

            if name.lower() == "open":
                rename_map[column] = "Open"
            elif name.lower() == "high":
                rename_map[column] = "High"
            elif name.lower() == "low":
                rename_map[column] = "Low"
            elif name.lower() == "close":
                rename_map[column] = "Close"
            elif name.lower() == "volume":
                rename_map[column] = "Volume"

        df = df.rename(columns=rename_map)

        required = ["Open", "High", "Low", "Close"]

        if not all(column in df.columns for column in required):
            return None

        # Make index datetime
        df.index = pd.to_datetime(df.index)

        # Remove timezone
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)

        # Sort
        df = df.sort_index()

        # Remove duplicate dates
        df = df[~df.index.duplicated(keep="last")]

        # Restrict start date
        df = df.loc[df.index >= START_DATE]

        if len(df) < ATR_PERIOD + RSI_PERIOD + 20:
            return None

        # Ensure numeric
        for column in required:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df = df.dropna(subset=required)

        if df.empty:
            return None

        return df

    except Exception:
        return None


# =============================================================================
# INDICATORS
# =============================================================================


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """
    Wilder ATR.

    Uses exponential smoothing with alpha=1/period.
    """

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    return atr


def calculate_rsi(df: pd.DataFrame, period: int = RSI_PERIOD) -> pd.Series:
    """
    Wilder RSI.
    """

    close = df["Close"]

    delta = close.diff()

    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    average_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    average_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = average_gain / average_loss

    rsi = 100.0 - (100.0 / (1.0 + rs))

    # Handle edge cases
    rsi = rsi.where(average_loss != 0, 100.0)

    rsi = rsi.where(average_gain != 0, 0.0)

    both_zero = (average_gain == 0) & (average_loss == 0)

    rsi = rsi.where(~both_zero, 50.0)

    return rsi


def calculate_supertrend(
    df: pd.DataFrame, atr_period: int = ATR_PERIOD, multiplier: float = ATR_MULTIPLIER
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Calculate SuperTrend.

    Returns:

        direction:
            +1 = bullish
            -1 = bearish

        signal:
            +1 = bullish reversal
            -1 = bearish reversal
             0 = no reversal
    """

    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    close = df["Close"].to_numpy(dtype=float)

    atr = calculate_atr(df, atr_period).to_numpy(dtype=float)

    n = len(df)

    upper = np.full(n, np.nan, dtype=float)

    lower = np.full(n, np.nan, dtype=float)

    supertrend = np.full(n, np.nan, dtype=float)

    direction = np.zeros(n, dtype=np.int8)

    signal = np.zeros(n, dtype=np.int8)

    valid_atr = np.where(~np.isnan(atr))[0]

    if len(valid_atr) == 0:
        return direction, signal

    # -------------------------------------------------------------------------
    # Initialise first valid ATR row
    # -------------------------------------------------------------------------

    first = int(valid_atr[0])

    hl2 = (high[first] + low[first]) / 2.0

    upper[first] = hl2 + multiplier * atr[first]

    lower[first] = hl2 - multiplier * atr[first]

    supertrend[first] = lower[first]

    direction[first] = 1

    # -------------------------------------------------------------------------
    # Main SuperTrend loop
    # -------------------------------------------------------------------------

    for i in range(first + 1, n):

        if np.isnan(atr[i]):
            continue

        hl2 = (high[i] + low[i]) / 2.0

        basic_upper = hl2 + multiplier * atr[i]

        basic_lower = hl2 - multiplier * atr[i]

        previous_upper = upper[i - 1]
        previous_lower = lower[i - 1]
        previous_close = close[i - 1]

        # Final upper band
        if (
            np.isnan(previous_upper)
            or basic_upper < previous_upper
            or previous_close > previous_upper
        ):
            upper[i] = basic_upper
        else:
            upper[i] = previous_upper

        # Final lower band
        if (
            np.isnan(previous_lower)
            or basic_lower > previous_lower
            or previous_close < previous_lower
        ):
            lower[i] = basic_lower
        else:
            lower[i] = previous_lower

        previous_direction = direction[i - 1]

        # ---------------------------------------------------------------------
        # Previous state was bullish
        # ---------------------------------------------------------------------

        if previous_direction == 1:

            if close[i] <= lower[i]:

                direction[i] = -1

                supertrend[i] = upper[i]

                signal[i] = -1

            else:

                direction[i] = 1

                supertrend[i] = lower[i]

        # ---------------------------------------------------------------------
        # Previous state was bearish
        # ---------------------------------------------------------------------

        elif previous_direction == -1:

            if close[i] >= upper[i]:

                direction[i] = 1

                supertrend[i] = lower[i]

                signal[i] = 1

            else:

                direction[i] = -1

                supertrend[i] = upper[i]

        # ---------------------------------------------------------------------
        # Unknown state
        # ---------------------------------------------------------------------

        else:

            if close[i] > lower[i]:

                direction[i] = 1

                supertrend[i] = lower[i]

            else:

                direction[i] = -1

                supertrend[i] = upper[i]

    return direction, signal


# =============================================================================
# PREPARE STOCK DATA
# =============================================================================


def prepare_stock(ticker: str) -> Tuple[str, Dict[str, np.ndarray]] | None:
    """
    Load and prepare one ticker.

    Returns a compact dictionary of NumPy arrays.
    """

    df = load_from_cache(ticker)

    if df is None:
        return None

    try:

        rsi = calculate_rsi(df, RSI_PERIOD)

        direction, signal = calculate_supertrend(df, ATR_PERIOD, ATR_MULTIPLIER)

        data = {
            "dates": df.index.to_numpy(),
            "open": df["Open"].to_numpy(dtype=np.float64),
            "high": df["High"].to_numpy(dtype=np.float64),
            "low": df["Low"].to_numpy(dtype=np.float64),
            "close": df["Close"].to_numpy(dtype=np.float64),
            "rsi": rsi.to_numpy(dtype=np.float64),
            "direction": direction,
            "signal": signal,
        }

        return ticker, data

    except Exception:
        return None


def prepare_all_data(tickers: List[str]) -> Dict[str, Dict[str, np.ndarray]]:
    """
    Prepare all stocks.
    """

    prepared = {}

    start_time = time.time()

    print()
    print("=" * 100)
    print("PREPARING STOCK DATA")
    print("=" * 100)

    for ticker in tqdm(tickers, desc="Preparing stocks", unit="stock"):

        result = prepare_stock(ticker)

        if result is None:
            continue

        ticker_name, data = result

        prepared[ticker_name] = data

    elapsed = time.time() - start_time

    print()
    print(f"Prepared {len(prepared):,} stocks " f"in {elapsed:.1f}s")

    return prepared


# =============================================================================
# UNIVERSAL CALENDAR
# =============================================================================


def create_calendar(data: Dict[str, Dict[str, np.ndarray]]) -> pd.DatetimeIndex:
    """
    Create a universal trading calendar containing
    every date appearing in the universe.
    """

    all_dates = set()

    for stock in data.values():

        for date in stock["dates"]:
            all_dates.add(pd.Timestamp(date))

    if not all_dates:
        raise RuntimeError("No dates found in prepared stock data.")

    calendar = pd.DatetimeIndex(sorted(all_dates))

    return calendar


def build_calendar_arrays(
    data: Dict[str, Dict[str, np.ndarray]], calendar: pd.DatetimeIndex
) -> Dict[str, Dict[str, np.ndarray]]:
    """
    Map every stock onto the universal calendar.
    """

    mapped = {}

    calendar_values = calendar.values

    for ticker, stock in tqdm(data.items(), desc="Mapping calendar", unit="stock"):

        n = len(calendar)

        open_array = np.full(n, np.nan, dtype=np.float64)

        close_array = np.full(n, np.nan, dtype=np.float64)

        rsi_array = np.full(n, np.nan, dtype=np.float64)

        signal_array = np.zeros(n, dtype=np.int8)

        direction_array = np.zeros(n, dtype=np.int8)

        dates = stock["dates"]

        positions = calendar.get_indexer(pd.DatetimeIndex(dates))

        valid = positions >= 0

        positions = positions[valid]

        if len(positions) == 0:
            continue

        open_array[positions] = stock["open"][valid]

        close_array[positions] = stock["close"][valid]

        rsi_array[positions] = stock["rsi"][valid]

        signal_array[positions] = stock["signal"][valid]

        direction_array[positions] = stock["direction"][valid]

        mapped[ticker] = {
            "open": open_array,
            "close": close_array,
            "rsi": rsi_array,
            "signal": signal_array,
            "direction": direction_array,
        }

    return mapped


# =============================================================================
# COST / EXECUTION HELPERS
# =============================================================================


def apply_buy_cost(price: float) -> float:
    """
    Price paid when buying.
    """

    return price * (1.0 + SLIPPAGE_RATE)


def apply_sell_cost(price: float) -> float:
    """
    Price received when selling.
    """

    return price * (1.0 - SLIPPAGE_RATE)


def calculate_commission(notional: float) -> float:
    return abs(notional) * COMMISSION_RATE


def position_size(equity: float, entry_price: float) -> float:
    """
    Position sizing based on:

        risk = 1% of equity
        assumed stop distance = 5%

    Therefore:

        position value = equity * 1% / 5%

    capped at 25% of equity.
    """

    if equity <= 0:
        return 0.0

    if entry_price <= 0:
        return 0.0

    risk_amount = equity * RISK_PER_TRADE

    position_value = risk_amount / STOP_DISTANCE_PCT

    maximum_value = equity * MAX_POSITION_PCT

    position_value = min(position_value, maximum_value)

    shares = position_value / entry_price

    return max(shares, 0.0)


# =============================================================================
# METRICS
# =============================================================================


def calculate_max_drawdown(equity_curve: np.ndarray) -> float:
    """
    Return max drawdown as a positive percentage.
    """

    if len(equity_curve) == 0:
        return 0.0

    running_max = np.maximum.accumulate(equity_curve)

    drawdown = (equity_curve / running_max) - 1.0

    return abs(float(drawdown.min())) * 100.0


def calculate_sharpe(equity_curve: np.ndarray) -> float:
    """
    Annualised daily Sharpe ratio.
    """

    if len(equity_curve) < 2:
        return 0.0

    equity_series = pd.Series(equity_curve)

    returns = equity_series.pct_change().dropna()

    if len(returns) < 2:
        return 0.0

    std = returns.std()

    if std == 0 or np.isnan(std):
        return 0.0

    return float(returns.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))


def calculate_trade_metrics(trades: List[Dict]) -> Dict[str, float]:
    """
    Calculate trade statistics.
    """

    if not trades:

        return {
            "trades": 0,
            "win_rate": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
        }

    profits = np.array([float(trade["net_pnl"]) for trade in trades], dtype=float)

    winning = profits[profits > 0]

    losing = profits[profits < 0]

    gross_profit = float(winning.sum()) if len(winning) else 0.0

    gross_loss = abs(float(losing.sum())) if len(losing) else 0.0

    if gross_loss > 0:

        profit_factor = gross_profit / gross_loss

    else:

        profit_factor = np.inf if gross_profit > 0 else 0.0

    win_rate = len(winning) / len(profits) * 100.0

    avg_win = float(winning.mean()) if len(winning) else 0.0

    avg_loss = float(losing.mean()) if len(losing) else 0.0

    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
    }


# =============================================================================
# PORTFOLIO SIMULATION
# =============================================================================


def simulate_configuration(
    config: Tuple[int, int],
    mapped: Dict[str, Dict[str, np.ndarray]],
    calendar: pd.DatetimeIndex,
) -> Dict:
    """
    Run one portfolio backtest.

    config:
        (RSI overbought, RSI oversold)

    Entry:
        SuperTrend bullish reversal
        AND RSI between OS and OB

    Exit:
        SuperTrend bearish reversal
        OR RSI >= OB

    Execution:
        Signal on today's close.
        Execute at next available day's open.
    """

    rsi_ob, rsi_os = config

    tickers = list(mapped.keys())

    n_days = len(calendar)

    cash = float(INITIAL_CAPITAL)

    positions = {}

    trades = []

    equity_curve = np.full(n_days, INITIAL_CAPITAL, dtype=np.float64)

    # -------------------------------------------------------------------------
    # Main daily loop
    # -------------------------------------------------------------------------

    for i in range(n_days - 1):

        # -------------------------------------------------------------
        # Current portfolio value before today's signal processing
        # -------------------------------------------------------------

        current_equity = cash

        for ticker, position in positions.items():

            close_price = mapped[ticker]["close"][i]

            if np.isnan(close_price):
                continue

            current_equity += position["shares"] * close_price

        equity_curve[i] = current_equity

        # -------------------------------------------------------------
        # Exit positions
        # -------------------------------------------------------------

        exit_candidates = []

        for ticker, position in list(positions.items()):

            signal = mapped[ticker]["signal"][i]

            rsi = mapped[ticker]["rsi"][i]

            should_exit = False

            if signal == -1:
                should_exit = True

            elif not np.isnan(rsi) and rsi >= rsi_ob:
                should_exit = True

            if should_exit:
                exit_candidates.append(ticker)

        # Execute exits on next bar open
        for ticker in exit_candidates:

            if ticker not in positions:
                continue

            next_open = mapped[ticker]["open"][i + 1]

            if np.isnan(next_open):
                continue

            position = positions[ticker]

            execution_price = apply_sell_cost(float(next_open))

            shares = position["shares"]

            gross_value = shares * execution_price

            commission = calculate_commission(gross_value)

            net_proceeds = gross_value - commission

            cash += net_proceeds

            entry_value = position["entry_value"]

            entry_commission = position["entry_commission"]

            total_cost = entry_value + entry_commission

            net_pnl = net_proceeds - total_cost

            trades.append(
                {
                    "ticker": ticker,
                    "entry_date": position["entry_date"],
                    "exit_date": calendar[i + 1],
                    "entry_price": position["entry_price"],
                    "exit_price": execution_price,
                    "shares": shares,
                    "entry_value": entry_value,
                    "exit_value": gross_value,
                    "entry_commission": entry_commission,
                    "exit_commission": commission,
                    "net_pnl": net_pnl,
                }
            )

            del positions[ticker]

        # -------------------------------------------------------------
        # Recalculate equity after exits
        # -------------------------------------------------------------

        equity_after_exits = cash

        for ticker, position in positions.items():

            close_price = mapped[ticker]["close"][i]

            if np.isnan(close_price):
                continue

            equity_after_exits += position["shares"] * close_price

        # -------------------------------------------------------------
        # Find new entries
        # -------------------------------------------------------------

        available_slots = MAX_POSITIONS - len(positions)

        if available_slots > 0:

            candidates = []

            for ticker in tickers:

                if ticker in positions:
                    continue

                signal = mapped[ticker]["signal"][i]

                if signal != 1:
                    continue

                rsi = mapped[ticker]["rsi"][i]

                if np.isnan(rsi):
                    continue

                # Baseline:
                # OB=100, OS=0 disables RSI filtering.
                if not (rsi_os == 0 and rsi_ob == 100):

                    if not (rsi_os < rsi < rsi_ob):
                        continue

                next_open = mapped[ticker]["open"][i + 1]

                if np.isnan(next_open):
                    continue

                if next_open <= 0:
                    continue

                candidates.append((ticker, float(rsi)))

            # ---------------------------------------------------------
            # Deterministic candidate ordering
            #
            # Higher RSI first.
            # This is simply the selection rule when more than
            # MAX_POSITIONS signals occur simultaneously.
            # ---------------------------------------------------------

            candidates.sort(key=lambda item: (-item[1], item[0]))

            candidates = candidates[:available_slots]

            # ---------------------------------------------------------
            # Enter positions
            # ---------------------------------------------------------

            for ticker, rsi in candidates:

                next_open = mapped[ticker]["open"][i + 1]

                execution_price = apply_buy_cost(float(next_open))

                shares = position_size(equity_after_exits, execution_price)

                if shares <= 0:
                    continue

                gross_value = shares * execution_price

                commission = calculate_commission(gross_value)

                total_required = gross_value + commission

                if total_required > cash:
                    continue

                cash -= total_required

                positions[ticker] = {
                    "entry_date": calendar[i + 1],
                    "entry_price": execution_price,
                    "entry_value": gross_value,
                    "entry_commission": commission,
                    "shares": shares,
                }

        # -------------------------------------------------------------
        # Mark portfolio to market at today's close
        # -------------------------------------------------------------

        marked_equity = cash

        for ticker, position in positions.items():

            close_price = mapped[ticker]["close"][i]

            if np.isnan(close_price):
                continue

            marked_equity += position["shares"] * close_price

        equity_curve[i] = marked_equity

    # =========================================================================
    # FORCE CLOSE ALL OPEN POSITIONS AT LAST AVAILABLE CLOSE
    # =========================================================================

    final_index = n_days - 1

    for ticker, position in list(positions.items()):

        final_close = mapped[ticker]["close"][final_index]

        if np.isnan(final_close):
            continue

        execution_price = apply_sell_cost(float(final_close))

        shares = position["shares"]

        gross_value = shares * execution_price

        commission = calculate_commission(gross_value)

        net_proceeds = gross_value - commission

        cash += net_proceeds

        total_cost = position["entry_value"] + position["entry_commission"]

        net_pnl = net_proceeds - total_cost

        trades.append(
            {
                "ticker": ticker,
                "entry_date": position["entry_date"],
                "exit_date": calendar[final_index],
                "entry_price": position["entry_price"],
                "exit_price": execution_price,
                "shares": shares,
                "entry_value": position["entry_value"],
                "exit_value": gross_value,
                "entry_commission": position["entry_commission"],
                "exit_commission": commission,
                "net_pnl": net_pnl,
            }
        )

    positions.clear()

    equity_curve[-1] = cash

    # =========================================================================
    # METRICS
    # =========================================================================

    final_equity = float(cash)

    net_profit = final_equity - INITIAL_CAPITAL

    return_pct = net_profit / INITIAL_CAPITAL * 100.0

    max_drawdown = calculate_max_drawdown(equity_curve)

    sharpe = calculate_sharpe(equity_curve)

    trade_metrics = calculate_trade_metrics(trades)

    result = {
        "rsi_ob": rsi_ob,
        "rsi_os": rsi_os,
        "initial_capital": INITIAL_CAPITAL,
        "final_equity": final_equity,
        "net_profit": net_profit,
        "return_pct": return_pct,
        "max_drawdown_pct": max_drawdown,
        "sharpe": sharpe,
        **trade_metrics,
        "equity_curve": equity_curve,
        "trades_data": trades,
    }

    return result


# =============================================================================
# MULTIPROCESSING
# =============================================================================

_WORKER_DATA = None
_WORKER_CALENDAR = None


def init_worker(mapped, calendar):
    """
    Initialise multiprocessing worker.
    """

    global _WORKER_DATA
    global _WORKER_CALENDAR

    _WORKER_DATA = mapped
    _WORKER_CALENDAR = calendar


def worker_backtest(config: Tuple[int, int]) -> Dict:
    """
    Multiprocessing worker.
    """

    return simulate_configuration(config, _WORKER_DATA, _WORKER_CALENDAR)


# =============================================================================
# SUPERTREND BASELINE
# =============================================================================


def run_supertrend_baseline(
    mapped: Dict[str, Dict[str, np.ndarray]], calendar: pd.DatetimeIndex
) -> Dict:
    """
    Run SuperTrend-only strategy.

    RSI is completely disabled.
    """

    print()
    print("=" * 100)
    print("BASELINE: SUPERTREND ONLY")
    print("=" * 100)

    start_time = time.time()

    result = simulate_configuration(
        (BASELINE_RSI_OB, BASELINE_RSI_OS), mapped, calendar
    )

    elapsed = time.time() - start_time

    print(f"Completed in {elapsed:.1f}s")

    print(f"Return: {result['return_pct']:.2f}%")

    print(f"Net profit: £{result['net_profit']:,.2f}")

    print(f"Max DD: {result['max_drawdown_pct']:.2f}%")

    print(f"Trades: {result['trades']:,}")

    print(f"Win rate: {result['win_rate']:.2f}%")

    print(f"Profit factor: {result['profit_factor']:.2f}")

    return result


# =============================================================================
# BUY & HOLD BENCHMARK
# =============================================================================


def calculate_buy_hold(
    mapped: Dict[str, Dict[str, np.ndarray]], calendar: pd.DatetimeIndex
) -> Dict:
    """
    Equal-weight buy-and-hold benchmark.

    Each stock receives an equal share of the initial £5,000.

    NOTE:
        This is a simple benchmark, not a dynamically rebalanced index.
    """

    print()
    print("=" * 100)
    print("BUY & HOLD BENCHMARK")
    print("=" * 100)

    stock_returns = []

    valid_tickers = []

    for ticker, stock in mapped.items():

        close = stock["close"]

        valid = ~np.isnan(close)

        if valid.sum() < 2:
            continue

        first_index = np.where(valid)[0][0]

        last_index = np.where(valid)[0][-1]

        first_price = close[first_index]

        last_price = close[last_index]

        if first_price <= 0:
            continue

        stock_return = last_price / first_price - 1.0

        stock_returns.append(stock_return)

        valid_tickers.append(ticker)

    if not stock_returns:

        return {
            "tickers": 0,
            "return_pct": 0.0,
            "final_equity": INITIAL_CAPITAL,
            "net_profit": 0.0,
        }

    average_return = float(np.mean(stock_returns))

    final_equity = INITIAL_CAPITAL * (1.0 + average_return)

    net_profit = final_equity - INITIAL_CAPITAL

    return_pct = average_return * 100.0

    print(f"Stocks included: {len(valid_tickers):,}")

    print(f"Equal-weight return: {return_pct:.2f}%")

    print(f"Final equity: £{final_equity:,.2f}")

    return {
        "tickers": len(valid_tickers),
        "return_pct": return_pct,
        "final_equity": final_equity,
        "net_profit": net_profit,
    }


# =============================================================================
# SIGNAL SANITY CHECK
# =============================================================================


def run_signal_sanity_check(mapped: Dict[str, Dict[str, np.ndarray]]) -> None:
    """
    Verify that SuperTrend is producing signals.

    If there are zero bullish signals, stop the program.
    """

    total_bullish = 0
    total_bearish = 0
    total_valid_rsi = 0

    for stock in mapped.values():

        total_bullish += int(np.sum(stock["signal"] == 1))

        total_bearish += int(np.sum(stock["signal"] == -1))

        total_valid_rsi += int(np.sum(~np.isnan(stock["rsi"])))

    print()
    print("=" * 100)
    print("SIGNAL SANITY CHECK")
    print("=" * 100)

    print(f"Bullish SuperTrend signals: {total_bullish:,}")

    print(f"Bearish SuperTrend signals: {total_bearish:,}")

    print(f"Valid RSI observations:     {total_valid_rsi:,}")

    if total_bullish == 0:

        print()
        print("ERROR: ZERO bullish SuperTrend signals.")

        print("Do NOT run the optimizer.")

        raise RuntimeError("SuperTrend generated zero bullish signals.")

    print()
    print("✓ Signal generation looks valid.")


# =============================================================================
# SAVE RESULTS
# =============================================================================


def save_results(results: List[Dict], baseline: Dict, benchmark: Dict) -> None:
    """
    Save optimisation results to CSV.
    """

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------

    summary_rows = []

    for result in results:

        summary_rows.append(
            {
                "rsi_ob": result["rsi_ob"],
                "rsi_os": result["rsi_os"],
                "initial_capital": result["initial_capital"],
                "final_equity": result["final_equity"],
                "net_profit": result["net_profit"],
                "return_pct": result["return_pct"],
                "max_drawdown_pct": result["max_drawdown_pct"],
                "sharpe": result["sharpe"],
                "trades": result["trades"],
                "win_rate": result["win_rate"],
                "gross_profit": result["gross_profit"],
                "gross_loss": result["gross_loss"],
                "profit_factor": result["profit_factor"],
                "avg_win": result["avg_win"],
                "avg_loss": result["avg_loss"],
            }
        )

    summary_df = pd.DataFrame(summary_rows)

    summary_path = OUTPUT_DIR / "rsi_optimization_summary.csv"

    summary_df.to_csv(summary_path, index=False)

    # -------------------------------------------------------------------------
    # All trades
    # -------------------------------------------------------------------------

    all_trades = []

    for result in results:

        for trade in result["trades_data"]:

            trade_row = dict(trade)

            trade_row["rsi_ob"] = result["rsi_ob"]

            trade_row["rsi_os"] = result["rsi_os"]

            all_trades.append(trade_row)

    if all_trades:

        trades_df = pd.DataFrame(all_trades)

        trades_path = OUTPUT_DIR / "rsi_all_trades.csv"

        trades_df.to_csv(trades_path, index=False)

    # -------------------------------------------------------------------------
    # Equity curves
    # -------------------------------------------------------------------------

    equity_rows = []

    for result in results:

        curve = result["equity_curve"]

        for index, equity in enumerate(curve):

            equity_rows.append(
                {
                    "date": index,
                    "rsi_ob": result["rsi_ob"],
                    "rsi_os": result["rsi_os"],
                    "equity": equity,
                }
            )

    if equity_rows:

        equity_df = pd.DataFrame(equity_rows)

        equity_path = OUTPUT_DIR / "rsi_equity_curves.csv"

        equity_df.to_csv(equity_path, index=False)

    # -------------------------------------------------------------------------
    # Baseline summary
    # -------------------------------------------------------------------------

    baseline_df = pd.DataFrame(
        [
            {
                "strategy": "SuperTrend Only",
                "initial_capital": INITIAL_CAPITAL,
                "final_equity": baseline["final_equity"],
                "net_profit": baseline["net_profit"],
                "return_pct": baseline["return_pct"],
                "max_drawdown_pct": baseline["max_drawdown_pct"],
                "sharpe": baseline["sharpe"],
                "trades": baseline["trades"],
                "win_rate": baseline["win_rate"],
                "profit_factor": baseline["profit_factor"],
            }
        ]
    )

    baseline_df.to_csv(OUTPUT_DIR / "supertrend_only_summary.csv", index=False)

    # Baseline trades
    if baseline["trades_data"]:

        pd.DataFrame(baseline["trades_data"]).to_csv(
            OUTPUT_DIR / "supertrend_only_trades.csv", index=False
        )

    # Baseline equity
    pd.DataFrame(
        {
            "date_index": np.arange(len(baseline["equity_curve"])),
            "equity": baseline["equity_curve"],
        }
    ).to_csv(OUTPUT_DIR / "supertrend_only_equity.csv", index=False)

    # -------------------------------------------------------------------------
    # Buy & Hold
    # -------------------------------------------------------------------------

    pd.DataFrame([benchmark]).to_csv(OUTPUT_DIR / "buy_hold_summary.csv", index=False)

    print()
    print(f"Results saved to: {OUTPUT_DIR.resolve()}")


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:

    total_start = time.time()

    print()
    print("=" * 100)
    print("SUPERTrend + RSI PORTFOLIO OPTIMIZER v4")
    print("=" * 100)

    print()
    print(f"Initial capital: £{INITIAL_CAPITAL:,.2f}")

    print(f"Risk per trade: {RISK_PER_TRADE:.2%}")

    print(f"Max positions: {MAX_POSITIONS}")

    print(f"Max position size: {MAX_POSITION_PCT:.2%}")

    print(f"Commission: {COMMISSION_RATE:.2%}")

    print(f"Slippage: {SLIPPAGE_RATE:.2%}")

    print(f"ATR period: {ATR_PERIOD}")

    print(f"SuperTrend multiplier: {ATR_MULTIPLIER}")

    print(f"RSI period: {RSI_PERIOD}")

    print(f"Start date: {START_DATE}")

    cores = os.cpu_count() or 1

    # Leave one core available for the operating system.
    workers = max(1, cores - 1)

    print(f"CPU cores detected: {cores}")

    print(f"Worker processes: {workers}")

    print("Strategy: multiprocessing")

    # =========================================================================
    # LOAD WATCHLIST
    # =========================================================================

    tickers = load_watchlist()

    print()
    print(f"Loaded {len(tickers):,} tickers " f"from {WATCHLIST_FILE}")

    # =========================================================================
    # PREPARE DATA
    # =========================================================================

    raw_data = prepare_all_data(tickers)

    if not raw_data:

        raise RuntimeError("No stocks could be prepared.")

    # =========================================================================
    # UNIVERSAL CALENDAR
    # =========================================================================

    print()
    print("=" * 100)
    print("CREATING UNIVERSAL CALENDAR")
    print("=" * 100)

    calendar_start = time.time()

    calendar = create_calendar(raw_data)

    print(f"Universal calendar: " f"{len(calendar):,} trading dates")

    mapped = build_calendar_arrays(raw_data, calendar)

    print(f"Calendar mapping complete in " f"{time.time() - calendar_start:.1f}s")

    # =========================================================================
    # SIGNAL SANITY CHECK
    # =========================================================================

    run_signal_sanity_check(mapped)

    # =========================================================================
    # BUY & HOLD
    # =========================================================================

    benchmark = calculate_buy_hold(mapped, calendar)

    # =========================================================================
    # SUPERTREND BASELINE
    # =========================================================================

    baseline = run_supertrend_baseline(mapped, calendar)

    # =========================================================================
    # CONFIGURATIONS
    # =========================================================================

    configurations = [
        (rsi_ob, rsi_os)
        for rsi_ob in RSI_OVERBOUGHT_VALUES
        for rsi_os in RSI_OVERSOLD_VALUES
        if rsi_os < rsi_ob
    ]

    print()
    print("=" * 100)
    print("MULTIPROCESS RSI OPTIMIZATION")
    print("=" * 100)

    print(f"Configurations: {len(configurations)}")

    print(f"Stocks: {len(mapped):,}")

    print(f"Worker processes: {workers}")

    print()
    print("Starting portfolio backtests...")

    # =========================================================================
    # MULTIPROCESSING
    # =========================================================================

    results = []

    optimization_start = time.time()

    ctx = mp.get_context("spawn")

    with ctx.Pool(
        processes=workers, initializer=init_worker, initargs=(mapped, calendar)
    ) as pool:

        progress = tqdm(
            total=len(configurations),
            desc="Backtests",
            unit="config",
            dynamic_ncols=True,
        )

        for result in pool.imap_unordered(worker_backtest, configurations):

            results.append(result)

            progress.update(1)

            progress.set_postfix(
                OB=result["rsi_ob"],
                OS=result["rsi_os"],
                Return=f"{result['return_pct']:.1f}%",
                PF=(
                    f"{result['profit_factor']:.2f}"
                    if np.isfinite(result["profit_factor"])
                    else "inf"
                ),
                DD=f"{result['max_drawdown_pct']:.1f}%",
            )

        progress.close()

    optimization_elapsed = time.time() - optimization_start

    print()
    print(f"Optimization completed in " f"{optimization_elapsed / 60:.2f} minutes")

    # =========================================================================
    # SORT RESULTS
    # =========================================================================

    results.sort(key=lambda result: result["net_profit"], reverse=True)

    # =========================================================================
    # FINAL RESULTS
    # =========================================================================

    best = results[0]

    print()
    print("=" * 100)
    print("FINAL RESULTS")
    print("=" * 100)

    print()
    print("BEST BY NET PROFIT:")

    print(f"OB={best['rsi_ob']}, " f"OS={best['rsi_os']}")

    print(f"Final equity: " f"£{best['final_equity']:,.2f}")

    print(f"Net profit: " f"£{best['net_profit']:,.2f}")

    print(f"Return: " f"{best['return_pct']:.2f}%")

    print(f"Max DD: " f"{best['max_drawdown_pct']:.2f}%")

    print(f"Sharpe: " f"{best['sharpe']:.2f}")

    print(f"Trades: " f"{best['trades']:,}")

    print(f"Win rate: " f"{best['win_rate']:.2f}%")

    if np.isfinite(best["profit_factor"]):

        print(f"Profit factor: " f"{best['profit_factor']:.2f}")

    else:

        print("Profit factor: inf")

    # =========================================================================
    # TOP 10 CONFIGURATIONS
    # =========================================================================

    print()
    print("=" * 100)
    print("TOP 10 CONFIGURATIONS BY NET PROFIT")
    print("=" * 100)

    print()

    print(
        f"{'Rank':<6}"
        f"{'OB':<6}"
        f"{'OS':<6}"
        f"{'Return':>12}"
        f"{'Net Profit':>15}"
        f"{'Max DD':>12}"
        f"{'Sharpe':>10}"
        f"{'Trades':>10}"
        f"{'Win %':>10}"
        f"{'PF':>10}"
    )

    print("-" * 100)

    for rank, result in enumerate(results[:10], start=1):

        pf = result["profit_factor"]

        pf_text = f"{pf:.2f}" if np.isfinite(pf) else "inf"

        print(
            f"{rank:<6}"
            f"{result['rsi_ob']:<6}"
            f"{result['rsi_os']:<6}"
            f"{result['return_pct']:>11.2f}%"
            f"£{result['net_profit']:>13,.2f}"
            f"{result['max_drawdown_pct']:>11.2f}%"
            f"{result['sharpe']:>10.2f}"
            f"{result['trades']:>10,}"
            f"{result['win_rate']:>9.2f}%"
            f"{pf_text:>10}"
        )

    # =========================================================================
    # BASELINE COMPARISON
    # =========================================================================

    print()
    print("=" * 100)
    print("BASELINE COMPARISON")
    print("=" * 100)

    print()

    print(
        f"SuperTrend only: "
        f"£{baseline['final_equity']:,.2f} "
        f"({baseline['return_pct']:.2f}%)"
    )

    print(f"Best RSI: " f"£{best['final_equity']:,.2f} " f"({best['return_pct']:.2f}%)")

    difference = best["final_equity"] - baseline["final_equity"]

    print(f"Difference: " f"£{difference:,.2f}")

    print(
        f"Buy & Hold: "
        f"£{benchmark['final_equity']:,.2f} "
        f"({benchmark['return_pct']:.2f}%)"
    )

    # =========================================================================
    # SAVE
    # =========================================================================

    save_results(results, baseline, benchmark)

    # =========================================================================
    # COMPLETE
    # =========================================================================

    total_elapsed = time.time() - total_start

    print()
    print("=" * 100)
    print(f"TOTAL RUNTIME: " f"{total_elapsed / 60:.2f} minutes")
    print("=" * 100)

    print()
    print("✓ Optimization complete.")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":

    mp.freeze_support()

    main()
