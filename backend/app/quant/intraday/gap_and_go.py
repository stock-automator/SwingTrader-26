"""Gap-and-Go day-trade rules on 5-minute bars.

Long trades a gap up, short a gap down, mirrored:

- Candidate day (daily cache): |open / previous close - 1| >= `gap_min`,
  open >= `min_price`, mean volume of the 20 prior sessions >= `min_adv`.
- Opening range: the 09:30 bar. It must trade >= `vol_mult` x (ADV / 78).
- Entry: the first later bar starting before 11:00 whose high (low) breaks
  the range high (low), filled at that level, or at the bar's open if it
  opens beyond it, plus slippage.
- Stop: the other side of the range; skipped if the planned risk is under
  `min_risk_pct` of the price. Target: `target_r` x the filled risk.
- A bar touching both stop and target counts as the stop. Otherwise exit at
  the close of the 15:55 bar. Every fill pays `slippage` per side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any

import numpy as np
import pandas as pd

NY = "America/New_York"
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


@dataclass(frozen=True)
class GapParams:
    gap_min: float = 0.02
    min_price: float = 5.0
    min_adv: float = 1_000_000.0
    vol_mult: float = 2.0
    slippage: float = 0.0005
    min_risk_pct: float = 0.002
    target_r: float = 2.0
    last_entry: time = time(11, 0)
    eod_bar: time = time(15, 55)
    bars_per_day: int = 78
    #: Skip a day whose first 5-minute open is this far from the daily open
    #: (spliced or mis-adjusted bars, see learnings 2026-11-23).
    max_open_mismatch: float = 0.03


def find_gap_candidates(
    daily: pd.DataFrame, ticker: str, params: GapParams
) -> pd.DataFrame:
    """Gap days of one ticker's daily bars. ADV uses only prior sessions."""
    prev_close = daily["Close"].shift(1)
    gap = daily["Open"] / prev_close - 1
    adv20 = daily["Volume"].shift(1).rolling(20).mean()
    mask = (
        (gap.abs() >= params.gap_min - 1e-12)
        & (daily["Open"] >= params.min_price)
        & (adv20 >= params.min_adv)
    )
    out = pd.DataFrame(
        {
            "date": daily.index,
            "ticker": ticker,
            "direction": np.where(gap > 0, "long", "short"),
            "gap": gap.to_numpy(),
            "prev_close": prev_close.to_numpy(),
            "open": daily["Open"].to_numpy(),
            "adv20": adv20.to_numpy(),
        }
    )
    return out[mask.to_numpy()].reset_index(drop=True)


def regular_session(bars: pd.DataFrame) -> pd.DataFrame:
    """Bars starting 09:30 to before 16:00 New York; naive stamps are UTC."""
    idx = bars.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    out = bars.set_axis(idx.tz_convert(NY))
    t = out.index.time
    return out[(t >= SESSION_OPEN) & (t < SESSION_CLOSE)].sort_index()


def simulate_setup(
    day: pd.DataFrame,
    direction: str,
    adv20: float,
    gap: float,
    params: GapParams,
    daily_open: float | None = None,
) -> dict[str, Any]:
    """Run one ticker-day. `status` is one of no_first_bar, data_mismatch,
    low_volume, small_risk (rejected at 09:35), no_trigger or traded."""
    res: dict[str, Any] = {"status": "", "rank_score": np.nan}
    if day.empty or day.index[0].time() != SESSION_OPEN:
        return {**res, "status": "no_first_bar"}
    o = day["Open"].to_numpy(float)
    h = day["High"].to_numpy(float)
    lo = day["Low"].to_numpy(float)
    c = day["Close"].to_numpy(float)
    if daily_open and abs(o[0] / daily_open - 1) > params.max_open_mismatch:
        return {**res, "status": "data_mismatch"}

    rel_vol = float(day["Volume"].iloc[0]) / (adv20 / params.bars_per_day)
    res["rank_score"] = abs(gap) * rel_vol
    if rel_vol < params.vol_mult - 1e-12:
        return {**res, "status": "low_volume"}

    long = direction == "long"
    sign = 1.0 if long else -1.0
    slip = params.slippage
    level, stop = (h[0], lo[0]) if long else (lo[0], h[0])
    res["stop"] = stop
    planned = level * (1 + sign * slip)
    if sign * (planned - stop) / planned < params.min_risk_pct:
        return {**res, "status": "small_risk"}

    times = np.array([ts.time() for ts in day.index])
    window = (np.arange(len(day)) > 0) & (times < params.last_entry)
    broke = window & ((h > level) if long else (lo < level))
    if not broke.any():
        return {**res, "status": "no_trigger"}
    i = int(np.argmax(broke))
    raw = max(o[i], level) if long else min(o[i], level)
    entry = raw * (1 + sign * slip)
    risk = sign * (entry - stop)
    target = entry + sign * params.target_r * risk

    last = np.flatnonzero(times <= params.eod_bar)[-1]
    seg = slice(i, last + 1)
    stop_hit = (lo[seg] <= stop) if long else (h[seg] >= stop)
    tgt_hit = (h[seg] >= target) if long else (lo[seg] <= target)
    hits = np.flatnonzero(stop_hit | tgt_hit)
    if hits.size:
        k = i + int(hits[0])
        # On the entry bar the open came before the fill, so no open gaps.
        bar_open = o[k] if k > i else None
        if stop_hit[hits[0]]:
            reason, px = "stop", stop
            if bar_open is not None and sign * (bar_open - stop) < 0:
                px = bar_open
        else:
            reason, px = "target", target
            if bar_open is not None and sign * (bar_open - target) > 0:
                px = bar_open
    else:
        k, reason, px = last, "eod", c[last]
    exit_px = px * (1 - sign * slip)
    return {
        **res,
        "status": "traded",
        "entry_time": day.index[i],
        "entry": entry,
        "target": target,
        "exit_time": day.index[k],
        "exit": exit_px,
        "exit_reason": reason,
        "r_multiple": sign * (exit_px - entry) / risk,
    }
