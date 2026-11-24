"""
Portfolio backtest: one strategy across many tickers sharing one account.

Unlike `backtest.run_comparison`, whose tickers are independent sleeves,
every position here draws on the same cash and the same limits (cycle 03,
Gate 1 defaults in `PortfolioConfig`):

- entries fill at the next open; stop/target are priced off the signal
  bar's close, exactly as `RiskManager.build_order` prices a live ticket;
- size = `risk_per_trade_pct` of equity (prior close) over the stop
  distance, then capped at `max_position_pct` of equity and by free cash;
- at most `max_positions` open, total initial risk <= `max_heat_pct`;
- one position per ticker; with more signals than slots, the highest
  reward:risk is taken first (ties by ticker);
- stop and target are checked on each bar's high/low, stop first when
  both are touched; a gap through a level fills at the open;
- an opposite signal exits at the next open; `time_stop_days` trading days
  after the fill the position exits at that close, or at the ticker's next
  open if it has no bar that day;
- shorts (`allow_shorts`) reserve their notional as collateral and nothing
  is leveraged; borrow cost is not modelled. A short that gaps past twice
  its entry can still lose more than its collateral: if equity or cash
  ever closes below zero the run is flagged and opens nothing new.
  With `shorts_require_non_bull` a short is taken only if the SPY macro
  regime at the signal bar's close (the fill day's t-1) is known and not
  `BULL_TRENDING`.
- optional long entry filters (off by default): `long_blocked_regimes`
  skips a long when the SPY macro regime at the signal bar's close is in
  the set (as for shorts); `long_trend_sma` skips a long unless the signal
  bar's close is above its own N-day SMA, computed over full history.

Signals are generated once per ticker over its full history (vectorised;
the strategies are causal), so the daily loop only does fills and exits,
and a window starting in 2023 still has its indicators warmed up. A ticker
with a spliced bar (`suspect_spike`, see learnings 2026-11-23) is excluded
and reported as "suspect data: TICKER".
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .regime import MACRO_BULL_TRENDING, MacroRegimeDetector
from .risk import RiskManager
from .strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

#: `skipped` keys.
MAX_POSITIONS = "max positions"
HEAT_CAP = "heat cap"
INSUFFICIENT_CASH = "insufficient cash"
CAPPED_TO_CASH = "size capped to cash"
SIZE_ZERO = "size rounds to zero"
SHORT_BLOCKED = "short blocked by bull regime"
LONG_REGIME_BLOCKED = "long blocked by regime"
LONG_BELOW_TREND = "long below trend SMA"
GAP_THROUGH_LEVEL = "gap through stop/target"
NO_BAR = "no bar on fill day"
INVALID_LEVELS = "invalid stop/target"
SIGNAL_ERROR = "signal errors (tickers)"
EQUITY_BELOW_ZERO = "equity below zero"
CASH_BELOW_ZERO = "cash below zero"

#: `suspect_spike` thresholds.
SPIKE_MOVE = 0.40
SPIKE_BARS = 5
SPIKE_RETURN = 0.05

_EPS = 1e-9


@dataclass(frozen=True)
class PortfolioConfig:
    """Cycle 03 Gate 1 defaults. Costs default to the `/backtest` request's
    (commission and spread per leg); `fx_cost_pct=None` takes the per-leg FX
    cost from Settings, as `scripts/run_baseline.py` does."""

    initial_capital: float = 10_000.0
    risk_per_trade_pct: float = 0.01
    max_position_pct: float = 0.25
    max_positions: int = 6
    max_heat_pct: float = 0.06
    time_stop_days: int = 40
    allow_shorts: bool = False
    shorts_require_non_bull: bool = True
    exit_on_opposite_signal: bool = True
    #: Long entry filters, off by default (cycle 03 step 3 experiments).
    long_blocked_regimes: frozenset[str] = frozenset()
    long_trend_sma: int | None = None
    commission: float = 0.001
    slippage_pct: float = 0.0005
    fee_per_share: float = 0.0
    fx_cost_pct: float | None = None

    def resolved_fx_cost_pct(self) -> float:
        if self.fx_cost_pct is not None:
            return self.fx_cost_pct
        from ..config import get_settings

        settings = get_settings()
        return settings.fx_cost_pct * settings.fx_conversions_per_round_trip / 2


@dataclass
class SignalMatrix:
    """Every ticker's bars and entry intent on one calendar (rows) x
    tickers (columns). Build once per strategy, reuse across windows."""

    index: pd.DatetimeIndex
    tickers: list[str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    mark: np.ndarray  # close forward-filled, for valuation only
    signal: np.ndarray
    sl_off: np.ndarray
    tp_off: np.ndarray
    errors: dict[str, str] = field(default_factory=dict)
    suspect: list[str] = field(default_factory=list)


@dataclass
class PortfolioResult:
    equity: pd.Series
    cash: pd.Series
    #: Gross |notional| / equity at each close, 0-1.
    invested: pd.Series
    trades: pd.DataFrame
    exposure_pct: float
    avg_invested_pct: float
    skipped: dict[str, int]
    strategy_name: str
    #: Data and solvency warnings: "suspect data: T", "equity below zero".
    flags: list[str] = field(default_factory=list)


def _offsets(kind: np.ndarray, value: np.ndarray, close, atr) -> np.ndarray:
    """Vectorised `RiskManager._resolve_offset`."""
    return np.where(
        kind == "PERCENTAGE",
        close * value,
        np.where(kind == "FIXED", value, value * atr),
    )


def suspect_spike(close: pd.Series) -> bool:
    """True if the closes show a spliced bar: a one-day move beyond
    `SPIKE_MOVE` that, within `SPIKE_BARS` bars, snaps back in a single bar
    from beyond `SPIKE_MOVE` to within `SPIKE_RETURN` of the pre-jump close
    (HCA 425 -> 16.77 -> 429).

    Real events retrace gradually or only partly: HOOD's +50% day gave back
    to +8.9%, BIIB fell back to -4.4% over three bars, so neither is flagged.
    A plain 10% return band would flag both, and CVNA (+9.9%)."""
    c = close.astype(float)
    prev = c.shift(1)
    jump = (c / prev - 1).abs() > SPIKE_MOVE
    snap_back = pd.Series(False, index=c.index)
    for d in range(1, SPIKE_BARS + 1):
        back = (c.shift(-d) / prev - 1).abs() <= SPIKE_RETURN
        still_out = (c.shift(1 - d) / prev - 1).abs() > SPIKE_MOVE
        snap_back |= back & still_out
    return bool((jump & snap_back).any())


def prepare_signals(
    strategy: BaseStrategy, frames: dict[str, pd.DataFrame], calendar: pd.Index
) -> SignalMatrix:
    """Run `strategy` on each full frame and lay the result on `calendar`.

    A ticker whose signal generation raises is dropped and logged, never
    silently: its error is kept in `SignalMatrix.errors`. A ticker failing
    `suspect_spike` is dropped and listed in `SignalMatrix.suspect`."""
    cols: dict[str, list[pd.Series]] = {
        k: [] for k in ("open", "high", "low", "close", "signal", "sl", "tp")
    }
    tickers: list[str] = []
    errors: dict[str, str] = {}
    suspect: list[str] = []
    for ticker in sorted(frames):
        df = frames[ticker]
        if suspect_spike(df["Close"]):
            suspect.append(ticker)
            logger.warning("%s: suspect data (reverting spike), excluded", ticker)
            continue
        try:
            out = strategy.generate_signals(df)
            BaseStrategy.validate_output(out)
        except Exception as exc:  # one bad ticker must not sink the run
            errors[ticker] = f"{type(exc).__name__}: {exc}"
            logger.warning("%s: %s skipped: %s", strategy.name, ticker, exc)
            continue
        close = df["Close"].to_numpy(dtype=float)
        atr = (
            out["atr"].to_numpy(dtype=float)
            if "atr" in out.columns
            else np.full(len(df), np.nan)
        )
        sig = out["signal"].to_numpy(dtype=int)
        with np.errstate(invalid="ignore"):
            sl = _offsets(
                out["sl_type"].to_numpy(dtype=object),
                out["sl_value"].to_numpy(dtype=float),
                close,
                atr,
            )
            tp = _offsets(
                out["tp_type"].to_numpy(dtype=object),
                out["tp_value"].to_numpy(dtype=float),
                close,
                atr,
            )
        tickers.append(ticker)
        for key, values in (
            ("open", df["Open"]),
            ("high", df["High"]),
            ("low", df["Low"]),
            ("close", df["Close"]),
            ("signal", pd.Series(sig, index=df.index)),
            ("sl", pd.Series(sl, index=df.index)),
            ("tp", pd.Series(tp, index=df.index)),
        ):
            cols[key].append(values.rename(ticker))

    def matrix(key: str, fill=np.nan) -> np.ndarray:
        if not tickers:
            return np.full((len(calendar), 0), fill, dtype=float)
        joined = pd.concat(cols[key], axis=1)
        joined = joined[~joined.index.duplicated(keep="last")]
        return joined.reindex(calendar).fillna(fill).to_numpy(dtype=float)

    close = matrix("close")
    return SignalMatrix(
        index=pd.DatetimeIndex(calendar),
        tickers=tickers,
        open=matrix("open"),
        high=matrix("high"),
        low=matrix("low"),
        close=close,
        mark=pd.DataFrame(close).ffill().to_numpy(),
        signal=matrix("signal", 0).astype(int),
        sl_off=matrix("sl"),
        tp_off=matrix("tp"),
        errors=errors,
        suspect=suspect,
    )


def macro_regime(spy: pd.DataFrame) -> pd.Series:
    """SPY macro regime per bar (`MacroRegimeDetector`), None in warm-up."""
    return MacroRegimeDetector().detect_regime(spy)


@dataclass
class _Position:
    col: int
    side: int
    shares: int
    entry_price: float
    entry_k: int
    stop: float
    target: float
    entry_fees: float
    time_due: bool = False

    @property
    def risk(self) -> float:
        return self.shares * abs(self.entry_price - self.stop)


def run_portfolio_backtest(
    strategy: BaseStrategy,
    frames: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    config: PortfolioConfig | None = None,
    start=None,
    end=None,
    regime: pd.Series | None = None,
    prepared: SignalMatrix | None = None,
) -> PortfolioResult:
    """Simulate `strategy` over `frames` as one account on SPY's calendar
    within [start, end]. See the module docstring for the rules.

    Args:
        regime: Macro regime per date; defaults to `macro_regime(spy)`.
        prepared: Precomputed `prepare_signals` output on SPY's calendar,
            to reuse one strategy's signals across windows and variants.
    """
    config = config or PortfolioConfig()
    if prepared is None:
        prepared = prepare_signals(strategy, frames, spy.index)
    rows = np.ones(len(prepared.index), dtype=bool)
    if start is not None:
        rows &= prepared.index >= pd.Timestamp(start)
    if end is not None:
        rows &= prepared.index <= pd.Timestamp(end)
    pos = np.flatnonzero(rows)
    if len(pos) == 0:
        raise ValueError("no SPY bars inside the requested window")
    lo, hi = pos[0], pos[-1] + 1
    index = prepared.index[lo:hi]
    O, H, L = prepared.open[lo:hi], prepared.high[lo:hi], prepared.low[lo:hi]
    C, M = prepared.close[lo:hi], prepared.mark[lo:hi]
    S, SL, TP = prepared.signal[lo:hi], prepared.sl_off[lo:hi], prepared.tp_off[lo:hi]
    tickers = prepared.tickers

    if regime is None:
        regime = macro_regime(spy)
    regime = regime[~regime.index.duplicated(keep="last")].reindex(index)
    short_ok = (regime.notna() & (regime != MACRO_BULL_TRENDING)).to_numpy()
    if not config.shorts_require_non_bull:
        short_ok[:] = True
    long_ok = ~regime.isin(config.long_blocked_regimes).to_numpy()
    if config.long_trend_sma:
        n = config.long_trend_sma
        sma = pd.DataFrame(prepared.mark).rolling(n, min_periods=n).mean()
        above = (prepared.mark > sma.to_numpy())[lo:hi]
    else:
        above = np.ones_like(S, dtype=bool)

    slip = config.slippage_pct
    fee = config.fee_per_share
    cost_rate = config.resolved_fx_cost_pct() + (0.0 if fee > 0 else config.commission)
    cap_label = f"size capped to {config.max_position_pct:.0%} of equity"

    def leg_fees(shares: int, price: float) -> float:
        return shares * price * cost_rate + shares * fee

    T, N = C.shape
    cash = float(config.initial_capital)
    equity_prev = cash
    held = np.zeros(N, dtype=bool)
    positions: dict[int, _Position] = {}
    trades: list[dict] = []
    skipped: dict[str, int] = {}
    flags = [f"suspect data: {t}" for t in prepared.suspect]
    halted = False
    if prepared.errors:
        skipped[SIGNAL_ERROR] = len(prepared.errors)
    equity = np.empty(T)
    cash_curve = np.empty(T)
    invested = np.empty(T)

    def bump(reason: str, n: int = 1) -> None:
        if n:
            skipped[reason] = skipped.get(reason, 0) + n

    def close_position(p: _Position, k: int, raw_price: float, reason: str) -> None:
        nonlocal cash
        fill = raw_price * (1 - p.side * slip)
        fees = leg_fees(p.shares, fill)
        if p.side == 1:
            cash += p.shares * fill - fees
        else:
            cash += p.shares * (2 * p.entry_price - fill) - fees
        pnl = p.side * p.shares * (fill - p.entry_price) - p.entry_fees - fees
        trades.append(
            {
                "ticker": tickers[p.col],
                "side": "long" if p.side == 1 else "short",
                "strategy": strategy.name,
                "entry_date": index[p.entry_k],
                "entry_price": p.entry_price,
                "exit_date": index[k],
                "exit_price": fill,
                "shares": p.shares,
                "stop": p.stop,
                "target": p.target,
                "risk": p.risk,
                "pnl": pnl,
                "r_multiple": pnl / p.risk if p.risk > 0 else np.nan,
                "holding_days": k - p.entry_k,
                "exit_reason": reason,
            }
        )
        held[p.col] = False
        del positions[p.col]

    for k in range(T):
        if k > 0:
            # 1. Overdue time stops, then opposite-signal exits, at the open.
            for p in list(positions.values()):
                o = O[k, p.col]
                if math.isnan(o):
                    continue
                if p.time_due:
                    close_position(p, k, o, "time")
                elif config.exit_on_opposite_signal and S[k - 1, p.col] == -p.side:
                    close_position(p, k, o, "signal")

            # 2. Entries from yesterday's signals, best reward:risk first.
            sig = S[k - 1]
            want = (sig == 1) | ((sig == -1) & config.allow_shorts)
            cand = np.flatnonzero(want & ~held)
            if halted:
                bump(EQUITY_BELOW_ZERO, len(cand))
                cand = cand[:0]
            if len(cand):
                if not short_ok[k - 1]:
                    blocked = sig[cand] == -1
                    bump(SHORT_BLOCKED, int(blocked.sum()))
                    cand = cand[~blocked]
                longs = sig[cand] == 1
                if not long_ok[k - 1]:
                    bump(LONG_REGIME_BLOCKED, int(longs.sum()))
                    cand, longs = cand[~longs], longs[~longs]
                below = longs & ~above[k - 1, cand]
                bump(LONG_BELOW_TREND, int(below.sum()))
                cand = cand[~below]
                sl_off, tp_off = SL[k - 1, cand], TP[k - 1, cand]
                valid = (sl_off > 0) & np.isfinite(sl_off) & np.isfinite(tp_off)
                valid &= tp_off > 0
                bump(INVALID_LEVELS, int((~valid).sum()))
                cand = cand[valid]
                rr = TP[k - 1, cand] / SL[k - 1, cand]
                order = np.lexsort((cand, -rr))
                cand = cand[order]
            for n, j in enumerate(cand):
                slots = config.max_positions - len(positions)
                if slots <= 0:
                    bump(MAX_POSITIONS, len(cand) - n)
                    break
                side = int(sig[j])
                ref = M[k - 1, j]
                stop = ref - side * SL[k - 1, j]
                target = ref + side * TP[k - 1, j]
                o = O[k, j]
                if math.isnan(o):
                    bump(NO_BAR)
                    continue
                if side * (o - stop) <= 0 or side * (target - o) <= 0:
                    bump(GAP_THROUGH_LEVEL)
                    continue
                # Heat in dollars: fractions like 0.06 - 0.05 lose a cent.
                open_risk = sum(p.risk for p in positions.values())
                heat_left = config.max_heat_pct * equity_prev - open_risk
                if heat_left <= _EPS:
                    bump(HEAT_CAP)
                    continue
                fill = o * (1 + side * slip)
                rm = RiskManager(equity_prev, config.risk_per_trade_pct)
                shares = rm.position_size(fill, stop)
                shares = min(shares, int(heat_left // abs(fill - stop)))
                if shares <= 0:
                    bump(SIZE_ZERO)
                    continue
                cap = math.floor(config.max_position_pct * equity_prev / fill)
                if shares > cap:
                    shares = cap
                    bump(cap_label)
                affordable = math.floor(max(cash, 0.0) / (fill * (1 + cost_rate) + fee))
                if shares > affordable:
                    if affordable <= 0:
                        bump(INSUFFICIENT_CASH)
                        continue
                    shares = affordable
                    bump(CAPPED_TO_CASH)
                if shares <= 0:
                    bump(SIZE_ZERO)
                    continue
                fees = leg_fees(shares, fill)
                cash -= shares * fill + fees
                positions[j] = _Position(j, side, shares, fill, k, stop, target, fees)
                held[j] = True

        # 3. Stops and targets on today's range, stop first.
        for p in list(positions.values()):
            h, low, o = H[k, p.col], L[k, p.col], O[k, p.col]
            if math.isnan(h) or math.isnan(low):
                continue
            o = p.entry_price if math.isnan(o) else o
            if p.side == 1:
                if low <= p.stop:
                    close_position(p, k, min(o, p.stop), "stop")
                elif h >= p.target:
                    close_position(p, k, max(o, p.target), "target")
            else:
                if h >= p.stop:
                    close_position(p, k, max(o, p.stop), "stop")
                elif low <= p.target:
                    close_position(p, k, min(o, p.target), "target")

        # 4. Time stop at the close; at the window's end, close everything.
        for p in list(positions.values()):
            c = C[k, p.col]
            if k == T - 1:
                close_position(p, k, M[k, p.col], "end")
            elif k - p.entry_k >= config.time_stop_days:
                if math.isnan(c):
                    p.time_due = True  # no bar today: exit at its next open
                else:
                    close_position(p, k, c, "time")

        # 5. Mark to market.
        gross = 0.0
        value = cash
        for p in positions.values():
            px = M[k, p.col]
            gross += p.shares * px
            value += p.shares * (px if p.side == 1 else 2 * p.entry_price - px)
        equity[k] = value
        cash_curve[k] = cash
        invested[k] = gross / value if value > 0 else 0.0
        equity_prev = value
        for bad, label in ((value, EQUITY_BELOW_ZERO), (cash, CASH_BELOW_ZERO)):
            if bad < -_EPS and label not in flags:
                flags.append(label)
                logger.warning("%s: %s on %s", strategy.name, label, index[k].date())
        halted = halted or value <= 0 or cash < -_EPS

    trades_df = pd.DataFrame(
        trades,
        columns=[
            "ticker",
            "side",
            "strategy",
            "entry_date",
            "entry_price",
            "exit_date",
            "exit_price",
            "shares",
            "stop",
            "target",
            "risk",
            "pnl",
            "r_multiple",
            "holding_days",
            "exit_reason",
        ],
    )
    if not trades_df.empty:
        trades_df = trades_df.sort_values(["entry_date", "ticker"], ignore_index=True)
    return PortfolioResult(
        equity=pd.Series(equity, index=index, name="equity"),
        cash=pd.Series(cash_curve, index=index, name="cash"),
        invested=pd.Series(invested, index=index, name="invested"),
        trades=trades_df,
        exposure_pct=float((invested > 0).mean() * 100),
        avg_invested_pct=float(invested.mean() * 100),
        skipped=skipped,
        strategy_name=strategy.name,
        flags=flags,
    )
