#!/usr/bin/env python3
"""
EDGE JOURNAL - Daily Trade Signal Scanner
Run this every morning at 8:00 AM (before market opens)
Identifies 3-5 entry opportunities for the day
"""

import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

DATA_DIR = Path("data/raw")
WATCHLIST_FILE = Path("watchlist.txt")

# ============================================================
# INDICATORS
# ============================================================


def calculate_atr(df, period=7):
    """Calculate ATR"""
    df = df.copy()
    df["high_low"] = df["High"] - df["Low"]
    df["high_close"] = abs(df["High"] - df["Close"].shift())
    df["low_close"] = abs(df["Low"] - df["Close"].shift())
    df["tr"] = df[["high_low", "high_close", "low_close"]].max(axis=1)
    df["atr"] = df["tr"].rolling(window=period).mean()
    return df["atr"]


def calculate_rsi(df, period=14):
    """Calculate RSI"""
    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_supertrend(df, atr_period=7, multiplier=2.5):
    """Calculate SuperTrend"""
    df = df.copy()

    df["atr"] = calculate_atr(df, atr_period)
    df["atr"] = df["atr"].bfill().fillna(df["atr"].mean())

    df["hl_avg"] = (df["High"] + df["Low"]) / 2
    df["basic_ub"] = df["hl_avg"] + multiplier * df["atr"]
    df["basic_lb"] = df["hl_avg"] - multiplier * df["atr"]

    close = df["Close"].values
    basic_ub = df["basic_ub"].values
    basic_lb = df["basic_lb"].values

    final_ub = np.zeros(len(df))
    final_lb = np.zeros(len(df))

    final_ub[0] = basic_ub[0]
    final_lb[0] = basic_lb[0]

    for i in range(1, len(df)):
        if basic_ub[i] < final_ub[i - 1] or close[i - 1] > final_ub[i - 1]:
            final_ub[i] = basic_ub[i]
        else:
            final_ub[i] = final_ub[i - 1]

        if basic_lb[i] > final_lb[i - 1] or close[i - 1] < final_lb[i - 1]:
            final_lb[i] = basic_lb[i]
        else:
            final_lb[i] = final_lb[i - 1]

    df["final_ub"] = final_ub
    df["final_lb"] = final_lb

    trend = np.zeros(len(df))
    trend[0] = 1

    for i in range(1, len(df)):
        if trend[i - 1] == 1:
            if close[i] <= final_lb[i]:
                trend[i] = -1
            else:
                trend[i] = 1
        else:
            if close[i] >= final_ub[i]:
                trend[i] = 1
            else:
                trend[i] = -1

    df["st_trend"] = trend

    signals = np.zeros(len(df))
    for i in range(1, len(df)):
        if trend[i] != trend[i - 1]:
            signals[i] = 1 if trend[i] == 1 else -1

    df["st_signal"] = signals

    return df


def load_from_cache(ticker):
    """Load ticker data from cache"""
    parquet_path = DATA_DIR / f"{ticker}.parquet"

    if not parquet_path.exists():
        return None

    try:
        df = pd.read_parquet(parquet_path)

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)

        return df
    except:
        return None


# ============================================================
# MAIN SCAN
# ============================================================


def scan_for_signals():
    """Scan watchlist for entry signals"""

    if not WATCHLIST_FILE.exists():
        print("❌ watchlist.txt not found")
        return

    with open(WATCHLIST_FILE) as f:
        tickers = [
            line.strip().upper()
            for line in f
            if line.strip() and not line.startswith("#")
        ]

    print()
    print("=" * 100)
    print("🚀 EDGE JOURNAL - DAILY SIGNAL SCAN")
    print("=" * 100)
    print(f"📅 Date: {datetime.now().strftime('%A, %B %d, %Y at %H:%M')}")
    print(f"📊 Scanning: {len(tickers)} stocks")
    print("=" * 100)
    print()

    signals = []

    for ticker in tickers:
        df = load_from_cache(ticker)

        if df is None or len(df) < 50:
            continue

        # Calculate indicators
        df = calculate_supertrend(df, atr_period=7, multiplier=2.5)
        df["rsi"] = calculate_rsi(df, period=14)

        # Get latest values
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        signal = latest["st_signal"]
        trend = latest["st_trend"]
        rsi = latest["rsi"]
        price = latest["Close"]

        # BUY SIGNAL: SuperTrend flipped to up AND RSI < 85
        if signal == 1 and not pd.isna(rsi) and rsi < 85:

            signals.append(
                {
                    "ticker": ticker,
                    "price": round(price, 2),
                    "rsi": round(rsi, 1),
                    "trend_strength": "STRONG" if trend > 0 else "WEAK",
                    "signal_strength": (
                        "FRESH" if prev["st_signal"] == 0 else "CONTINUING"
                    ),
                    "volume_trend": (
                        "UP"
                        if latest["Volume"] > df["Volume"].tail(20).mean()
                        else "DOWN"
                    ),
                    "atr": round(latest["atr"], 2),
                    "support": round(latest["final_lb"], 2),
                    "resistance": round(latest["final_ub"], 2),
                    "entry_price": round(price, 2),
                    "stop_loss": round(price * 0.98, 2),  # 2% below entry
                    "target_1": round(price * 1.02, 2),  # 2% above entry
                    "target_2": round(price * 1.05, 2),  # 5% above entry
                }
            )

    # Sort by RSI (lower is safer entry)
    signals.sort(key=lambda x: x["rsi"])

    # ========================================================
    # DISPLAY RESULTS
    # ========================================================

    if not signals:
        print("❌ NO SIGNALS FOUND TODAY")
        print()
        print("Possible reasons:")
        print("  • Market trending sideways (low volatility)")
        print("  • Recent selloff (too many shorts)")
        print("  • Early in recovery (RSI still low)")
        print()
        print("Action: Check back tomorrow or expand watchlist")
        print()
        return

    print(f"✅ FOUND {len(signals)} BUY SIGNALS")
    print()

    # Show top 5 signals
    print("=" * 100)
    print("🎯 TOP SIGNALS (Best to Worst)")
    print("=" * 100)
    print()

    for rank, sig in enumerate(signals[:5], 1):
        print(f"{rank}. {sig['ticker']}")
        print(f"   {'─' * 90}")
        print(f"   💰 Current Price:        ${sig['price']}")
        print(f"   📊 RSI:                  {sig['rsi']:.1f} (< 85 = OK)")
        print(f"   🔥 Trend Strength:       {sig['trend_strength']}")
        print(f"   ⚡ Signal Quality:        {sig['signal_strength']}")
        print(f"   📈 Volume Trend:         {sig['volume_trend']}")
        print()
        print(f"   📌 TRADE PLAN:")
        print(f"      Entry:     ${sig['entry_price']}")
        print(f"      Stop Loss: ${sig['stop_loss']} (2% below entry)")
        print(f"      Target 1:  ${sig['target_1']} (2% profit)")
        print(f"      Target 2:  ${sig['target_2']} (5% profit)")
        print(
            f"      Risk/Reward: ~2.5x (Risk £{(sig['entry_price'] - sig['stop_loss']):.2f} for £{(sig['target_1'] - sig['entry_price']):.2f})"
        )
        print()

    # ========================================================
    # ACTION ITEMS
    # ========================================================

    print("=" * 100)
    print("✅ ACTION CHECKLIST")
    print("=" * 100)
    print()
    print("For each signal, verify BEFORE entering:")
    print()
    print("□ SIGNAL CONFIRMED")
    print("  → On Trading 212, check 15-min chart")
    print("  → Does it show uptrend (green SuperTrend line)?")
    print()
    print("□ MOMENTUM CHECK")
    print("  → Is RSI < 85? (Check RSI indicator)")
    print()
    print("□ VOLUME CHECK")
    print("  → Is volume above average today?")
    print("  → (Real move, not fake pump)")
    print()
    print("□ POSITION SIZE")
    print("  → Calculating shares to trade for 1% risk:")

    account_size = 10000  # Adjust to your account size
    risk_per_trade = account_size * 0.01  # 1% = £100

    for sig in signals[:3]:
        stop_distance = sig["entry_price"] - sig["stop_loss"]
        shares = int(risk_per_trade / stop_distance)
        potential_loss = shares * stop_distance
        potential_gain = shares * (sig["target_1"] - sig["entry_price"])

        print(f"\n  {sig['ticker']}:")
        print(f"     Shares to buy: {shares}")
        print(f"     Max loss: £{potential_loss:.2f} (acceptable)")
        print(f"     Max gain (T1): £{potential_gain:.2f}")

    print()
    print()
    print("□ NO OVERTRADING")
    print("  → Max 5 open positions at a time")
    print(f"  → You currently have: ? (Check Trading 212)")
    print()
    print("=" * 100)
    print()
    print("🚀 If all checks pass, execute ONE trade")
    print("   (Don't get greedy - quality over quantity)")
    print()
    print("=" * 100)
    print()


# ============================================================
# LOGGING
# ============================================================


def log_signal():
    """Save signals to file for tracking"""

    # Create log entry
    log_file = Path("daily_signals_log.txt")

    with open(log_file, "a") as f:
        f.write(f"\n{datetime.now().strftime('%Y-%m-%d %H:%M')} - Scan complete\n")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    scan_for_signals()
    log_signal()
