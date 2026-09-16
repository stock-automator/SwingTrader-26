#!/usr/bin/env python3

"""
FROZEN RSI + SUPERTREND VALIDATION

Strategy:
    SuperTrend: ATR 10, multiplier 3.0
    RSI: 14
    Entry: SuperTrend bullish reversal AND 45 < RSI < 60
    Exit: SuperTrend bearish reversal OR RSI >= 60
    Execution: next available bar OPEN

Realistic risk:
    Risk per trade: 1% of equity
    Stop: 3 x ATR
    Maximum position: 25% of equity
    Maximum positions: 10

Costs:
    Commission: 0.10%
    Slippage:   0.05%

Validation:
    Development: 2015-2022
    OOS:         2023-2026
    Calendar-year robustness: 2021-2026

IMPORTANT:
    RSI parameters are FROZEN at OB=60 / OS=45.
    Nothing is optimised using OOS data.
"""

from pathlib import Path
import os
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

DATA_DIR = Path("data/raw")
WATCHLIST_FILE = Path("watchlist.txt")
OUTPUT_DIR = Path("results/validation")

START_DATE = "2015-01-01"
END_DATE = "2026-09-15"

INITIAL_CAPITAL = 5000.0

# FROZEN STRATEGY
RSI_PERIOD = 14
RSI_OB = 60
RSI_OS = 45

ATR_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0

# REAL RISK MODEL
RISK_PER_TRADE = 0.01
STOP_ATR_MULTIPLIER = 3.0

MAX_POSITIONS = 10
MAX_POSITION_PCT = 0.25

# COSTS
COMMISSION_RATE = 0.0010
SLIPPAGE_RATE = 0.0005

TRADING_DAYS_PER_YEAR = 252


# ============================================================
# DATA
# ============================================================

def load_watchlist():

    if not WATCHLIST_FILE.exists():
        raise FileNotFoundError(
            f"Missing {WATCHLIST_FILE}"
        )

    tickers = []

    for line in WATCHLIST_FILE.read_text().splitlines():

        ticker = line.strip().upper()

        if ticker and not ticker.startswith("#"):
            tickers.append(ticker)

    return list(dict.fromkeys(tickers))


def load_stock(ticker):

    path = DATA_DIR / f"{ticker}.parquet"

    if not path.exists():
        return None

    try:

        df = pd.read_parquet(path)

        if df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):

            df.columns = [
                c[0] if isinstance(c, tuple) else c
                for c in df.columns
            ]

        rename = {}

        for c in df.columns:

            x = str(c).lower().strip()

            if x == "open":
                rename[c] = "Open"

            elif x == "high":
                rename[c] = "High"

            elif x == "low":
                rename[c] = "Low"

            elif x == "close":
                rename[c] = "Close"

            elif x == "volume":
                rename[c] = "Volume"

        df = df.rename(columns=rename)

        required = [
            "Open",
            "High",
            "Low",
            "Close"
        ]

        if not all(c in df.columns for c in required):
            return None

        df.index = pd.to_datetime(df.index)

        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)

        df = df.sort_index()
        df = df[~df.index.duplicated(keep="last")]

        df = df[df.index >= START_DATE]

        for c in required:
            df[c] = pd.to_numeric(
                df[c],
                errors="coerce"
            )

        df = df.dropna(subset=required)

        if len(df) < 100:
            return None

        return df

    except Exception:
        return None


# ============================================================
# INDICATORS
# ============================================================

def atr(df, period=10):

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    previous_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()


def rsi(df, period=14):

    delta = df["Close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = avg_gain / avg_loss

    result = 100 - (100 / (1 + rs))

    result = result.where(
        avg_loss != 0,
        100
    )

    result = result.where(
        avg_gain != 0,
        0
    )

    both_zero = (
        (avg_gain == 0)
        & (avg_loss == 0)
    )

    result = result.where(
        ~both_zero,
        50
    )

    return result


def supertrend(df, period=10, multiplier=3.0):

    high = df["High"].to_numpy(float)
    low = df["Low"].to_numpy(float)
    close = df["Close"].to_numpy(float)

    atr_values = atr(
        df,
        period
    ).to_numpy(float)

    n = len(df)

    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)

    direction = np.zeros(n, dtype=np.int8)
    signal = np.zeros(n, dtype=np.int8)

    valid = np.where(
        ~np.isnan(atr_values)
    )[0]

    if len(valid) == 0:
        return direction, signal

    first = valid[0]

    hl2 = (
        high[first]
        + low[first]
    ) / 2

    upper[first] = (
        hl2
        + multiplier * atr_values[first]
    )

    lower[first] = (
        hl2
        - multiplier * atr_values[first]
    )

    direction[first] = 1

    for i in range(first + 1, n):

        if np.isnan(atr_values[i]):
            continue

        hl2 = (
            high[i]
            + low[i]
        ) / 2

        basic_upper = (
            hl2
            + multiplier * atr_values[i]
        )

        basic_lower = (
            hl2
            - multiplier * atr_values[i]
        )

        previous_upper = upper[i - 1]
        previous_lower = lower[i - 1]
        previous_close = close[i - 1]

        if (
            np.isnan(previous_upper)
            or basic_upper < previous_upper
            or previous_close > previous_upper
        ):
            upper[i] = basic_upper
        else:
            upper[i] = previous_upper

        if (
            np.isnan(previous_lower)
            or basic_lower > previous_lower
            or previous_close < previous_lower
        ):
            lower[i] = basic_lower
        else:
            lower[i] = previous_lower

        previous_direction = direction[i - 1]

        if previous_direction == 1:

            if close[i] <= lower[i]:

                direction[i] = -1
                signal[i] = -1

            else:

                direction[i] = 1

        elif previous_direction == -1:

            if close[i] >= upper[i]:

                direction[i] = 1
                signal[i] = 1

            else:

                direction[i] = -1

    return direction, signal


# ============================================================
# PREPARE DATA
# ============================================================

def prepare_stock(ticker):

    df = load_stock(ticker)

    if df is None:
        return None

    try:

        atr_values = atr(
            df,
            ATR_PERIOD
        )

        rsi_values = rsi(
            df,
            RSI_PERIOD
        )

        direction, signal = supertrend(
            df,
            ATR_PERIOD,
            SUPERTREND_MULTIPLIER
        )

        return {
            "dates": df.index.to_numpy(),
            "open": df["Open"].to_numpy(float),
            "high": df["High"].to_numpy(float),
            "low": df["Low"].to_numpy(float),
            "close": df["Close"].to_numpy(float),
            "atr": atr_values.to_numpy(float),
            "rsi": rsi_values.to_numpy(float),
            "direction": direction,
            "signal": signal,
        }

    except Exception:
        return None


def prepare_all(tickers):

    prepared = {}

    print()
    print("Preparing stock data...")

    for number, ticker in enumerate(
        tickers,
        1
    ):

        data = prepare_stock(ticker)

        if data is not None:
            prepared[ticker] = data

        if number % 50 == 0:

            print(
                f"  {number}/{len(tickers)} "
                f"stocks processed"
            )

    print(
        f"Prepared {len(prepared)} stocks"
    )

    return prepared


# ============================================================
# CALENDAR
# ============================================================

def create_calendar(data):

    dates = set()

    for stock in data.values():

        dates.update(
            pd.Timestamp(x)
            for x in stock["dates"]
        )

    return pd.DatetimeIndex(
        sorted(dates)
    )


def map_calendar(data, calendar):

    mapped = {}

    n = len(calendar)

    for ticker, stock in data.items():

        arrays = {
            "open": np.full(n, np.nan),
            "high": np.full(n, np.nan),
            "low": np.full(n, np.nan),
            "close": np.full(n, np.nan),
            "atr": np.full(n, np.nan),
            "rsi": np.full(n, np.nan),
            "signal": np.zeros(n, dtype=np.int8),
        }

        stock_dates = pd.DatetimeIndex(
            stock["dates"]
        )

        positions = calendar.get_indexer(
            stock_dates
        )

        valid = positions >= 0

        positions = positions[valid]

        if len(positions) == 0:
            continue

        for key in [
            "open",
            "high",
            "low",
            "close",
            "atr",
            "rsi",
        ]:

            arrays[key][positions] = (
                stock[key][valid]
            )

        arrays["signal"][positions] = (
            stock["signal"][valid]
        )

        mapped[ticker] = arrays

    return mapped


# ============================================================
# EXECUTION
# ============================================================

def buy_execution(price):

    return price * (
        1 + SLIPPAGE_RATE
    )


def sell_execution(price):

    return price * (
        1 - SLIPPAGE_RATE
    )


def commission(value):

    return abs(value) * COMMISSION_RATE


# ============================================================
# PORTFOLIO
# ============================================================

def equity_value(
    cash,
    positions,
    mapped,
    i
):

    equity = cash

    for ticker, position in positions.items():

        price = mapped[ticker]["close"][i]

        if np.isfinite(price):

            equity += (
                position["shares"]
                * price
            )

    return equity


def position_size(
    equity,
    entry_price,
    atr_value
):

    if (
        equity <= 0
        or entry_price <= 0
        or not np.isfinite(atr_value)
        or atr_value <= 0
    ):
        return 0, np.nan

    risk_amount = (
        equity
        * RISK_PER_TRADE
    )

    stop_distance = (
        atr_value
        * STOP_ATR_MULTIPLIER
    )

    if stop_distance <= 0:
        return 0, np.nan

    shares = (
        risk_amount
        / stop_distance
    )

    max_value = (
        equity
        * MAX_POSITION_PCT
    )

    max_shares = (
        max_value
        / entry_price
    )

    shares = min(
        shares,
        max_shares
    )

    stop_price = (
        entry_price
        - stop_distance
    )

    if stop_price <= 0:
        return 0, np.nan

    return shares, stop_price


# ============================================================
# SIMULATOR
# ============================================================

def simulate(
    mapped,
    calendar,
    start_index,
    end_index
):

    cash = INITIAL_CAPITAL

    positions = {}

    trades = []

    equity_curve = []

    max_positions_seen = 0
    max_deployed = 0

    tickers = sorted(
        mapped.keys()
    )

    for i in range(
        start_index,
        end_index + 1
    ):

        current_date = calendar[i]

        # ----------------------------------------------------
        # 1. INTRADAY HARD STOPS
        # ----------------------------------------------------

        stop_exits = []

        for ticker, position in list(
            positions.items()
        ):

            open_price = (
                mapped[ticker]["open"][i]
            )

            low_price = (
                mapped[ticker]["low"][i]
            )

            if not np.isfinite(low_price):
                continue

            stop = position[
                "stop_price"
            ]

            if low_price > stop:
                continue

            if (
                np.isfinite(open_price)
                and open_price <= stop
            ):

                raw_exit = open_price
                reason = "ATR_STOP_GAP"

            else:

                raw_exit = stop
                reason = "ATR_STOP"

            stop_exits.append(
                (
                    ticker,
                    raw_exit,
                    reason
                )
            )

        for ticker, raw_exit, reason in stop_exits:

            if ticker not in positions:
                continue

            position = positions[ticker]

            exit_price = sell_execution(
                raw_exit
            )

            gross = (
                position["shares"]
                * exit_price
            )

            exit_commission = commission(
                gross
            )

            net_proceeds = (
                gross
                - exit_commission
            )

            cash += net_proceeds

            pnl = (
                net_proceeds
                - position["entry_cost"]
            )

            trades.append({
                "ticker": ticker,
                "entry_date": position["entry_date"],
                "exit_date": current_date,
                "entry_price": position["entry_price"],
                "exit_price": exit_price,
                "shares": position["shares"],
                "entry_value": position["entry_value"],
                "exit_value": gross,
                "entry_commission": position["entry_commission"],
                "exit_commission": exit_commission,
                "net_pnl": pnl,
                "exit_reason": reason,
                "atr_at_entry": position["atr"],
                "stop_price": position["stop_price"],
            })

            del positions[ticker]

        # ----------------------------------------------------
        # 2. SIGNAL EXITS
        # ----------------------------------------------------

        if i < end_index:

            for ticker in list(
                positions.keys()
            ):

                signal = mapped[
                    ticker
                ]["signal"][i]

                current_rsi = mapped[
                    ticker
                ]["rsi"][i]

                should_exit = (
                    signal == -1
                    or (
                        np.isfinite(current_rsi)
                        and current_rsi >= RSI_OB
                    )
                )

                if not should_exit:
                    continue

                next_open = mapped[
                    ticker
                ]["open"][i + 1]

                if (
                    not np.isfinite(next_open)
                    or next_open <= 0
                ):
                    continue

                position = positions[ticker]

                exit_price = sell_execution(
                    next_open
                )

                gross = (
                    position["shares"]
                    * exit_price
                )

                exit_commission = commission(
                    gross
                )

                net_proceeds = (
                    gross
                    - exit_commission
                )

                cash += net_proceeds

                pnl = (
                    net_proceeds
                    - position["entry_cost"]
                )

                reason = (
                    "SUPERTREND_EXIT"
                    if signal == -1
                    else "RSI_EXIT"
                )

                trades.append({
                    "ticker": ticker,
                    "entry_date": position["entry_date"],
                    "exit_date": calendar[i + 1],
                    "entry_price": position["entry_price"],
                    "exit_price": exit_price,
                    "shares": position["shares"],
                    "entry_value": position["entry_value"],
                    "exit_value": gross,
                    "entry_commission": position["entry_commission"],
                    "exit_commission": exit_commission,
                    "net_pnl": pnl,
                    "exit_reason": reason,
                    "atr_at_entry": position["atr"],
                    "stop_price": position["stop_price"],
                })

                del positions[ticker]

        # ----------------------------------------------------
        # 3. ENTRIES
        # ----------------------------------------------------

        if i < end_index:

            available_slots = (
                MAX_POSITIONS
                - len(positions)
            )

            if available_slots > 0:

                equity = equity_value(
                    cash,
                    positions,
                    mapped,
                    i
                )

                candidates = []

                for ticker in tickers:

                    if ticker in positions:
                        continue

                    signal = mapped[
                        ticker
                    ]["signal"][i]

                    if signal != 1:
                        continue

                    current_rsi = mapped[
                        ticker
                    ]["rsi"][i]

                    if not np.isfinite(
                        current_rsi
                    ):
                        continue

                    if not (
                        RSI_OS
                        < current_rsi
                        < RSI_OB
                    ):
                        continue

                    next_open = mapped[
                        ticker
                    ]["open"][i + 1]

                    entry_atr = mapped[
                        ticker
                    ]["atr"][i]

                    if (
                        not np.isfinite(next_open)
                        or next_open <= 0
                    ):
                        continue

                    if (
                        not np.isfinite(entry_atr)
                        or entry_atr <= 0
                    ):
                        continue

                    candidates.append({
                        "ticker": ticker,
                        "rsi": current_rsi,
                        "open": next_open,
                        "atr": entry_atr,
                    })

                # EXACT SAME ORDERING AS ORIGINAL OPTIMIZER
                candidates.sort(
                    key=lambda x: (
                        -x["rsi"],
                        x["ticker"]
                    )
                )

                for candidate in candidates:

                    if (
                        len(positions)
                        >= MAX_POSITIONS
                    ):
                        break

                    ticker = candidate[
                        "ticker"
                    ]

                    raw_open = candidate[
                        "open"
                    ]

                    entry_atr = candidate[
                        "atr"
                    ]

                    entry_price = buy_execution(
                        raw_open
                    )

                    shares, stop_price = (
                        position_size(
                            equity,
                            entry_price,
                            entry_atr
                        )
                    )

                    if shares <= 0:
                        continue

                    entry_value = (
                        shares
                        * entry_price
                    )

                    entry_commission = commission(
                        entry_value
                    )

                    total_cost = (
                        entry_value
                        + entry_commission
                    )

                    # Cash constraint
                    if total_cost > cash:
                        continue

                    cash -= total_cost

                    positions[ticker] = {
                        "entry_date": calendar[i + 1],
                        "entry_price": entry_price,
                        "entry_value": entry_value,
                        "entry_commission": entry_commission,
                        "entry_cost": total_cost,
                        "shares": shares,
                        "atr": entry_atr,
                        "stop_price": stop_price,
                    }

                    max_positions_seen = max(
                        max_positions_seen,
                        len(positions)
                    )

                    deployed = sum(
                        p["entry_value"]
                        for p in positions.values()
                    )

                    max_deployed = max(
                        max_deployed,
                        deployed
                    )

        # ----------------------------------------------------
        # 4. EQUITY
        # ----------------------------------------------------

        equity = equity_value(
            cash,
            positions,
            mapped,
            i
        )

        equity_curve.append(
            equity
        )

    # --------------------------------------------------------
    # FORCE CLOSE
    # --------------------------------------------------------

    final_date = calendar[end_index]

    for ticker, position in list(
        positions.items()
    ):

        close = mapped[
            ticker
        ]["close"][end_index]

        if not np.isfinite(close):
            continue

        exit_price = sell_execution(
            close
        )

        gross = (
            position["shares"]
            * exit_price
        )

        exit_commission = commission(
            gross
        )

        net_proceeds = (
            gross
            - exit_commission
        )

        cash += net_proceeds

        pnl = (
            net_proceeds
            - position["entry_cost"]
        )

        trades.append({
            "ticker": ticker,
            "entry_date": position["entry_date"],
            "exit_date": final_date,
            "entry_price": position["entry_price"],
            "exit_price": exit_price,
            "shares": position["shares"],
            "entry_value": position["entry_value"],
            "exit_value": gross,
            "entry_commission": position["entry_commission"],
            "exit_commission": exit_commission,
            "net_pnl": pnl,
            "exit_reason": "PERIOD_END",
            "atr_at_entry": position["atr"],
            "stop_price": position["stop_price"],
        })

    positions.clear()

    equity_curve[-1] = cash

    return {
        "dates": calendar[
            start_index:end_index + 1
        ],
        "equity": np.array(
            equity_curve
        ),
        "trades": trades,
        "max_positions": max_positions_seen,
        "max_deployed": max_deployed,
    }


# ============================================================
# METRICS
# ============================================================

def calculate_metrics(result):

    equity = result["equity"]

    final_equity = float(
        equity[-1]
    )

    net_profit = (
        final_equity
        - INITIAL_CAPITAL
    )

    return_pct = (
        net_profit
        / INITIAL_CAPITAL
        * 100
    )

    running_max = np.maximum.accumulate(
        equity
    )

    drawdown = (
        equity / running_max
        - 1
    )

    max_dd = (
        abs(drawdown.min())
        * 100
    )

    daily_returns = (
        pd.Series(equity)
        .pct_change()
        .dropna()
    )

    if (
        len(daily_returns) > 1
        and daily_returns.std() > 0
    ):

        sharpe = (
            daily_returns.mean()
            / daily_returns.std()
            * np.sqrt(
                TRADING_DAYS_PER_YEAR
            )
        )

    else:

        sharpe = 0.0

    trades = result["trades"]

    if trades:

        pnls = np.array([
            t["net_pnl"]
            for t in trades
        ])

        winners = pnls[pnls > 0]
        losers = pnls[pnls < 0]

        gross_profit = (
            winners.sum()
            if len(winners)
            else 0
        )

        gross_loss = abs(
            losers.sum()
        ) if len(losers) else 0

        profit_factor = (
            gross_profit
            / gross_loss
            if gross_loss > 0
            else np.inf
        )

        win_rate = (
            len(winners)
            / len(pnls)
            * 100
        )

        avg_win = (
            winners.mean()
            if len(winners)
            else 0
        )

        avg_loss = (
            losers.mean()
            if len(losers)
            else 0
        )

        payoff = (
            avg_win
            / abs(avg_loss)
            if avg_loss != 0
            else np.inf
        )

        holds = []

        for trade in trades:

            entry = pd.Timestamp(
                trade["entry_date"]
            )

            exit_date = pd.Timestamp(
                trade["exit_date"]
            )

            holds.append(
                max(
                    1,
                    (
                        exit_date
                        - entry
                    ).days
                )
            )

        avg_hold = np.mean(
            holds
        )

        median_hold = np.median(
            holds
        )

        max_hold = np.max(
            holds
        )

        max_win = pnls.max()
        max_loss = pnls.min()

        # Consecutive losing trades
        max_consecutive_losses = 0
        current_losses = 0

        for pnl in pnls:

            if pnl < 0:

                current_losses += 1

                max_consecutive_losses = max(
                    max_consecutive_losses,
                    current_losses
                )

            else:

                current_losses = 0

    else:

        gross_profit = 0
        gross_loss = 0
        profit_factor = 0
        win_rate = 0
        avg_win = 0
        avg_loss = 0
        payoff = 0
        avg_hold = 0
        median_hold = 0
        max_hold = 0
        max_win = 0
        max_loss = 0
        max_consecutive_losses = 0

    return {
        "initial_capital": INITIAL_CAPITAL,
        "final_equity": final_equity,
        "net_profit": net_profit,
        "return_pct": return_pct,
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "trades": len(trades),
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff,
        "largest_winner": max_win,
        "largest_loser": max_loss,
        "avg_hold_days": avg_hold,
        "median_hold_days": median_hold,
        "max_hold_days": max_hold,
        "max_consecutive_losses": max_consecutive_losses,
        "max_positions": result[
            "max_positions"
        ],
        "max_deployed": result[
            "max_deployed"
        ],
    }


# ============================================================
# PERIOD
# ============================================================

def get_indices(
    calendar,
    start_date,
    end_date
):

    mask = (
        (calendar >= pd.Timestamp(start_date))
        &
        (calendar <= pd.Timestamp(end_date))
    )

    indices = np.where(mask)[0]

    if len(indices) < 2:
        return None

    return (
        int(indices[0]),
        int(indices[-1])
    )


def run_period(
    name,
    mapped,
    calendar,
    start_date,
    end_date
):

    indices = get_indices(
        calendar,
        start_date,
        end_date
    )

    if indices is None:
        raise RuntimeError(
            f"No sufficient data for {name}"
        )

    start_i, end_i = indices

    result = simulate(
        mapped,
        calendar,
        start_i,
        end_i
    )

    metrics = calculate_metrics(
        result
    )

    metrics.update({
        "period": name,
        "start_date": calendar[start_i],
        "end_date": calendar[end_i],
    })

    return result, metrics


# ============================================================
# PRINT
# ============================================================

def print_metrics(metrics):

    print()
    print("=" * 80)
    print(metrics["period"])
    print("=" * 80)

    print(
        f"Period:          "
        f"{metrics['start_date'].date()} "
        f"-> "
        f"{metrics['end_date'].date()}"
    )

    print(
        f"Final equity:    "
        f"£{metrics['final_equity']:,.2f}"
    )

    print(
        f"Net profit:      "
        f"£{metrics['net_profit']:,.2f}"
    )

    print(
        f"Return:          "
        f"{metrics['return_pct']:.2f}%"
    )

    print(
        f"Max drawdown:    "
        f"{metrics['max_drawdown_pct']:.2f}%"
    )

    print(
        f"Sharpe:          "
        f"{metrics['sharpe']:.2f}"
    )

    print(
        f"Trades:          "
        f"{metrics['trades']:,}"
    )

    print(
        f"Win rate:        "
        f"{metrics['win_rate']:.2f}%"
    )

    pf = metrics["profit_factor"]

    print(
        f"Profit factor:   "
        f"{pf:.2f}"
        if np.isfinite(pf)
        else "Profit factor:   inf"
    )

    print(
        f"Avg win:         "
        f"£{metrics['avg_win']:,.2f}"
    )

    print(
        f"Avg loss:        "
        f"£{metrics['avg_loss']:,.2f}"
    )

    print(
        f"Payoff ratio:    "
        f"{metrics['payoff_ratio']:.2f}"
    )

    print(
        f"Largest winner:  "
        f"£{metrics['largest_winner']:,.2f}"
    )

    print(
        f"Largest loser:   "
        f"£{metrics['largest_loser']:,.2f}"
    )

    print(
        f"Avg hold:        "
        f"{metrics['avg_hold_days']:.1f} days"
    )

    print(
        f"Max hold:        "
        f"{metrics['max_hold_days']:.0f} days"
    )

    print(
        f"Max loss streak: "
        f"{metrics['max_consecutive_losses']}"
    )

    print(
        f"Max positions:   "
        f"{metrics['max_positions']}"
    )


# ============================================================
# SAVE
# ============================================================

def save_result(
    result,
    metrics,
    filename_prefix
):

    equity_df = pd.DataFrame({
        "date": result["dates"],
        "equity": result["equity"],
    })

    equity_df.to_csv(
        OUTPUT_DIR
        / f"{filename_prefix}_equity.csv",
        index=False
    )

    if result["trades"]:

        pd.DataFrame(
            result["trades"]
        ).to_csv(
            OUTPUT_DIR
            / f"{filename_prefix}_trades.csv",
            index=False
        )

    return equity_df


# ============================================================
# MAIN
# ============================================================

def main():

    started = time.time()

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    print()
    print("=" * 100)
    print("FROZEN RSI + SUPERTREND VALIDATION")
    print("=" * 100)

    print()
    print("Strategy:")
    print(
        f"  SuperTrend: ATR {ATR_PERIOD}, "
        f"multiplier {SUPERTREND_MULTIPLIER}"
    )
    print(
        f"  RSI: {RSI_PERIOD}"
    )
    print(
        f"  RSI OB={RSI_OB}, OS={RSI_OS}"
    )

    print()
    print("Risk:")
    print(
        f"  Risk/trade: {RISK_PER_TRADE:.1%}"
    )
    print(
        f"  Hard stop: {STOP_ATR_MULTIPLIER:.1f} ATR"
    )
    print(
        f"  Max position: {MAX_POSITION_PCT:.0%}"
    )
    print(
        f"  Max positions: {MAX_POSITIONS}"
    )

    # --------------------------------------------------------
    # LOAD
    # --------------------------------------------------------

    tickers = load_watchlist()

    print()
    print(
        f"Watchlist: {len(tickers)} tickers"
    )

    data = prepare_all(
        tickers
    )

    if not data:
        raise RuntimeError(
            "No usable stock data found."
        )

    # --------------------------------------------------------
    # CALENDAR
    # --------------------------------------------------------

    print()
    print("Building universal calendar...")

    calendar = create_calendar(
        data
    )

    print(
        f"Calendar: {len(calendar):,} dates"
    )

    print(
        f"Range: "
        f"{calendar[0].date()} "
        f"-> "
        f"{calendar[-1].date()}"
    )

    mapped = map_calendar(
        data,
        calendar
    )

    # --------------------------------------------------------
    # DEVELOPMENT
    # --------------------------------------------------------

    development_result, development_metrics = (
        run_period(
            "DEVELOPMENT 2015-2022",
            mapped,
            calendar,
            "2015-01-01",
            "2022-12-31"
        )
    )

    print_metrics(
        development_metrics
    )

    save_result(
        development_result,
        development_metrics,
        "development_2015_2022"
    )

    # --------------------------------------------------------
    # STRICT OOS
    # --------------------------------------------------------

    oos_result, oos_metrics = (
        run_period(
            "STRICT OOS 2023-2026",
            mapped,
            calendar,
            "2023-01-01",
            END_DATE
        )
    )

    print_metrics(
        oos_metrics
    )

    save_result(
        oos_result,
        oos_metrics,
        "oos_2023_2026"
    )

    # --------------------------------------------------------
    # YEARLY TESTS
    # --------------------------------------------------------

    yearly = []

    for year in range(
        2021,
        2027
    ):

        end = (
            f"{year}-12-31"
            if year < 2026
            else END_DATE
        )

        result, metrics = run_period(
            f"CALENDAR YEAR {year}",
            mapped,
            calendar,
            f"{year}-01-01",
            end
        )

        print_metrics(
            metrics
        )

        yearly.append(
            metrics
        )

        save_result(
            result,
            metrics,
            f"year_{year}"
        )

    # --------------------------------------------------------
    # SAVE SUMMARIES
    # --------------------------------------------------------

    pd.DataFrame([
        development_metrics,
        oos_metrics
    ]).to_csv(
        OUTPUT_DIR
        / "oos_summary.csv",
        index=False
    )

    pd.DataFrame(
        yearly
    ).to_csv(
        OUTPUT_DIR
        / "walk_forward_summary.csv",
        index=False
    )

    # --------------------------------------------------------
    # COMBINED OOS TRADES
    # --------------------------------------------------------

    if oos_result["trades"]:

        pd.DataFrame(
            oos_result["trades"]
        ).to_csv(
            OUTPUT_DIR
            / "oos_trades.csv",
            index=False
        )

    # --------------------------------------------------------
    # YEARLY RESULTS
    # --------------------------------------------------------

    pd.DataFrame(
        yearly
    ).to_csv(
        OUTPUT_DIR
        / "yearly_results.csv",
        index=False
    )

    # --------------------------------------------------------
    # CHARTS
    # --------------------------------------------------------

    try:

        # OOS equity
        plt.figure(
            figsize=(12, 6)
        )

        plt.plot(
            oos_result["dates"],
            oos_result["equity"]
        )

        plt.title(
            "Strict OOS Equity — 2023-2026"
        )

        plt.xlabel("Date")
        plt.ylabel("Equity (£)")
        plt.grid(
            True,
            alpha=0.25
        )

        plt.tight_layout()

        plt.savefig(
            OUTPUT_DIR
            / "oos_equity.png",
            dpi=150
        )

        plt.close()

        # OOS drawdown
        equity = oos_result[
            "equity"
        ]

        peak = np.maximum.accumulate(
            equity
        )

        drawdown = (
            equity / peak - 1
        ) * 100

        plt.figure(
            figsize=(12, 5)
        )

        plt.plot(
            oos_result["dates"],
            drawdown
        )

        plt.title(
            "Strict OOS Drawdown"
        )

        plt.xlabel("Date")
        plt.ylabel("Drawdown (%)")
        plt.grid(
            True,
            alpha=0.25
        )

        plt.tight_layout()

        plt.savefig(
            OUTPUT_DIR
            / "oos_drawdown.png",
            dpi=150
        )

        plt.close()

    except Exception as e:

        print(
            f"Chart generation skipped: {e}"
        )

    # --------------------------------------------------------
    # FINAL SUMMARY
    # --------------------------------------------------------

    print()
    print("=" * 100)
    print("FINAL VALIDATION SUMMARY")
    print("=" * 100)

    print()
    print(
        "FROZEN PARAMETERS:"
    )

    print(
        f"  RSI = {RSI_PERIOD}"
    )

    print(
        f"  OB = {RSI_OB}"
    )

    print(
        f"  OS = {RSI_OS}"
    )

    print(
        f"  SuperTrend = "
        f"{ATR_PERIOD} / "
        f"{SUPERTREND_MULTIPLIER}"
    )

    print(
        f"  Stop = "
        f"{STOP_ATR_MULTIPLIER} ATR"
    )

    print()
    print(
        "STRICT OOS:"
    )

    print(
        f"  Return:        "
        f"{oos_metrics['return_pct']:.2f}%"
    )

    print(
        f"  Max DD:        "
        f"{oos_metrics['max_drawdown_pct']:.2f}%"
    )

    print(
        f"  Sharpe:        "
        f"{oos_metrics['sharpe']:.2f}"
    )

    print(
        f"  Profit Factor: "
        f"{oos_metrics['profit_factor']:.2f}"
    )

    print(
        f"  Trades:        "
        f"{oos_metrics['trades']}"
    )

    print()
    print(
        "No parameter optimisation was performed."
    )

    print()
    print(
        f"Results: "
        f"{OUTPUT_DIR.resolve()}"
    )

    print()
    print(
        f"Runtime: "
        f"{(time.time() - started) / 60:.2f} minutes"
    )

    print()
    print("=" * 100)


if __name__ == "__main__":
    main()
