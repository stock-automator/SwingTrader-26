"""
Backtest wrapper around the `backtesting` library.

Adapts a `BaseStrategy`'s precomputed signal DataFrame into a
`backtesting.Strategy`, executing trades bar-by-bar with the position sizing
and SL/TP resolution supplied by a `RiskManager`. This engine is long-only:
`signal == 1` opens a long if flat, `signal == -1` closes an open long. It
does not open short positions - see `engine/forward_tester.py` /
AGENTS.md for how to extend this.

Also carries `build_order_ticket`, which turns a sized `Order` into the
structured, broker-shaped ticket the frontend's Order Ticket Drawer copies
out for manual entry at Trading 212 / IBKR.
"""

import math
from dataclasses import dataclass

import pandas as pd
from backtesting import Backtest, Strategy

from backend.app.quant.indicators import wilder_atr
from backend.app.quant.regime import MacroRegimeDetector
from backend.app.quant.risk import RiskManager
from backend.app.quant.strategies.base import BaseStrategy

REQUIRED_OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")

#: Trading 212 charges no per-share commission on its default long-term
#: investing accounts, but IBKR's US stock tier does, and modelling *some*
#: per-share friction is the point - $0.005/share (IBKR's own published
#: tiered-pricing rate) is the default fixed maker/taker fee applied to both
#: legs of a round trip when `fee_per_share` is set.
DEFAULT_FEE_PER_SHARE = 0.005

#: A signal on bar `t` fills at bar `t+1`'s open - the realistic default:
#: nothing here trades on information that bar's own close hasn't fully
#: revealed the consequences of yet (the next bar's open is the first
#: price the market offers *after* that close is known).
EXECUTION_MODE_NEXT_OPEN = "NEXT_OPEN"

#: A signal on bar `t` fills at bar `t`'s own close - a faster-executing
#: approximation (e.g. an intraday alert acted on before the close prints)
#: that trades slightly ahead of `NEXT_OPEN`'s realism, hence the paired
#: name: pair this with non-zero `slippage_pct`/`atr_slippage_multiple` to
#: compensate for the fill being optimistic.
EXECUTION_MODE_SAME_CLOSE_SLIPPAGE = "SAME_CLOSE_SLIPPAGE"

VALID_EXECUTION_MODES = frozenset(
    {EXECUTION_MODE_NEXT_OPEN, EXECUTION_MODE_SAME_CLOSE_SLIPPAGE}
)


@dataclass
class BacktestResult:
    """Result of a single `run_backtest` call."""

    stats: pd.Series
    trades: pd.DataFrame
    equity_curve: pd.DataFrame


def estimate_atr_spread_pct(df: pd.DataFrame, atr_multiple: float = 0.1) -> float:
    """Average bid/ask spread, as a fraction of price, implied by ATR.

    `backtesting.py`'s `spread` parameter is one constant applied to every
    fill for the whole run, so a genuinely per-bar ATR-scaled spread isn't
    representable - this instead prices the *whole run's* spread off the
    dataset's mean `ATR / Close` ratio, scaled by `atr_multiple`. A more
    volatile name (higher ATR relative to its price) gets a wider modelled
    spread than a quiet one, which is the part of "ATR-based slippage" that
    actually matters for comparing strategies/tickers - it is not trying to
    reproduce the exact spread on any single historical bar.

    Args:
        atr_multiple: Fraction of the mean ATR/Close ratio charged as spread,
            e.g. `0.1` -> a stock whose ATR averages 2% of price gets a 0.2%
            modelled spread.

    Returns:
        `0.0` if `df` has fewer than 2 rows or every ATR/Close ratio is
        non-finite (there is no volatility signal to price a spread off of),
        never NaN or inf.
    """
    if atr_multiple < 0:
        raise ValueError("atr_multiple must be non-negative")
    if len(df) < 2:
        return 0.0

    atr = wilder_atr(df)
    ratio = (
        (atr / df["Close"])
        .replace([float("inf"), float("-inf")], float("nan"))
        .dropna()
    )
    if ratio.empty:
        return 0.0

    spread = float(ratio.mean()) * atr_multiple
    return spread if math.isfinite(spread) and spread > 0 else 0.0


def run_backtest(
    strategy: BaseStrategy,
    df: pd.DataFrame,
    risk_manager: RiskManager,
    commission: float = 0.001,
    slippage_pct: float = 0.0005,
    fee_per_share: float = 0.0,
    atr_slippage_multiple: float = 0.0,
    execution_mode: str = EXECUTION_MODE_NEXT_OPEN,
    direction: int | None = None,
) -> BacktestResult:
    """Simulate `strategy` over historical `df` with realistic costs.

    Args:
        strategy: A `BaseStrategy` instance.
        df: OHLCV DataFrame (see AGENTS.md "Data ingestion format").
        risk_manager: Sizes positions and resolves absolute SL/TP prices.
        commission: Round-trip commission rate, e.g. 0.001 for 0.1%. Ignored
            (superseded, not stacked) if `fee_per_share` is set.
        slippage_pct: Bid/ask spread applied to fills, e.g. 0.0005 for 0.05%.
            Ignored (superseded, not stacked) if `atr_slippage_multiple` is
            set.
        fee_per_share: Fixed dollar fee per share, charged on both legs of a
            round trip (e.g. IBKR-style $0.005/share maker/taker pricing),
            via `backtesting.py`'s callable-commission hook. `0.0` (the
            default) keeps the original flat-rate `commission` behavior
            exactly - this is opt-in cost modelling, not a replacement.
        atr_slippage_multiple: When positive, replaces `slippage_pct` with
            `estimate_atr_spread_pct(df, atr_slippage_multiple)` - a spread
            that scales with the traded ticker's own volatility rather than
            one flat rate applied to every ticker alike.
        execution_mode: `NEXT_OPEN` (the default, and the prior behavior of
            this function - fills at the bar *after* the signal) or
            `SAME_CLOSE_SLIPPAGE` (fills at the signal's own bar close, via
            `backtesting.py`'s `trade_on_close`).
        direction: `1` for long-only (the default, existing behaviour),
            `-1` to also allow short entries (`signal == -1` opens a short
            when flat, `signal == 1` closes an open short). When `None` the
            engine stays long-only exactly as before this parameter was added.

    Raises:
        ValueError: if `df` is missing a required OHLCV column, or
            `execution_mode` isn't one of `VALID_EXECUTION_MODES`.

    Returns:
        BacktestResult with the summary `stats`, a `trades` DataFrame (the
        `backtesting` library's native columns - Size, EntryPrice, PnL, etc. -
        plus a lowercase `pnl` alias column for `analytics.metrics.compute_metrics`),
        and the `equity_curve`.
    """
    missing = [c for c in REQUIRED_OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"df is missing required OHLCV columns: {missing}")
    if execution_mode not in VALID_EXECUTION_MODES:
        raise ValueError(
            f"Unknown execution_mode {execution_mode!r}. "
            f"Available: {sorted(VALID_EXECUTION_MODES)}"
        )

    signals_df = strategy.generate_signals(df)
    BaseStrategy.validate_output(signals_df)

    signal = signals_df["signal"].to_numpy()
    sl_type = signals_df["sl_type"].to_numpy()
    sl_value = signals_df["sl_value"].to_numpy()
    tp_type = signals_df["tp_type"].to_numpy()
    tp_value = signals_df["tp_value"].to_numpy()
    atr = signals_df["atr"].to_numpy() if "atr" in signals_df.columns else None

    bt_df = df[list(REQUIRED_OHLCV_COLUMNS)].copy()

    _direction = direction

    class _SignalAdapter(Strategy):
        def init(self):
            pass

        def next(self):
            i = len(self.data) - 1
            sig = signal[i]

            if self.position:
                # Close on opposite signal: long closes on -1, short closes on 1.
                if _direction == 1 and sig == -1:
                    self.position.close()
                    return
                if _direction == -1 and sig == 1:
                    self.position.close()
                    return
                if _direction is None and sig == -1:
                    self.position.close()
                    return
                # Same-direction signal while already in: skip (no re-entry).
                return

            # No position: look for an entry signal.
            if _direction == 1:
                if sig != 1:
                    return
            elif _direction == -1:
                if sig != -1:
                    return
            else:  # _direction is None: long-only
                if sig != 1:
                    return

            entry_price = float(self.data.Close[-1])
            atr_value = float(atr[i]) if atr is not None else None

            order = risk_manager.build_order(
                entry_price=entry_price,
                sl_type=sl_type[i],
                sl_value=float(sl_value[i]),
                tp_type=tp_type[i],
                tp_value=float(tp_value[i]),
                direction=_direction if _direction is not None else 1,
                atr=atr_value,
            )

            if order.shares > 0:
                if _direction == -1:
                    self.sell(
                        size=order.shares, sl=order.stop_loss, tp=order.take_profit
                    )
                else:
                    self.buy(
                        size=order.shares, sl=order.stop_loss, tp=order.take_profit
                    )

    commission_param = (
        (lambda size, price: abs(size) * fee_per_share)
        if fee_per_share > 0
        else commission
    )
    spread_param = (
        estimate_atr_spread_pct(df, atr_slippage_multiple)
        if atr_slippage_multiple > 0
        else slippage_pct
    )

    bt = Backtest(
        bt_df,
        _SignalAdapter,
        cash=risk_manager.account_equity,
        commission=commission_param,
        spread=spread_param,
        exclusive_orders=True,
        trade_on_close=execution_mode == EXECUTION_MODE_SAME_CLOSE_SLIPPAGE,
    )
    stats = bt.run()

    trades = stats._trades.copy()
    trades["pnl"] = trades["PnL"]

    return BacktestResult(stats=stats, trades=trades, equity_curve=stats._equity_curve)


#: Standard benchmark account sizes an order ticket is generated for -
#: matches `quant/backtest.py`'s $1,000 baseline plus the $5k/$10k tiers the
#: product also benchmarks against.
DEFAULT_ACCOUNT_TIERS: tuple[float, ...] = (1_000.0, 5_000.0, 10_000.0)


@dataclass(frozen=True)
class OrderTicket:
    """A single broker-ready order, shaped for manual entry at Trading 212
    or IBKR: everything a trader needs to place the trade and nothing they
    have to compute themselves."""

    ticker: str
    account_equity: float
    order_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: int
    notional_value: float
    risk_amount: float
    reward_risk_ratio: float
    tradable: bool
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "account_equity": round(self.account_equity, 2),
            "order_type": self.order_type,
            "entry_price": round(self.entry_price, 4),
            "stop_loss": round(self.stop_loss, 4),
            "take_profit": round(self.take_profit, 4),
            "quantity": self.quantity,
            "notional_value": round(self.notional_value, 2),
            "risk_amount": round(self.risk_amount, 2),
            "reward_risk_ratio": round(self.reward_risk_ratio, 2),
            "tradable": self.tradable,
            "note": self.note,
        }


def build_order_ticket(
    ticker: str,
    entry_price: float,
    sl_type: str,
    sl_value: float,
    tp_type: str,
    tp_value: float,
    account_equity: float,
    atr: float | None = None,
    direction: int = 1,
    order_type: str = "MARKET",
) -> OrderTicket:
    """One broker-ready order ticket, sized under the full risk engine.

    Uses `RiskManager.build_risk_managed_order` rather than `build_order`:
    a ticket that fails the minimum reward:risk or the account's dynamic
    risk budget is exactly what this ticket exists to keep a trader from
    manually placing, so it comes back `tradable=False` with an explanatory
    `note` instead of a share count that looks actionable but shouldn't be
    acted on.
    """
    order = RiskManager(account_equity=account_equity).build_risk_managed_order(
        entry_price=entry_price,
        sl_type=sl_type,
        sl_value=sl_value,
        tp_type=tp_type,
        tp_value=tp_value,
        direction=direction,
        atr=atr,
    )
    return OrderTicket(
        ticker=ticker.upper(),
        account_equity=account_equity,
        order_type=order_type,
        entry_price=order.entry_price,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        quantity=order.shares,
        notional_value=order.shares * order.entry_price,
        risk_amount=order.risk_amount,
        reward_risk_ratio=order.reward_risk_ratio,
        tradable=not order.rejected and order.shares > 0,
        note=order.rejection_reason,
    )


def build_order_tickets(
    ticker: str,
    entry_price: float,
    sl_type: str,
    sl_value: float,
    tp_type: str,
    tp_value: float,
    account_tiers: tuple[float, ...] = DEFAULT_ACCOUNT_TIERS,
    atr: float | None = None,
    direction: int = 1,
    order_type: str = "MARKET",
) -> list[OrderTicket]:
    """One ticket per account size in `account_tiers` - the $1k/$5k/$10k
    side-by-side comparison the frontend's Order Ticket Drawer renders."""
    return [
        build_order_ticket(
            ticker,
            entry_price,
            sl_type,
            sl_value,
            tp_type,
            tp_value,
            account_equity=tier,
            atr=atr,
            direction=direction,
            order_type=order_type,
        )
        for tier in account_tiers
    ]


#: Trading days a single simulated trade is allowed to stay open before it
#: is force-closed at that bar's close as a `TIMEOUT` - a swing trade that
#: hasn't resolved to its stop or target in ~3 months is treated as "thesis
#: didn't play out" rather than left open indefinitely.
DEFAULT_MAX_HOLDING_PERIOD_DAYS = 60

EXIT_TRIGGER_STOP = "STOP"
EXIT_TRIGGER_TARGET = "TARGET"
EXIT_TRIGGER_REGIME = "REGIME"
EXIT_TRIGGER_TIMEOUT = "TIMEOUT"

VALID_EXIT_TRIGGERS = frozenset(
    {EXIT_TRIGGER_STOP, EXIT_TRIGGER_TARGET, EXIT_TRIGGER_REGIME, EXIT_TRIGGER_TIMEOUT}
)


@dataclass(frozen=True)
class ExitResolution:
    """How a single already-filled trade would have resolved, walking
    forward bar-by-bar from the fill.

    `mae_pct`/`mfe_pct` are measured over the *entire path* from entry to
    `exit_date` (the worst/best intrabar excursion at any point along the
    way), not just entry-to-exit - the whole reason a trader wants this
    over a bare P&L number.
    """

    exit_date: pd.Timestamp
    exit_price: float
    exit_trigger: str
    holding_period_days: int
    mae_pct: float
    mfe_pct: float


def resolve_trade_exit(
    df: pd.DataFrame,
    fill_bar_index: int,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    direction: int = 1,
    max_holding_period_days: int = DEFAULT_MAX_HOLDING_PERIOD_DAYS,
    use_regime_filter: bool = True,
) -> ExitResolution:
    """Walk `df` forward from the bar after `fill_bar_index`, one bar at a
    time, until the trade's stop, target, a blocked macro regime, or the
    holding-period ceiling resolves it - whichever comes first.

    Each bar `i` is evaluated using only `df.iloc[:i+1]` (bars up to and
    including `i`): the regime check at bar `i` cannot see bar `i+1`, so
    this walk carries zero lookahead beyond what a trade holding through
    bar `i` has itself already revealed.

    Priority when more than one condition is true on the same bar: `STOP`
    beats `TARGET` beats `REGIME` - the conservative assumption (a bar that
    gapped through both levels is charged the worse outcome) matches how a
    real stop-loss order would have filled first if the bar opened against
    the position.

    Args:
        fill_bar_index: Index (into `df`) of the bar the trade actually
            filled on - the walk starts at `fill_bar_index + 1`, never
            re-examining the fill bar itself.
        entry_price: The actual fill price (post-slippage), not a raw
            reference price - MAE/MFE and P&L must be measured from what
            the trade actually paid.
        direction: `1` for long, `-1` for short.
        use_regime_filter: If `True` and `direction == 1`, an `UNKNOWN`-or-
            blocked macro regime (`MacroRegimeDetector.is_long_blocked`) on
            a bar closes the trade there as `REGIME`. Shorts have no
            symmetric macro filter defined in this codebase, so this has no
            effect when `direction == -1`.

    Raises:
        ValueError: if `direction` is not `1`/`-1`, or there is no bar after
            `fill_bar_index` to walk forward on.
    """
    if direction not in (1, -1):
        raise ValueError("direction must be 1 (long) or -1 (short)")
    if fill_bar_index + 1 >= len(df):
        raise ValueError(
            "No bars after fill_bar_index are available to resolve an exit"
        )
    if max_holding_period_days < 1:
        raise ValueError("max_holding_period_days must be at least 1")

    regime_detector = (
        MacroRegimeDetector() if use_regime_filter and direction == 1 else None
    )

    last_idx = min(fill_bar_index + max_holding_period_days, len(df) - 1)
    mae_dollars = 0.0
    mfe_dollars = 0.0

    for i in range(fill_bar_index + 1, last_idx + 1):
        low = float(df["Low"].iloc[i])
        high = float(df["High"].iloc[i])

        if direction == 1:
            adverse = max(0.0, entry_price - low)
            favorable = max(0.0, high - entry_price)
            stop_touched = low <= stop_loss
            target_touched = high >= take_profit
        else:
            adverse = max(0.0, high - entry_price)
            favorable = max(0.0, entry_price - low)
            stop_touched = high >= stop_loss
            target_touched = low <= take_profit

        mae_dollars = max(mae_dollars, adverse)
        mfe_dollars = max(mfe_dollars, favorable)

        if stop_touched:
            exit_price, trigger = stop_loss, EXIT_TRIGGER_STOP
        elif target_touched:
            exit_price, trigger = take_profit, EXIT_TRIGGER_TARGET
        elif regime_detector is not None and regime_detector.is_long_blocked(
            df.iloc[: i + 1]
        ):
            exit_price, trigger = float(df["Close"].iloc[i]), EXIT_TRIGGER_REGIME
        else:
            continue

        return ExitResolution(
            exit_date=df.index[i],
            exit_price=exit_price,
            exit_trigger=trigger,
            holding_period_days=(df.index[i] - df.index[fill_bar_index]).days,
            mae_pct=mae_dollars / entry_price if entry_price else 0.0,
            mfe_pct=mfe_dollars / entry_price if entry_price else 0.0,
        )

    # Nothing triggered within the holding-period ceiling: force-close at
    # the last examined bar's close.
    timeout_idx = last_idx
    return ExitResolution(
        exit_date=df.index[timeout_idx],
        exit_price=float(df["Close"].iloc[timeout_idx]),
        exit_trigger=EXIT_TRIGGER_TIMEOUT,
        holding_period_days=(df.index[timeout_idx] - df.index[fill_bar_index]).days,
        mae_pct=mae_dollars / entry_price if entry_price else 0.0,
        mfe_pct=mfe_dollars / entry_price if entry_price else 0.0,
    )
