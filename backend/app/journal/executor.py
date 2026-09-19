"""
Trade Journal Executor
Logs every trade (entry/exit) and performs post-trade analysis
Critical for finding blind spots between backtest and reality
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np
import pandas as pd


class TradeJournal:
    """
    Log and track all trades: entry, exits, thesis, outcomes
    Enables post-mortem analysis: which setups work, which don't?
    """

    def __init__(self, journal_file: str = "data/trades_live.csv"):
        self.filepath = Path(journal_file)
        self.df = self._load_or_create()

    def _load_or_create(self) -> pd.DataFrame:
        """Load existing journal or create new one"""

        if self.filepath.exists():
            df = pd.read_csv(self.filepath)
            # Ensure datetime columns
            df["entry_date"] = pd.to_datetime(df["entry_date"])
            df["exit_date"] = pd.to_datetime(df["exit_date"], errors="coerce")
            # `source` is new - a journal CSV written before this field
            # existed has no column for it at all, not just empty values.
            # Every pre-existing row is a real trade this app or its
            # trader logged, i.e. "LIVE", not a manual what-if simulation.
            if "source" not in df.columns:
                df["source"] = "LIVE"
            return df

        # Create empty journal
        return pd.DataFrame(
            {
                "id": pd.Series(dtype="int64"),
                "ticker": pd.Series(dtype="str"),
                "entry_date": pd.Series(dtype="datetime64[ns]"),
                "entry_price": pd.Series(dtype="float64"),
                "entry_thesis": pd.Series(dtype="str"),
                "signal_strength": pd.Series(dtype="float64"),
                "stop_loss": pd.Series(dtype="float64"),
                "target_1": pd.Series(dtype="float64"),
                "target_2": pd.Series(dtype="float64"),
                "entry_status": pd.Series(dtype="str"),  # PENDING, TAKEN, SKIPPED
                "skip_reason": pd.Series(dtype="str"),  # Why didn't we take it?
                "actual_entry_date": pd.Series(dtype="datetime64[ns]"),
                "actual_entry_price": pd.Series(dtype="float64"),
                "exit_date": pd.Series(dtype="datetime64[ns]"),
                "exit_price": pd.Series(dtype="float64"),
                "exit_reason": pd.Series(dtype="str"),  # SL, TP1, TP2, TIMEOUT, MANUAL
                "holding_days": pd.Series(dtype="int64"),
                "pnl": pd.Series(dtype="float64"),
                "pnl_pct": pd.Series(dtype="float64"),
                "r_multiple": pd.Series(dtype="float64"),  # Risk multiples
                "notes": pd.Series(dtype="str"),
                "created_at": pd.Series(dtype="datetime64[ns]"),
                "source": pd.Series(dtype="str"),
            }
        )

    def log_signal(
        self,
        ticker: str,
        entry_date: datetime,
        entry_price: float,
        thesis: str,
        signal_strength: float,
        stop_loss: float,
        target_1: float,
        target_2: float,
        source: str = "LIVE",
    ) -> int:
        """
        Log a trading signal (entry opportunity found)
        Returns trade_id for later updates

        Args:
            source: Where this trade originated - `"LIVE"` (default) for a
                real signal/trade, `"MANUAL_SIMULATION"` for a resolved
                point-in-time replay logged via `POST /api/v1/journal/
                simulate`. Purely a tag: every other analysis method treats
                both the same unless a caller filters on it.
        """

        trade_id = len(self.df) + 1

        new_trade = {
            "id": trade_id,
            "ticker": ticker,
            "entry_date": entry_date,
            "entry_price": entry_price,
            "entry_thesis": thesis,
            "signal_strength": signal_strength,
            "stop_loss": stop_loss,
            "target_1": target_1,
            "target_2": target_2,
            "entry_status": "PENDING",
            "skip_reason": None,
            "actual_entry_date": None,
            "actual_entry_price": None,
            "exit_date": None,
            "exit_price": None,
            "exit_reason": None,
            "holding_days": None,
            "pnl": None,
            "pnl_pct": None,
            "r_multiple": None,
            "notes": "",
            "created_at": datetime.now(),
            "source": source,
        }

        self.df = pd.concat([self.df, pd.DataFrame([new_trade])], ignore_index=True)
        self._save()

        return trade_id

    def log_entry(
        self, trade_id: int, actual_entry_price: float, actual_entry_date: datetime
    ):
        """Log that we actually entered a trade"""

        mask = self.df["id"] == trade_id
        self.df.loc[mask, "entry_status"] = "TAKEN"
        self.df.loc[mask, "actual_entry_price"] = actual_entry_price
        self.df.loc[mask, "actual_entry_date"] = actual_entry_date

        self._save()

    def log_skip(self, trade_id: int, skip_reason: str):
        """Log that we skipped a signal"""

        mask = self.df["id"] == trade_id
        self.df.loc[mask, "entry_status"] = "SKIPPED"
        self.df.loc[mask, "skip_reason"] = skip_reason

        self._save()

    def log_exit(
        self,
        trade_id: int,
        exit_date: datetime,
        exit_price: float,
        exit_reason: str,
        notes: str = "",
    ):
        """
        Log trade exit with outcome
        exit_reason: 'SL', 'TP1', 'TP2', 'TIMEOUT', 'MANUAL'
        """

        mask = self.df["id"] == trade_id
        trade = self.df.loc[mask].iloc[0]

        if trade["entry_status"] != "TAKEN":
            raise ValueError(f"Cannot exit trade {trade_id}: not marked as taken")

        entry_price = trade["actual_entry_price"]
        pnl = exit_price - entry_price
        pnl_pct = (exit_price - entry_price) / entry_price if entry_price else 0

        # Risk multiple: how many times the initial stop loss distance?
        risk_amount = entry_price - trade["stop_loss"]
        r_multiple = pnl / risk_amount if risk_amount != 0 else 0

        holding_days = (
            (exit_date - trade["actual_entry_date"]).days
            if pd.notna(trade["actual_entry_date"])
            else None
        )

        self.df.loc[mask, "exit_date"] = exit_date
        self.df.loc[mask, "exit_price"] = exit_price
        self.df.loc[mask, "exit_reason"] = exit_reason
        self.df.loc[mask, "pnl"] = pnl
        self.df.loc[mask, "pnl_pct"] = pnl_pct
        self.df.loc[mask, "r_multiple"] = r_multiple
        self.df.loc[mask, "holding_days"] = holding_days
        self.df.loc[mask, "notes"] = notes

        self._save()

    def _save(self):
        """Save journal to CSV"""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.df.to_csv(self.filepath, index=False)

    # ========== ANALYSIS ==========

    def analyze_all_trades(self) -> Dict:
        """Comprehensive trade analysis"""

        taken = self.df[self.df["entry_status"] == "TAKEN"]
        exited = taken[taken["exit_date"].notna()]

        if len(exited) == 0:
            return {"error": "No completed trades yet"}

        winners = exited[exited["pnl"] > 0]
        losers = exited[exited["pnl"] < 0]

        return {
            "total_trades": len(taken),
            "completed_trades": len(exited),
            "open_trades": len(taken) - len(exited),
            "win_rate": len(winners) / len(exited) if len(exited) > 0 else 0,
            "profit_factor": (
                winners["pnl"].sum() / abs(losers["pnl"].sum())
                if len(losers) > 0 and losers["pnl"].sum() != 0
                else 0
            ),
            "avg_winner": winners["pnl"].mean() if len(winners) > 0 else 0,
            "avg_loser": losers["pnl"].mean() if len(losers) > 0 else 0,
            "avg_pnl": exited["pnl"].mean(),
            "median_pnl": exited["pnl"].median(),
            "avg_r_multiple": exited["r_multiple"].mean(),
            "max_consecutive_losses": self._max_consecutive_losses(exited),
            "max_drawdown": self._calculate_max_drawdown(exited),
            "avg_holding_days": (
                exited["holding_days"].mean()
                if exited["holding_days"].notna().any()
                else None
            ),
        }

    def analyze_skipped_signals(self) -> Dict:
        """CRITICAL: Analyze signals we DIDN'T take - did they win?"""

        skipped = self.df[self.df["entry_status"] == "SKIPPED"].copy()

        if len(skipped) == 0:
            return {"error": "No skipped signals"}

        # This requires knowing what happened to prices after skip date
        # For now, return summary of why we skipped

        skip_reasons = skipped["skip_reason"].value_counts().to_dict()

        return {
            "total_skipped": len(skipped),
            "skip_reasons": skip_reasons,
            "avg_signal_strength_skipped": skipped["signal_strength"].mean(),
            "note": "Skipped signal outcomes require price data updates",
        }

    def analyze_by_signal_strength(self) -> List[Dict]:
        """Cohort analysis: do higher-confidence signals win more?"""

        taken = self.df[self.df["entry_status"] == "TAKEN"]
        exited = taken[taken["exit_date"].notna()]

        if len(exited) == 0:
            return []

        cohorts = []

        for decile in range(0, 101, 10):
            cohort = exited[
                (exited["signal_strength"] >= decile)
                & (exited["signal_strength"] < decile + 10)
            ]

            if len(cohort) == 0:
                continue

            wr = len(cohort[cohort["pnl"] > 0]) / len(cohort)

            cohorts.append(
                {
                    "strength_range": f"{decile}-{decile+10}",
                    "num_trades": len(cohort),
                    "win_rate": wr,
                    "avg_pnl": cohort["pnl"].mean(),
                    "avg_r_multiple": cohort["r_multiple"].mean(),
                }
            )

        return cohorts

    def analyze_by_exit_reason(self) -> Dict:
        """Which exit reasons are most profitable?"""

        exited = self.df[self.df["exit_date"].notna()]

        reasons = {}

        for reason in exited["exit_reason"].unique():
            cohort = exited[exited["exit_reason"] == reason]

            reasons[reason] = {
                "count": len(cohort),
                "win_rate": len(cohort[cohort["pnl"] > 0]) / len(cohort),
                "avg_pnl": cohort["pnl"].mean(),
                "avg_holding_days": cohort["holding_days"].mean(),
            }

        return reasons

    def analyze_by_ticker(self) -> Dict:
        """Per-ticker performance"""

        exited = self.df[self.df["exit_date"].notna()]

        results = {}

        for ticker in exited["ticker"].unique():
            cohort = exited[exited["ticker"] == ticker]

            results[ticker] = {
                "trades": len(cohort),
                "win_rate": len(cohort[cohort["pnl"] > 0]) / len(cohort),
                "total_pnl": cohort["pnl"].sum(),
                "avg_pnl": cohort["pnl"].mean(),
            }

        return results

    def compute_mae_mfe(self, trade_id: int, price_df: pd.DataFrame) -> Dict:
        """Maximum Adverse/Favorable Excursion during a closed trade's
        holding period.

        Uses intrabar Low/High against the actual entry price over
        `[actual_entry_date, exit_date]` - how far the trade moved against
        you (MAE) and in your favor (MFE) at any point, not just at exit.
        Assumes a long position, matching every engine in this codebase.

        Args:
            price_df: The trade's ticker's OHLCV frame, datetime-indexed.

        Raises:
            ValueError: if `trade_id` is unknown, the trade isn't `TAKEN`
                with both `actual_entry_date` and `exit_date` recorded, or
                `price_df` has no bars in the holding window.
        """
        mask = self.df["id"] == trade_id
        if not mask.any():
            raise ValueError(f"Unknown trade_id: {trade_id}")
        trade = self.df.loc[mask].iloc[0]

        if trade["entry_status"] != "TAKEN":
            raise ValueError(f"Trade {trade_id} is not TAKEN")
        if pd.isna(trade["actual_entry_date"]) or pd.isna(trade["exit_date"]):
            raise ValueError(
                f"Trade {trade_id} is missing actual_entry_date or exit_date"
            )

        entry_price = float(trade["actual_entry_price"])
        holding_start = trade["actual_entry_date"]
        holding_end = trade["exit_date"]
        window = price_df.loc[holding_start:holding_end]
        if window.empty:
            raise ValueError(f"No price bars for trade {trade_id}'s holding window")

        # Clamped at 0: a trade that never traded below/above entry has no
        # adverse/favorable excursion, not a negative one.
        mae_dollars = max(0.0, entry_price - float(window["Low"].min()))
        mfe_dollars = max(0.0, float(window["High"].max()) - entry_price)

        return {
            "trade_id": trade_id,
            "mae_dollars": mae_dollars,
            "mae_pct": mae_dollars / entry_price if entry_price else 0.0,
            "mfe_dollars": mfe_dollars,
            "mfe_pct": mfe_dollars / entry_price if entry_price else 0.0,
        }

    def list_trades(self) -> List[Dict]:
        """Every logged trade signal, in log order - the raw feed behind
        the Trade Journal UI's table. Unlike `analyze_all_trades`, this
        includes PENDING/SKIPPED rows and open (not-yet-exited) TAKEN
        trades, not just completed ones."""

        def _clean(value):
            # `datetime` also matches `pd.Timestamp` (a subclass) - covers
            # both a freshly-loaded CSV column and a same-session in-memory
            # assignment like `log_entry`'s raw `datetime.datetime`.
            if isinstance(value, datetime):
                return None if pd.isna(value) else value.isoformat()
            if isinstance(value, np.integer):
                return int(value)
            if isinstance(value, np.floating):
                return None if pd.isna(value) else float(value)
            if isinstance(value, str):
                return value
            return None if pd.isna(value) else value

        return [
            {col: _clean(row[col]) for col in self.df.columns}
            for _, row in self.df.iterrows()
        ]

    def compute_mae_mfe_all(self, price_loader: Callable[[str], pd.DataFrame]) -> Dict:
        """MAE/MFE for every completed (TAKEN + exited) trade, calling
        `price_loader` once per distinct ticker rather than once per trade.

        A per-ticker load failure or a per-trade `compute_mae_mfe` failure
        (e.g. no bars in the holding window) is collected into `warnings`
        and that trade is skipped, rather than failing the whole
        distribution - mirrors `alerts.dispatcher.AlertDispatcher.dispatch`
        isolating one channel's failure from the rest.

        Returns:
            `{"points": [...], "warnings": [...]}`.
        """
        taken = self.df[self.df["entry_status"] == "TAKEN"]
        exited = taken[taken["exit_date"].notna()]

        warnings: List[str] = []
        price_cache: Dict[str, pd.DataFrame] = {}
        for ticker in exited["ticker"].unique():
            try:
                price_cache[ticker] = price_loader(ticker)
            except Exception as exc:  # noqa: BLE001 - isolates one ticker's failure
                warnings.append(f"{ticker}: price data unavailable ({exc})")

        points: List[Dict] = []
        for _, trade in exited.iterrows():
            ticker = trade["ticker"]
            price_df = price_cache.get(ticker)
            if price_df is None:
                continue
            trade_id = int(trade["id"])
            try:
                mae_mfe = self.compute_mae_mfe(trade_id, price_df)
            except ValueError as exc:
                warnings.append(f"trade {trade_id}: {exc}")
                continue
            points.append(
                {
                    "trade_id": trade_id,
                    "ticker": ticker,
                    "mae_pct": mae_mfe["mae_pct"],
                    "mfe_pct": mae_mfe["mfe_pct"],
                    "pnl": (float(trade["pnl"]) if pd.notna(trade["pnl"]) else None),
                    "r_multiple": (
                        float(trade["r_multiple"])
                        if pd.notna(trade["r_multiple"])
                        else None
                    ),
                    "exit_reason": (
                        trade["exit_reason"] if pd.notna(trade["exit_reason"]) else None
                    ),
                }
            )

        return {"points": points, "warnings": warnings}

    def analyze_decay(
        self, windows: tuple = (30, 60, 90), as_of: datetime | None = None
    ) -> Dict:
        """Rolling win-rate/expectancy over trailing windows - catches a
        strategy's edge decaying before it shows up in the all-time numbers.

        Args:
            windows: Trailing day-counts to report on independently, e.g.
                trades exited in the last 30 days vs. the last 90.
            as_of: Reference "now" each window is measured back from.
                Defaults to `datetime.now()` - tests should pass this
                explicitly rather than depend on wall-clock time.

        Returns:
            `{window: {trade_count, win_rate, avg_r_multiple, expectancy}}`.
            A window with zero exited trades reports `None` for the rate
            fields rather than dividing by zero.
        """
        as_of = as_of or datetime.now()
        exited = self.df[self.df["exit_date"].notna()]

        result: Dict = {}
        for window in windows:
            cutoff = as_of - timedelta(days=window)
            cohort = exited[exited["exit_date"] >= cutoff]

            if len(cohort) == 0:
                result[window] = {
                    "trade_count": 0,
                    "win_rate": None,
                    "avg_r_multiple": None,
                    "expectancy": None,
                }
                continue

            winners = cohort[cohort["pnl"] > 0]
            avg_r_multiple = cohort["r_multiple"].mean()
            expectancy = cohort["pnl"].mean()

            result[window] = {
                "trade_count": len(cohort),
                "win_rate": len(winners) / len(cohort),
                "avg_r_multiple": (
                    float(avg_r_multiple) if pd.notna(avg_r_multiple) else None
                ),
                "expectancy": float(expectancy) if pd.notna(expectancy) else None,
            }

        return result

    @staticmethod
    def _max_consecutive_losses(df: pd.DataFrame) -> int:
        """Calculate longest loss streak"""

        is_loss = (df["pnl"] < 0).values

        if len(is_loss) == 0:
            return 0

        max_streak = 0
        current_streak = 0

        for loss in is_loss:
            if loss:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        return max_streak

    @staticmethod
    def _calculate_max_drawdown(df: pd.DataFrame) -> float:
        """Calculate max drawdown from cumulative PnL"""

        if len(df) == 0:
            return 0

        cumulative_pnl = df["pnl"].cumsum()
        running_max = cumulative_pnl.expanding().max()
        drawdown = cumulative_pnl - running_max
        max_dd = drawdown.min()

        return max_dd if max_dd != 0 else 0

    def export_summary(self, output_file: str = "results/trade_journal_summary.json"):
        """Export comprehensive analysis to JSON"""

        summary = {
            "as_of": datetime.now().isoformat(),
            "trades": self.analyze_all_trades(),
            "skipped": self.analyze_skipped_signals(),
            "by_strength": self.analyze_by_signal_strength(),
            "by_exit_reason": self.analyze_by_exit_reason(),
            "by_ticker": self.analyze_by_ticker(),
        }

        Path(output_file).parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w") as f:
            json.dump(summary, f, indent=2, default=str)

        return summary
