#!/usr/bin/env python3

"""
Comprehensive diagnostic analysis for the RSI + SuperTrend optimizer.

Candidate:
    RSI OB=60 / OS=45

Important:
    rsi_equity_curves.csv contains 28 separate equity curves.
    Each configuration has 2,942 observations.

This script:
    1. Filters the exact candidate equity curve.
    2. Reconstructs the optimizer trading calendar.
    3. Reconciles equity against the optimizer result.
    4. Analyses trades.
    5. Calculates yearly/monthly performance.
    6. Calculates drawdowns.
    7. Analyses exposure and concentration.
    8. Analyses market regimes.
    9. Produces CSV reports and PNG charts.

No market data is downloaded.
"""

from pathlib import Path
import warnings
import multiprocessing as mp

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from tqdm import tqdm

warnings.filterwarnings("ignore")


# ============================================================
# CONFIGURATION
# ============================================================

RESULTS_DIR = Path("results")
DATA_DIR = Path("data/raw")

TRADES_FILE = RESULTS_DIR / "rsi_all_trades.csv"
EQUITY_FILE = RESULTS_DIR / "rsi_equity_curves.csv"
SUMMARY_FILE = RESULTS_DIR / "rsi_optimization_summary.csv"

OUTPUT_DIR = RESULTS_DIR / "analysis"

CANDIDATE_OB = 60
CANDIDATE_OS = 45

INITIAL_CAPITAL = 5000.0

# This is the result reported by the optimizer run we are validating.
EXPECTED_FINAL_EQUITY = 9766.07

EXPECTED_EQUITY_POINTS = 2942

# Optimizer configuration
COMMISSION_RATE = 0.0010
SLIPPAGE_RATE = 0.0005

MAX_POSITIONS = 10


# ============================================================
# HELPERS
# ============================================================


def money(x):
    return f"£{x:,.2f}"


def pct(x):
    return f"{x:.2f}%"


def safe_div(a, b):
    if b == 0 or pd.isna(b):
        return np.nan
    return a / b


# ============================================================
# LOAD FILES
# ============================================================


def load_files():
    print("=" * 110)
    print("COMPREHENSIVE RSI + SUPERTREND STRATEGY ANALYSIS")
    print("=" * 110)
    print()

    required = [
        TRADES_FILE,
        EQUITY_FILE,
        SUMMARY_FILE,
    ]

    for f in required:
        if not f.exists():
            raise FileNotFoundError(f"Missing required file: {f}")
        print(f"✓ Found {f}")

    print()

    trades = pd.read_csv(TRADES_FILE)
    equity = pd.read_csv(EQUITY_FILE)
    summary = pd.read_csv(SUMMARY_FILE)

    print(f"✓ Trades loaded: {len(trades):,}")
    print(f"✓ Equity observations loaded: {len(equity):,}")
    print(f"✓ Optimization configurations loaded: {len(summary):,}")
    print()

    return trades, equity, summary


# ============================================================
# FILTER CANDIDATE
# ============================================================


def filter_candidate(trades, equity):
    print("=" * 110)
    print("CANDIDATE STRATEGY")
    print("=" * 110)

    candidate_trades = trades[
        (trades["rsi_ob"] == CANDIDATE_OB) & (trades["rsi_os"] == CANDIDATE_OS)
    ].copy()

    candidate_equity = equity[
        (equity["rsi_ob"] == CANDIDATE_OB) & (equity["rsi_os"] == CANDIDATE_OS)
    ].copy()

    candidate_equity = candidate_equity.sort_values("date").reset_index(drop=True)

    print(f"Candidate: RSI OB={CANDIDATE_OB}, OS={CANDIDATE_OS}")
    print(f"Candidate trades: {len(candidate_trades):,}")
    print(f"Candidate equity observations: {len(candidate_equity):,}")
    print()

    if len(candidate_equity) != EXPECTED_EQUITY_POINTS:
        raise ValueError(
            f"Expected {EXPECTED_EQUITY_POINTS:,} equity observations "
            f"but found {len(candidate_equity):,}."
        )

    return candidate_trades, candidate_equity


# ============================================================
# LOAD OPTIMIZER SUMMARY
# ============================================================


def verify_optimizer_result(summary):
    print("=" * 110)
    print("OPTIMIZER RESULT RECONCILIATION")
    print("=" * 110)

    row = summary[
        (summary["rsi_ob"] == CANDIDATE_OB) & (summary["rsi_os"] == CANDIDATE_OS)
    ]

    if row.empty:
        print("⚠ Candidate not found in optimization summary.")
        print()
        return None

    row = row.iloc[0]

    print(row.to_string())
    print()

    possible_final_columns = [
        "final_equity",
        "Final Equity",
        "final_equity_value",
    ]

    final_value = None

    for c in possible_final_columns:
        if c in row.index:
            final_value = float(row[c])
            break

    if final_value is not None:
        print(f"Optimizer final equity: {money(final_value)}")

        difference = final_value - EXPECTED_FINAL_EQUITY

        if abs(difference) < 0.10:
            print("✓ Matches expected optimizer result.")
        else:
            print(f"⚠ Difference from expected result: {money(difference)}")

    print()

    return row


# ============================================================
# RECONSTRUCT CALENDAR
# ============================================================


def read_one_parquet(path):
    """
    Read only dates from a cached Parquet file.

    Multiprocessing worker.
    """
    try:
        df = pd.read_parquet(path)

        if isinstance(df.index, pd.DatetimeIndex):
            dates = df.index

        elif "Date" in df.columns:
            dates = pd.to_datetime(df["Date"], errors="coerce")

        elif "date" in df.columns:
            dates = pd.to_datetime(df["date"], errors="coerce")

        else:
            return []

        dates = pd.DatetimeIndex(dates).dropna().normalize()

        return dates.tolist()

    except Exception:
        return []


def reconstruct_calendar(equity_length):
    """
    Reconstruct the optimizer's calendar from cached Parquet data.

    We do not assume a start date.

    Instead:
        - collect all available trading dates
        - sort/deduplicate
        - find a contiguous suffix of exactly the number of
          observations present in the optimizer equity curve

    This is designed to match the optimizer's universal calendar
    when the cached data has a broader historical range.
    """

    print("=" * 110)
    print("RECONSTRUCTING OPTIMIZER CALENDAR")
    print("=" * 110)

    files = sorted(DATA_DIR.glob("*.parquet"))

    if not files:
        raise FileNotFoundError(f"No Parquet files found in {DATA_DIR}")

    print(f"✓ Found {len(files):,} cached Parquet files")
    print(f"Calendar workers: {max(1, mp.cpu_count() - 1)}")
    print()

    workers = max(1, mp.cpu_count() - 1)

    with mp.get_context("spawn").Pool(workers) as pool:
        results = list(
            tqdm(
                pool.imap(read_one_parquet, files),
                total=len(files),
                desc="Reading cached dates",
                unit="stock",
            )
        )

    all_dates = []

    for dates in results:
        all_dates.extend(dates)

    if not all_dates:
        raise RuntimeError("Could not reconstruct any trading dates.")

    calendar = pd.DatetimeIndex(sorted(set(all_dates)))

    print(f"All cached trading dates: {len(calendar):,}")
    print(f"Full cached range: " f"{calendar.min().date()} → {calendar.max().date()}")

    # --------------------------------------------------------
    # Find exact-length calendar.
    #
    # The optimizer's equity curve contains N observations.
    # If the cache contains earlier historical data, take the
    # latest N dates.
    # --------------------------------------------------------

    if len(calendar) < equity_length:
        raise RuntimeError(
            f"Only {len(calendar):,} calendar dates available, "
            f"but equity curve needs {equity_length:,}."
        )

    if len(calendar) == equity_length:
        optimizer_calendar = calendar

    else:
        optimizer_calendar = calendar[-equity_length:]

    print(f"Selected optimizer calendar: " f"{len(optimizer_calendar):,} dates")

    print(
        f"Optimizer calendar range: "
        f"{optimizer_calendar.min().date()} → "
        f"{optimizer_calendar.max().date()}"
    )

    if len(optimizer_calendar) != equity_length:
        raise RuntimeError(
            "Calendar reconstruction failed to produce the "
            "same number of observations as the equity curve."
        )

    print("✓ Calendar length matches equity curve exactly.")
    print()

    return optimizer_calendar


# ============================================================
# ATTACH DATES
# ============================================================


def attach_equity_dates(equity, calendar):
    equity = equity.copy()

    equity = equity.sort_values("date").reset_index(drop=True)

    numeric_dates = pd.to_numeric(equity["date"], errors="coerce")

    if numeric_dates.isna().any():
        raise ValueError("Equity 'date' column contains non-numeric values.")

    numeric_dates = numeric_dates.astype(int)

    if numeric_dates.min() != 0:
        raise ValueError(
            f"Expected equity date index to start at 0, "
            f"but found {numeric_dates.min()}."
        )

    if numeric_dates.max() != len(calendar) - 1:
        raise ValueError(
            f"Equity date index ends at {numeric_dates.max()}, "
            f"but calendar ends at {len(calendar) - 1}."
        )

    equity["actual_date"] = [calendar[i] for i in numeric_dates]

    return equity


# ============================================================
# EQUITY ANALYSIS
# ============================================================


def calculate_equity_metrics(equity):
    eq = equity.copy()

    eq["equity"] = pd.to_numeric(eq["equity"], errors="coerce")

    eq = eq.sort_values("actual_date").reset_index(drop=True)

    initial = float(eq["equity"].iloc[0])
    final = float(eq["equity"].iloc[-1])

    net_profit = final - initial

    total_return = safe_div(net_profit, initial)

    running_max = eq["equity"].cummax()

    drawdown = (eq["equity"] / running_max) - 1.0

    max_drawdown = drawdown.min()

    daily_returns = (
        eq["equity"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    )

    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
    else:
        sharpe = np.nan

    eq["running_max"] = running_max
    eq["drawdown"] = drawdown
    eq["daily_return"] = eq["equity"].pct_change()

    metrics = {
        "initial_equity": initial,
        "final_equity": final,
        "net_profit": net_profit,
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
    }

    return eq, metrics


# ============================================================
# TRADE METRICS
# ============================================================


def prepare_trades(trades):
    t = trades.copy()

    t["entry_date"] = pd.to_datetime(t["entry_date"])
    t["exit_date"] = pd.to_datetime(t["exit_date"])

    numeric_cols = [
        "entry_price",
        "exit_price",
        "shares",
        "entry_value",
        "exit_value",
        "entry_commission",
        "exit_commission",
        "net_pnl",
    ]

    for c in numeric_cols:
        t[c] = pd.to_numeric(t[c], errors="coerce")

    t["holding_days"] = (t["exit_date"] - t["entry_date"]).dt.days

    t["winner"] = t["net_pnl"] > 0
    t["loser"] = t["net_pnl"] < 0

    return t


def calculate_trade_metrics(trades):
    t = trades.copy()

    pnl = t["net_pnl"]

    winners = pnl[pnl > 0]
    losers = pnl[pnl < 0]

    gross_profit = winners.sum()
    gross_loss = abs(losers.sum())

    profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.inf

    win_rate = len(winners) / len(pnl) if len(pnl) > 0 else np.nan

    avg_winner = winners.mean() if len(winners) else np.nan
    avg_loser = losers.mean() if len(losers) else np.nan

    payoff_ratio = (
        avg_winner / abs(avg_loser)
        if pd.notna(avg_winner) and pd.notna(avg_loser) and avg_loser != 0
        else np.nan
    )

    metrics = {
        "trades": len(t),
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "average_trade": pnl.mean(),
        "median_trade": pnl.median(),
        "average_winner": avg_winner,
        "average_loser": avg_loser,
        "largest_winner": winners.max() if len(winners) else np.nan,
        "largest_loser": losers.min() if len(losers) else np.nan,
        "payoff_ratio": payoff_ratio,
        "median_holding_days": t["holding_days"].median(),
        "average_holding_days": t["holding_days"].mean(),
        "max_holding_days": t["holding_days"].max(),
        "min_holding_days": t["holding_days"].min(),
    }

    return metrics


# ============================================================
# CONSECUTIVE LOSSES
# ============================================================


def max_consecutive_losses(trades):
    t = trades.sort_values(["exit_date", "entry_date"]).copy()

    streak = 0
    maximum = 0

    for value in t["net_pnl"]:
        if value < 0:
            streak += 1
            maximum = max(maximum, streak)
        else:
            streak = 0

    return maximum


# ============================================================
# YEARLY ANALYSIS
# ============================================================


def yearly_analysis(trades):
    t = trades.copy()

    t["year"] = t["exit_date"].dt.year

    result = (
        t.groupby("year")
        .agg(
            pnl=("net_pnl", "sum"),
            trades=("net_pnl", "count"),
            wins=("winner", "sum"),
            avg_trade=("net_pnl", "mean"),
            avg_holding_days=("holding_days", "mean"),
        )
        .reset_index()
    )

    result["win_rate"] = result["wins"] / result["trades"]

    return result


# ============================================================
# MONTHLY ANALYSIS
# ============================================================


def monthly_analysis(equity):
    e = equity.copy()

    e["month"] = e["actual_date"].dt.to_period("M")

    monthly = (
        e.groupby("month")
        .agg(
            start_equity=("equity", "first"),
            end_equity=("equity", "last"),
        )
        .reset_index()
    )

    monthly["return"] = monthly["end_equity"] / monthly["start_equity"] - 1

    monthly["pnl"] = monthly["end_equity"] - monthly["start_equity"]

    monthly["month"] = monthly["month"].astype(str)

    return monthly


# ============================================================
# DRAWDOWN PERIODS
# ============================================================


def drawdown_periods(equity):
    e = equity.copy()

    in_drawdown = e["drawdown"] < 0

    periods = []

    start = None
    trough = None
    trough_dd = 0.0

    for i, active in enumerate(in_drawdown):

        if active and start is None:
            start = i
            trough = i
            trough_dd = e.loc[i, "drawdown"]

        elif active:
            if e.loc[i, "drawdown"] < trough_dd:
                trough = i
                trough_dd = e.loc[i, "drawdown"]

        elif not active and start is not None:

            periods.append(
                {
                    "start": e.loc[start, "actual_date"],
                    "trough": e.loc[trough, "actual_date"],
                    "recovery": e.loc[i, "actual_date"],
                    "drawdown": trough_dd,
                    "duration_days": (
                        e.loc[i, "actual_date"] - e.loc[start, "actual_date"]
                    ).days,
                }
            )

            start = None
            trough = None
            trough_dd = 0.0

    if start is not None:

        periods.append(
            {
                "start": e.loc[start, "actual_date"],
                "trough": e.loc[trough, "actual_date"],
                "recovery": pd.NaT,
                "drawdown": trough_dd,
                "duration_days": (
                    e.loc[len(e) - 1, "actual_date"] - e.loc[start, "actual_date"]
                ).days,
            }
        )

    result = pd.DataFrame(periods)

    if not result.empty:
        result = result.sort_values("drawdown").reset_index(drop=True)

    return result


# ============================================================
# REGIME ANALYSIS
# ============================================================


def regime_analysis(trades):
    t = trades.copy()

    periods = {
        "2015-2019": ("2015-01-01", "2019-12-31"),
        "2020 COVID": ("2020-01-01", "2020-12-31"),
        "2021 Bull": ("2021-01-01", "2021-12-31"),
        "2022 Bear": ("2022-01-01", "2022-12-31"),
        "2023 Recovery": ("2023-01-01", "2023-12-31"),
        "2024-2025 Recent": ("2024-01-01", "2025-12-31"),
    }

    rows = []

    for name, (start, end) in periods.items():

        mask = (t["exit_date"] >= start) & (t["exit_date"] <= end)

        subset = t.loc[mask]

        if subset.empty:
            continue

        wins = subset[subset["net_pnl"] > 0]["net_pnl"]
        losses = subset[subset["net_pnl"] < 0]["net_pnl"]

        gross_profit = wins.sum()
        gross_loss = abs(losses.sum())

        pf = gross_profit / gross_loss if gross_loss > 0 else np.inf

        rows.append(
            {
                "period": name,
                "trades": len(subset),
                "pnl": subset["net_pnl"].sum(),
                "win_rate": (len(wins) / len(subset) if len(subset) else np.nan),
                "profit_factor": pf,
                "average_trade": subset["net_pnl"].mean(),
                "average_holding_days": subset["holding_days"].mean(),
            }
        )

    return pd.DataFrame(rows)


# ============================================================
# TICKER ANALYSIS
# ============================================================


def ticker_analysis(trades):
    t = trades.copy()

    result = (
        t.groupby("ticker")
        .agg(
            pnl=("net_pnl", "sum"),
            trades=("net_pnl", "count"),
            wins=("winner", "sum"),
            avg_trade=("net_pnl", "mean"),
        )
        .reset_index()
    )

    result["win_rate"] = result["wins"] / result["trades"]

    return result.sort_values("pnl", ascending=False).reset_index(drop=True)


def concentration_analysis(ticker):
    t = ticker.copy()

    profitable = t[t["pnl"] > 0].copy()

    gross_profit = profitable["pnl"].sum()

    if gross_profit > 0:
        top5_gross_profit_share = profitable.head(5)["pnl"].sum() / gross_profit

        top10_gross_profit_share = profitable.head(10)["pnl"].sum() / gross_profit
    else:
        top5_gross_profit_share = np.nan
        top10_gross_profit_share = np.nan

    absolute_pnl = t["pnl"].abs().sum()

    top10_absolute_share = (
        t.head(10)["pnl"].abs().sum() / absolute_pnl if absolute_pnl > 0 else np.nan
    )

    return {
        "unique_tickers": len(t),
        "top5_gross_profit_share": top5_gross_profit_share,
        "top10_gross_profit_share": top10_gross_profit_share,
        "top10_absolute_pnl_share": top10_absolute_share,
    }


# ============================================================
# EXPOSURE
# ============================================================


def calculate_exposure(trades, calendar):
    """
    Reconstruct approximate simultaneous open positions
    using entry/exit intervals.

    This is trade-level exposure, not mark-to-market portfolio
    exposure.
    """

    if trades.empty:
        return pd.DataFrame()

    events = []

    for _, row in trades.iterrows():

        events.append(
            {
                "date": row["entry_date"],
                "change": 1,
                "capital": row["entry_value"],
            }
        )

        events.append(
            {
                "date": row["exit_date"],
                "change": -1,
                "capital": -row["exit_value"],
            }
        )

    events = pd.DataFrame(events)

    if events.empty:
        return pd.DataFrame()

    events = (
        events.groupby("date")
        .agg(
            position_change=("change", "sum"),
            capital_change=("capital", "sum"),
        )
        .sort_index()
    )

    events["positions"] = events["position_change"].cumsum()
    events["capital_deployed"] = events["capital_change"].cumsum()

    return events.reset_index()


# ============================================================
# RECONCILIATION
# ============================================================


def reconcile(trades, equity, optimizer_row):
    print("=" * 110)
    print("RECONCILIATION CHECKS")
    print("=" * 110)

    equity_initial = float(equity["equity"].iloc[0])
    equity_final = float(equity["equity"].iloc[-1])

    trade_pnl = float(trades["net_pnl"].sum())

    equity_change = equity_final - equity_initial

    print(f"Equity initial:       {money(equity_initial)}")
    print(f"Equity final:         {money(equity_final)}")
    print(f"Equity change:        {money(equity_change)}")
    print(f"Trade net P&L:        {money(trade_pnl)}")

    difference = equity_change - trade_pnl

    print(f"Difference:           {money(difference)}")

    if abs(difference) < 0.10:
        print("✓ Trade P&L reconciles with equity change.")
    else:
        print("⚠ Trade P&L does NOT exactly reconcile with " "equity change.")

    if optimizer_row is not None:

        final_columns = [
            "final_equity",
            "Final Equity",
            "final_equity_value",
        ]

        optimizer_final = None

        for c in final_columns:
            if c in optimizer_row.index:
                optimizer_final = float(optimizer_row[c])
                break

        if optimizer_final is not None:

            difference = equity_final - optimizer_final

            print(f"Optimizer final equity: " f"{money(optimizer_final)}")

            print(f"Equity CSV difference: " f"{money(difference)}")

            if abs(difference) < 0.10:
                print("✓ Equity curve reconciles with optimizer.")
            else:
                print("⚠ Equity curve does NOT reconcile with " "optimizer result.")

    print()


# ============================================================
# CHARTS
# ============================================================


def make_charts(equity, monthly, ticker, yearly, drawdowns):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 110)
    print("GENERATING CHARTS")
    print("=" * 110)

    # --------------------------------------------------------
    # Equity curve
    # --------------------------------------------------------

    plt.figure(figsize=(14, 7))

    plt.plot(
        equity["actual_date"],
        equity["equity"],
        linewidth=1.5,
    )

    plt.title(
        f"RSI + SuperTrend Equity Curve " f"(OB={CANDIDATE_OB}, OS={CANDIDATE_OS})"
    )

    plt.xlabel("Date")
    plt.ylabel("Portfolio Equity (£)")
    plt.grid(alpha=0.25)
    plt.tight_layout()

    plt.savefig(
        OUTPUT_DIR / "equity_curve.png",
        dpi=150,
    )

    plt.close()

    # --------------------------------------------------------
    # Drawdown
    # --------------------------------------------------------

    plt.figure(figsize=(14, 6))

    plt.fill_between(
        equity["actual_date"],
        equity["drawdown"] * 100,
        0,
        alpha=0.3,
    )

    plt.title("Portfolio Drawdown")
    plt.xlabel("Date")
    plt.ylabel("Drawdown (%)")
    plt.grid(alpha=0.25)
    plt.tight_layout()

    plt.savefig(
        OUTPUT_DIR / "drawdown.png",
        dpi=150,
    )

    plt.close()

    # --------------------------------------------------------
    # Monthly returns
    # --------------------------------------------------------

    if not monthly.empty:

        plt.figure(figsize=(14, 7))

        values = monthly["return"] * 100

        plt.bar(
            monthly["month"],
            values,
        )

        plt.title("Monthly Returns")
        plt.xlabel("Month")
        plt.ylabel("Return (%)")
        plt.xticks(
            rotation=90,
            fontsize=7,
        )
        plt.grid(
            axis="y",
            alpha=0.25,
        )
        plt.tight_layout()

        plt.savefig(
            OUTPUT_DIR / "monthly_returns.png",
            dpi=150,
        )

        plt.close()

    # --------------------------------------------------------
    # Yearly P&L
    # --------------------------------------------------------

    if not yearly.empty:

        plt.figure(figsize=(12, 6))

        plt.bar(
            yearly["year"].astype(str),
            yearly["pnl"],
        )

        plt.title("Yearly Trading P&L")
        plt.xlabel("Year")
        plt.ylabel("P&L (£)")
        plt.grid(
            axis="y",
            alpha=0.25,
        )
        plt.tight_layout()

        plt.savefig(
            OUTPUT_DIR / "yearly_pnl.png",
            dpi=150,
        )

        plt.close()

    # --------------------------------------------------------
    # Top / bottom tickers
    # --------------------------------------------------------

    if not ticker.empty:

        plot_data = pd.concat(
            [
                ticker.head(10),
                ticker.tail(10),
            ]
        )

        plt.figure(figsize=(12, 8))

        plt.barh(
            plot_data["ticker"],
            plot_data["pnl"],
        )

        plt.title("Top and Bottom Tickers by Net P&L")

        plt.xlabel("Net P&L (£)")
        plt.ylabel("Ticker")

        plt.grid(
            axis="x",
            alpha=0.25,
        )

        plt.tight_layout()

        plt.savefig(
            OUTPUT_DIR / "ticker_pnl.png",
            dpi=150,
        )

        plt.close()

    # --------------------------------------------------------
    # Drawdown periods
    # --------------------------------------------------------

    if not drawdowns.empty:

        top = drawdowns.head(10).copy()

        plt.figure(figsize=(12, 7))

        plt.barh(
            range(len(top)),
            top["drawdown"] * 100,
        )

        plt.yticks(
            range(len(top)),
            [str(x.date()) for x in top["start"]],
        )

        plt.title("Largest Drawdown Periods")
        plt.xlabel("Drawdown (%)")

        plt.grid(
            axis="x",
            alpha=0.25,
        )

        plt.tight_layout()

        plt.savefig(
            OUTPUT_DIR / "drawdown_periods.png",
            dpi=150,
        )

        plt.close()

    print(f"✓ Charts saved to {OUTPUT_DIR}")

    print()


# ============================================================
# PRINT REPORT
# ============================================================


def print_report(
    equity_metrics,
    trade_metrics,
    yearly,
    monthly,
    regimes,
    ticker,
    concentration,
    exposure,
    drawdowns,
):
    print("=" * 110)
    print("FINAL STRATEGY DIAGNOSTICS")
    print("=" * 110)

    print()
    print("EQUITY")
    print("-" * 110)

    print(
        f"Test period: "
        f"{equity_metrics['start_date'].date()} → "
        f"{equity_metrics['end_date'].date()}"
    )

    print(f"Initial equity:      " f"{money(equity_metrics['initial_equity'])}")

    print(f"Final equity:        " f"{money(equity_metrics['final_equity'])}")

    print(f"Net profit:          " f"{money(equity_metrics['net_profit'])}")

    print(f"Total return:        " f"{pct(equity_metrics['total_return'] * 100)}")

    print(f"Maximum drawdown:    " f"{pct(equity_metrics['max_drawdown'] * 100)}")

    print(
        f"Sharpe:              " f"{equity_metrics['sharpe']:.2f}"
        if pd.notna(equity_metrics["sharpe"])
        else "Sharpe:              N/A"
    )

    print()
    print("TRADES")
    print("-" * 110)

    print(f"Trades:              " f"{trade_metrics['trades']:,}")

    print(f"Win rate:            " f"{pct(trade_metrics['win_rate'] * 100)}")

    print(f"Profit factor:       " f"{trade_metrics['profit_factor']:.2f}")

    print(f"Gross profit:        " f"{money(trade_metrics['gross_profit'])}")

    print(f"Gross loss:          " f"{money(trade_metrics['gross_loss'])}")

    print(f"Average trade:       " f"{money(trade_metrics['average_trade'])}")

    print(f"Median trade:        " f"{money(trade_metrics['median_trade'])}")

    print(f"Average winner:      " f"{money(trade_metrics['average_winner'])}")

    print(f"Average loser:       " f"{money(trade_metrics['average_loser'])}")

    print(f"Largest winner:      " f"{money(trade_metrics['largest_winner'])}")

    print(f"Largest loser:       " f"{money(trade_metrics['largest_loser'])}")

    print(f"Payoff ratio:        " f"{trade_metrics['payoff_ratio']:.2f}")

    print(f"Median holding:      " f"{trade_metrics['median_holding_days']:.1f} days")

    print(f"Average holding:     " f"{trade_metrics['average_holding_days']:.1f} days")

    print(f"Maximum holding:     " f"{trade_metrics['max_holding_days']} days")

    print(f"Max consecutive loss:" f" {max_consecutive_losses(
            CURRENT_TRADES
        )}")

    if exposure is not None and not exposure.empty:

        print()
        print("EXPOSURE")
        print("-" * 110)

        print(f"Maximum positions:   " f"{exposure['positions'].max():.0f}")

        print(f"Average positions:   " f"{exposure['positions'].mean():.2f}")

        print(f"Maximum deployed:    " f"{money(exposure['capital_deployed'].max())}")

    print()
    print("TICKER CONCENTRATION")
    print("-" * 110)

    print(f"Unique tickers:       " f"{concentration['unique_tickers']:,}")

    print(
        f"Top 5 gross-profit share:"
        f" {pct(concentration['top5_gross_profit_share'] * 100)}"
    )

    print(
        f"Top 10 gross-profit share:"
        f" {pct(concentration['top10_gross_profit_share'] * 100)}"
    )

    print(
        f"Top 10 absolute-P&L share:"
        f" {pct(concentration['top10_absolute_pnl_share'] * 100)}"
    )

    print()
    print("YEARLY RESULTS")
    print("-" * 110)

    if not yearly.empty:

        display = yearly.copy()

        display["pnl"] = display["pnl"].map(money)

        display["win_rate"] = (display["win_rate"] * 100).map(lambda x: f"{x:.1f}%")

        display["avg_trade"] = display["avg_trade"].map(money)

        print(
            display[
                [
                    "year",
                    "trades",
                    "pnl",
                    "win_rate",
                    "avg_trade",
                    "avg_holding_days",
                ]
            ].to_string(index=False)
        )

    print()
    print("REGIME RESULTS")
    print("-" * 110)

    if not regimes.empty:

        display = regimes.copy()

        display["pnl"] = display["pnl"].map(money)

        display["win_rate"] = (display["win_rate"] * 100).map(lambda x: f"{x:.1f}%")

        display["profit_factor"] = display["profit_factor"].map(lambda x: f"{x:.2f}")

        display["average_trade"] = display["average_trade"].map(money)

        print(display.to_string(index=False))

    print()
    print("LARGEST DRAWDOWNS")
    print("-" * 110)

    if not drawdowns.empty:

        display = drawdowns.head(10).copy()

        display["drawdown"] = (display["drawdown"] * 100).map(lambda x: f"{x:.2f}%")

        display["start"] = display["start"].dt.strftime("%Y-%m-%d")

        display["trough"] = display["trough"].dt.strftime("%Y-%m-%d")

        display["recovery"] = display["recovery"].apply(
            lambda x: x.strftime("%Y-%m-%d") if pd.notna(x) else "Not recovered"
        )

        print(
            display[
                [
                    "start",
                    "trough",
                    "recovery",
                    "drawdown",
                    "duration_days",
                ]
            ].to_string(index=False)
        )

    print()


# ============================================================
# VALIDATION CHECKS
# ============================================================


def validation_checks(
    equity_metrics,
    trade_metrics,
    candidate_equity,
    candidate_trades,
):
    print("=" * 110)
    print("VALIDATION CHECKS")
    print("=" * 110)

    checks = []

    # --------------------------------------------------------
    # Final equity
    # --------------------------------------------------------

    difference = equity_metrics["final_equity"] - EXPECTED_FINAL_EQUITY

    passed = abs(difference) < 0.10

    checks.append(
        {
            "check": "Final equity matches optimizer",
            "status": "PASS" if passed else "FAIL",
            "detail": money(difference),
        }
    )

    # --------------------------------------------------------
    # Equity points
    # --------------------------------------------------------

    passed = len(candidate_equity) == EXPECTED_EQUITY_POINTS

    checks.append(
        {
            "check": "Equity observations",
            "status": "PASS" if passed else "FAIL",
            "detail": f"{len(candidate_equity):,}",
        }
    )

    # --------------------------------------------------------
    # Candidate trade count
    # --------------------------------------------------------

    passed = len(candidate_trades) == 2048

    checks.append(
        {
            "check": "Candidate trade count",
            "status": "PASS" if passed else "REVIEW",
            "detail": f"{len(candidate_trades):,}",
        }
    )

    # --------------------------------------------------------
    # Profit factor
    # --------------------------------------------------------

    passed = trade_metrics["profit_factor"] > 1

    checks.append(
        {
            "check": "Profit factor > 1",
            "status": "PASS" if passed else "FAIL",
            "detail": f"{trade_metrics['profit_factor']:.2f}",
        }
    )

    # --------------------------------------------------------
    # Positive total return
    # --------------------------------------------------------

    passed = equity_metrics["total_return"] > 0

    checks.append(
        {
            "check": "Positive total return",
            "status": "PASS" if passed else "FAIL",
            "detail": pct(equity_metrics["total_return"] * 100),
        }
    )

    # --------------------------------------------------------
    # Drawdown
    # --------------------------------------------------------

    dd = equity_metrics["max_drawdown"]

    status = "PASS" if dd > -0.30 else "REVIEW"

    checks.append(
        {
            "check": "Maximum drawdown",
            "status": status,
            "detail": pct(dd * 100),
        }
    )

    # --------------------------------------------------------
    # Max positions
    # --------------------------------------------------------

    # Trade overlap calculation
    events = []

    for _, row in candidate_trades.iterrows():

        events.append((row["entry_date"], 1))

        events.append((row["exit_date"], -1))

    events.sort()

    current = 0
    maximum = 0

    for _, change in events:
        current += change
        maximum = max(maximum, current)

    passed = maximum <= MAX_POSITIONS

    checks.append(
        {
            "check": "Maximum position limit",
            "status": "PASS" if passed else "FAIL",
            "detail": f"{maximum} / {MAX_POSITIONS}",
        }
    )

    print()

    for check in checks:

        symbol = "✓" if check["status"] == "PASS" else "⚠"

        print(
            f"{symbol} "
            f"{check['check']:<35} "
            f"{check['status']:<7} "
            f"{check['detail']}"
        )

    print()

    return pd.DataFrame(checks)


# ============================================================
# SAVE REPORTS
# ============================================================


def save_outputs(
    equity,
    trades,
    yearly,
    monthly,
    regimes,
    ticker,
    drawdowns,
    exposure,
    checks,
):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    equity.to_csv(
        OUTPUT_DIR / "candidate_equity.csv",
        index=False,
    )

    trades.to_csv(
        OUTPUT_DIR / "candidate_trades.csv",
        index=False,
    )

    yearly.to_csv(
        OUTPUT_DIR / "yearly_results.csv",
        index=False,
    )

    monthly.to_csv(
        OUTPUT_DIR / "monthly_results.csv",
        index=False,
    )

    regimes.to_csv(
        OUTPUT_DIR / "regime_results.csv",
        index=False,
    )

    ticker.to_csv(
        OUTPUT_DIR / "ticker_results.csv",
        index=False,
    )

    drawdowns.to_csv(
        OUTPUT_DIR / "drawdown_periods.csv",
        index=False,
    )

    if exposure is not None:
        exposure.to_csv(
            OUTPUT_DIR / "exposure.csv",
            index=False,
        )

    checks.to_csv(
        OUTPUT_DIR / "validation_checks.csv",
        index=False,
    )

    print(f"✓ CSV reports saved to {OUTPUT_DIR}")


# ============================================================
# MAIN
# ============================================================

CURRENT_TRADES = None


def main():
    global CURRENT_TRADES

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    trades, equity, summary = load_files()

    candidate_trades, candidate_equity = filter_candidate(
        trades,
        equity,
    )

    CURRENT_TRADES = prepare_trades(candidate_trades)

    optimizer_row = verify_optimizer_result(summary)

    # --------------------------------------------------------
    # Calendar
    # --------------------------------------------------------

    calendar = reconstruct_calendar(len(candidate_equity))

    candidate_equity = attach_equity_dates(
        candidate_equity,
        calendar,
    )

    # --------------------------------------------------------
    # Equity
    # --------------------------------------------------------

    candidate_equity, equity_metrics = calculate_equity_metrics(candidate_equity)

    equity_metrics["start_date"] = candidate_equity["actual_date"].iloc[0]

    equity_metrics["end_date"] = candidate_equity["actual_date"].iloc[-1]

    # --------------------------------------------------------
    # Trades
    # --------------------------------------------------------

    trade_metrics = calculate_trade_metrics(CURRENT_TRADES)

    # --------------------------------------------------------
    # Reports
    # --------------------------------------------------------

    yearly = yearly_analysis(CURRENT_TRADES)

    monthly = monthly_analysis(candidate_equity)

    regimes = regime_analysis(CURRENT_TRADES)

    ticker = ticker_analysis(CURRENT_TRADES)

    concentration = concentration_analysis(ticker)

    exposure = calculate_exposure(
        CURRENT_TRADES,
        calendar,
    )

    drawdowns = drawdown_periods(candidate_equity)

    # --------------------------------------------------------
    # Reconciliation
    # --------------------------------------------------------

    reconcile(
        CURRENT_TRADES,
        candidate_equity,
        optimizer_row,
    )

    # --------------------------------------------------------
    # Console report
    # --------------------------------------------------------

    print_report(
        equity_metrics,
        trade_metrics,
        yearly,
        monthly,
        regimes,
        ticker,
        concentration,
        exposure,
        drawdowns,
    )

    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

    checks = validation_checks(
        equity_metrics,
        trade_metrics,
        candidate_equity,
        CURRENT_TRADES,
    )

    # --------------------------------------------------------
    # Charts
    # --------------------------------------------------------

    make_charts(
        candidate_equity,
        monthly,
        ticker,
        yearly,
        drawdowns,
    )

    # --------------------------------------------------------
    # Save CSVs
    # --------------------------------------------------------

    save_outputs(
        candidate_equity,
        CURRENT_TRADES,
        yearly,
        monthly,
        regimes,
        ticker,
        drawdowns,
        exposure,
        checks,
    )

    print("=" * 110)
    print("ANALYSIS COMPLETE")
    print("=" * 110)

    print()
    print(f"Results directory: {OUTPUT_DIR.resolve()}")
    print()


if __name__ == "__main__":
    mp.freeze_support()
    main()
