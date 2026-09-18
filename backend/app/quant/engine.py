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
from backend.app.quant.risk import RiskManager
from backend.app.quant.strategies.base import BaseStrategy

REQUIRED_OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")

#: Trading 212 charges no per-share commission on its default long-term
#: investing accounts, but IBKR's US stock tier does, and modelling *some*
#: per-share friction is the point - $0.005/share (IBKR's own published
#: tiered-pricing rate) is the default fixed maker/taker fee applied to both
#: legs of a round trip when `fee_per_share` is set.
DEFAULT_FEE_PER_SHARE = 0.005


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

    Returns:
        BacktestResult with the summary `stats`, a `trades` DataFrame (the
        `backtesting` library's native columns - Size, EntryPrice, PnL, etc. -
        plus a lowercase `pnl` alias column for `analytics.metrics.compute_metrics`),
        and the `equity_curve`.
    """
    missing = [c for c in REQUIRED_OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"df is missing required OHLCV columns: {missing}")

    signals_df = strategy.generate_signals(df)
    BaseStrategy.validate_output(signals_df)

    signal = signals_df["signal"].to_numpy()
    sl_type = signals_df["sl_type"].to_numpy()
    sl_value = signals_df["sl_value"].to_numpy()
    tp_type = signals_df["tp_type"].to_numpy()
    tp_value = signals_df["tp_value"].to_numpy()
    atr = signals_df["atr"].to_numpy() if "atr" in signals_df.columns else None

    bt_df = df[list(REQUIRED_OHLCV_COLUMNS)].copy()

    class _SignalAdapter(Strategy):
        def init(self):
            pass

        def next(self):
            i = len(self.data) - 1
            sig = signal[i]

            if self.position:
                if sig == -1:
                    self.position.close()
                return

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
                direction=1,
                atr=atr_value,
            )

            if order.shares > 0:
                self.buy(size=order.shares, sl=order.stop_loss, tp=order.take_profit)

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
