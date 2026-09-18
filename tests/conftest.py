"""
Shared pytest configuration.

Puts the repository root on `sys.path` once, so test modules can import
`backend.app.*` directly. Previously every test file prepended the root
itself, which forced its imports below a statement and required a blanket
E402 exemption for `tests/` in `.flake8`.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    """120 bars of synthetic daily OHLCV on a steady uptrend.

    Long enough to clear the 63-bar Donchian momentum warm-up and the 50-bar
    SMA warm-up, so strategies actually emit signals over it.
    """
    index = pd.date_range("2023-01-02", periods=120, freq="B")
    close = pd.Series([100.0 + i * 0.5 for i in range(120)], index=index)

    return pd.DataFrame(
        {
            "Open": close - 0.25,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": pd.Series([1_000_000] * 120, index=index),
        },
        index=index,
    )
