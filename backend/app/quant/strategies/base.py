"""
Base Strategy Interface - All trading strategies inherit from this.

Strategies are pure functions of price history: given an OHLCV DataFrame they
return a DataFrame of the same length/index describing what to do on each bar.
Position sizing and converting relative SL/TP definitions into absolute prices
is the responsibility of `backend.app.quant.risk.RiskManager`, not the strategy.

See AGENTS.md for the full schema contract and instructions on adding a new
strategy.
"""

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

#: Valid values for the `signal` column.
VALID_SIGNALS = {1, -1, 0}

#: Valid values for the `sl_type` / `tp_type` columns.
VALID_LEVEL_TYPES = {"PERCENTAGE", "FIXED", "ATR"}

#: Columns every `generate_signals()` output must contain.
REQUIRED_COLUMNS = ("signal", "sl_type", "sl_value", "tp_type", "tp_value")


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies.

    Subclasses implement `generate_signals`, which must return a DataFrame
    indexed identically to the input `df` with the columns described in
    `REQUIRED_COLUMNS`. Use `validate_output` to assert the contract is met
    (existing strategy test suites call this on their own output).
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate trade signals and risk parameters from OHLCV data.

        Args:
            df: OHLCV DataFrame with columns Open, High, Low, Close, Volume
                and a datetime index (see AGENTS.md "Data ingestion format").

        Returns:
            DataFrame indexed like `df`, with columns:
                signal: int, one of {1 (BUY), -1 (SELL), 0 (HOLD)}
                sl_type: str, one of {'PERCENTAGE', 'FIXED', 'ATR'}
                sl_value: float, e.g. 0.02 for 2%, or 1.5 for 1.5x ATR
                tp_type: str, one of {'PERCENTAGE', 'FIXED', 'ATR'}
                tp_value: float
            On HOLD rows (signal == 0), sl_value/tp_value should be NaN since
            there is no trade to size.
        """
        raise NotImplementedError

    @staticmethod
    def validate_output(df: pd.DataFrame) -> None:
        """Assert that a `generate_signals` output satisfies the schema.

        Raises:
            ValueError: if any required column is missing or malformed.
        """
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required signal columns: {missing}")

        bad_signals = set(df["signal"].unique()) - VALID_SIGNALS
        if bad_signals:
            raise ValueError(f"Invalid signal values: {bad_signals}")

        active = df[df["signal"] != 0]

        bad_sl_types = set(active["sl_type"].unique()) - VALID_LEVEL_TYPES
        if bad_sl_types:
            raise ValueError(f"Invalid sl_type values: {bad_sl_types}")

        bad_tp_types = set(active["tp_type"].unique()) - VALID_LEVEL_TYPES
        if bad_tp_types:
            raise ValueError(f"Invalid tp_type values: {bad_tp_types}")

        for col in ("sl_value", "tp_value"):
            values = active[col].to_numpy(dtype=float)
            if len(values) and (not np.all(np.isfinite(values)) or np.any(values <= 0)):
                raise ValueError(
                    f"{col} must be finite and positive on active signal rows"
                )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
