"""Parquet data-lifecycle management: staleness detection, incremental
sync, and corporate-action reconciliation. See `parquet_manager.py`.

Distinct from `backend.app.data` (the read-path: yfinance/Finnhub fetches
and the on-disk cache reader consumed by every backtest/screener request)
- this package owns the write-path that keeps that cache current.
"""
