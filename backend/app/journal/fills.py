"""Forward-test journal: Alpaca paper fills in DuckDB, netted into trades.

`ingest_fills` stores every FILL activity once (`activity_id` is the key, so
an overlapping fetch adds nothing). `round_trips` nets each symbol's fills in
time order: a trade opens when the position leaves flat and closes when it
is flat again, so partial fills and bracket legs never double-count. Alpaca's
FILL activities carry no parent order or leg, so those columns may be empty.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

import numpy as np
import pandas as pd

from ..config import Settings
from ..db.session import get_connection
from .executor import TradeJournal

COLUMNS = [
    "activity_id",
    "order_id",
    "parent_order_id",
    "leg",
    "symbol",
    "side",
    "qty",
    "price",
    "filled_at",
    "ingested_at",
]
#: Re-fetch this far before the newest stored fill, in case Alpaca reports
#: a fill late; the primary key drops the overlap.
REFETCH_OVERLAP = timedelta(days=1)
#: Position size treated as flat (fractional-share float noise).
FLAT_EPSILON = 1e-9
SOURCE = "ALPACA_PAPER"


class FillSource(Protocol):
    def get_fill_activities(self, after: datetime | None = None) -> list[dict]: ...


def _utc_naive(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, format="ISO8601").dt.tz_convert(None)


def ingest_fills(
    settings: Settings, client: FillSource, now: datetime | None = None
) -> int:
    """Fetch fills from `client` and store the new ones; returns how many
    were added. A fetch error propagates before anything is written."""
    with get_connection() as conn:
        newest = conn.execute("SELECT max(filled_at) FROM broker_fills").fetchone()[0]
    after = None
    if newest is not None:
        after = newest.replace(tzinfo=timezone.utc) - REFETCH_OVERLAP
    activities = client.get_fill_activities(after)
    if not activities:
        return 0
    raw = pd.DataFrame(activities)
    incoming = pd.DataFrame(
        {
            "activity_id": raw["id"].astype(str),
            "order_id": raw.get("order_id"),
            "parent_order_id": raw.get("parent_order_id"),
            "leg": raw.get("leg"),
            "symbol": raw["symbol"].str.upper(),
            "side": raw["side"].str.lower(),
            "qty": raw["qty"].astype(float),
            "price": raw["price"].astype(float),
            "filled_at": _utc_naive(raw["transaction_time"]),
            "ingested_at": pd.Timestamp(now or datetime.now(timezone.utc))
            .tz_convert("UTC")
            .tz_localize(None),
        }
    ).drop_duplicates("activity_id")
    with get_connection() as conn:
        before = conn.execute("SELECT count(*) FROM broker_fills").fetchone()[0]
        conn.register("incoming_fills", incoming)
        try:
            conn.execute(
                f"INSERT INTO broker_fills ({', '.join(COLUMNS)}) "
                f"SELECT {', '.join(COLUMNS)} FROM incoming_fills "
                "WHERE activity_id NOT IN (SELECT activity_id FROM broker_fills)"
            )
        finally:
            conn.unregister("incoming_fills")
        after_count = conn.execute("SELECT count(*) FROM broker_fills").fetchone()[0]
    return after_count - before


def load_fills() -> pd.DataFrame:
    """Every stored fill, `filled_at` as UTC timestamps."""
    with get_connection() as conn:
        df = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM broker_fills").df()
    df["filled_at"] = pd.to_datetime(df["filled_at"]).dt.tz_localize("UTC")
    return df


def _empty_trades() -> pd.DataFrame:
    return TradeJournal.empty_frame().assign(qty=pd.Series(dtype="float64"))


def _positions(fills: pd.DataFrame) -> pd.DataFrame:
    """Fills in time order with the running position and a per-symbol trade
    number (a new trade starts whenever the position was flat)."""
    df = fills.sort_values(["filled_at", "activity_id"]).copy()
    df["signed"] = np.where(df["side"] == "buy", df["qty"], -df["qty"])
    df["position"] = df.groupby("symbol")["signed"].cumsum()
    before = df.groupby("symbol")["position"].shift(fill_value=0.0)
    df["trade_no"] = (before.abs() < FLAT_EPSILON).groupby(df["symbol"]).cumsum()
    return df


def open_positions(fills: pd.DataFrame) -> int:
    """How many symbols are not flat after their last fill."""
    if fills.empty:
        return 0
    last = _positions(fills).groupby("symbol")["position"].last()
    return int((last.abs() >= FLAT_EPSILON).sum())


def round_trips(fills: pd.DataFrame) -> pd.DataFrame:
    """Closed trades (flat to flat) in `TradeJournal` columns plus `qty`.
    Entry/exit prices are volume-weighted; `pnl` is gross USD, before costs."""
    if fills.empty:
        return _empty_trades()
    df = _positions(fills)
    df["buy_qty"] = df["qty"].where(df["side"] == "buy", 0.0)
    df["sell_qty"] = df["qty"] - df["buy_qty"]
    df["buy_usd"] = df["buy_qty"] * df["price"]
    df["sell_usd"] = df["sell_qty"] * df["price"]
    g = df.groupby(["symbol", "trade_no"], sort=False).agg(
        entry_date=("filled_at", "first"),
        exit_date=("filled_at", "last"),
        first_signed=("signed", "first"),
        end_position=("position", "last"),
        buy_qty=("buy_qty", "sum"),
        sell_qty=("sell_qty", "sum"),
        buy_usd=("buy_usd", "sum"),
        sell_usd=("sell_usd", "sum"),
    )
    g = g[g["end_position"].abs() < FLAT_EPSILON].reset_index()
    if g.empty:
        return _empty_trades()
    long = g["first_signed"] > 0
    qty = np.where(long, g["buy_qty"], g["sell_qty"])
    buy_px, sell_px = g["buy_usd"] / g["buy_qty"], g["sell_usd"] / g["sell_qty"]
    entry_price = np.where(long, buy_px, sell_px)
    pnl = g["sell_usd"] - g["buy_usd"]
    trades = _empty_trades().reindex(range(len(g)))
    trades["id"] = range(1, len(g) + 1)
    trades["ticker"] = g["symbol"]
    trades["entry_date"] = trades["actual_entry_date"] = g["entry_date"]
    trades["entry_price"] = trades["actual_entry_price"] = entry_price
    trades["entry_status"] = "TAKEN"
    trades["exit_date"] = trades["created_at"] = g["exit_date"]
    trades["exit_price"] = np.where(long, sell_px, buy_px)
    trades["holding_days"] = (g["exit_date"] - g["entry_date"]).dt.days
    trades["pnl"] = pnl
    trades["pnl_pct"] = pnl / (entry_price * qty)
    trades["notes"] = ""
    trades["source"] = SOURCE
    trades["qty"] = qty
    return trades
