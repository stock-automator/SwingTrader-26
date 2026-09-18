"""
Dynamic risk & position sizing.

Converts the abstract, relative SL/TP definitions produced by a
`BaseStrategy` (`sl_type`/`sl_value`/`tp_type`/`tp_value`) into absolute
stop-loss and take-profit prices, and sizes the position so a stopped-out
trade loses no more than a fixed percentage of account equity.

Also carries the portfolio-level risk controls layered on top of that
per-trade sizing (`build_risk_managed_order`, `CircuitBreaker`): a minimum
reward:risk ratio, a sliding 1%-2% risk budget scaled by setup quality, a
cap on total risk open across every position at once, and a rolling
drawdown kill-switch. These are additive - `build_order` (used by
`engine.py` and every existing strategy/test) is untouched, so the new gate
is opt-in for callers that want it, e.g. the live screener's Trading
212/IBKR entry path.
"""

import math
from dataclasses import dataclass

import pandas as pd

from backend.app.quant.strategies.base import VALID_LEVEL_TYPES

#: Minimum acceptable reward:risk ratio for `build_risk_managed_order` to
#: accept an entry signal at all. Below this, the setup is rejected outright
#: regardless of sizing - no position size fixes a bad trade structure.
MIN_REWARD_RISK_RATIO = 2.5

#: Dynamic per-trade risk budget scales linearly with reward:risk between
#: these bounds: a setup right at the minimum acceptable R gets the floor,
#: one at or above `DYNAMIC_RISK_R_CAP` gets the ceiling.
MIN_RISK_PER_TRADE_PCT = 0.01
MAX_RISK_PER_TRADE_PCT = 0.02
DYNAMIC_RISK_R_CAP = 4.0

#: Hard ceiling on risk committed across every open position at once, so a
#: string of independently-sized 2% setups can't stack into an account-wide
#: exposure nothing here individually would allow.
MAX_PORTFOLIO_RISK_PCT = 0.06

#: Circuit-breaker: rolling peak-to-trough equity drawdown over this many
#: trading bars that exceeds this fraction halts new long setups.
DRAWDOWN_KILL_SWITCH_PCT = 0.10
DRAWDOWN_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class Order:
    """Fully resolved, actionable order parameters for a single trade.

    `reward_risk_ratio`/`rejected`/`rejection_reason` are only meaningful
    for orders built via `build_risk_managed_order` - `build_order` leaves
    them at their defaults since it has no minimum-R or portfolio-cap
    concept to reject against.
    """

    shares: int
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    risk_per_share: float
    reward_risk_ratio: float = 0.0
    rejected: bool = False
    rejection_reason: str | None = None


class RiskManager:
    """Converts strategy signals into sized, absolute-price orders.

    Args:
        account_equity: Current account equity in the account's currency.
        risk_per_trade_pct: Fraction of `account_equity` to risk on a single
            trade if the stop-loss is hit, e.g. 0.02 for 2%.
    """

    def __init__(self, account_equity: float, risk_per_trade_pct: float = 0.02):
        if account_equity <= 0:
            raise ValueError("account_equity must be positive")
        if not 0 < risk_per_trade_pct <= 1:
            raise ValueError("risk_per_trade_pct must be in (0, 1]")

        self.account_equity = account_equity
        self.risk_per_trade_pct = risk_per_trade_pct

    @staticmethod
    def _resolve_offset(
        level_type: str, value: float, entry_price: float, atr: float | None
    ) -> float:
        if level_type not in VALID_LEVEL_TYPES:
            raise ValueError(f"Unknown level type: {level_type!r}")
        # NaN first: it is False against every comparison, so a bare
        # `value <= 0` waves it through and it resurfaces much later as an
        # opaque "cannot convert float NaN to integer" out of position_size.
        # A strategy emitting NaN sl_value on an active bar is the realistic
        # source, so name it here.
        if not math.isfinite(value):
            raise ValueError(f"value must be finite, got {value!r}")
        if value <= 0:
            raise ValueError("value must be positive")

        if level_type == "PERCENTAGE":
            return entry_price * value
        if level_type == "FIXED":
            return value
        # ATR
        if atr is None:
            raise ValueError("atr must be supplied for ATR-based SL/TP")
        if not math.isfinite(atr):
            raise ValueError(f"atr must be finite, got {atr!r}")
        if atr <= 0:
            raise ValueError("atr must be positive")
        return value * atr

    def resolve_stop_loss(
        self,
        entry_price: float,
        sl_type: str,
        sl_value: float,
        atr: float | None = None,
        direction: int = 1,
    ) -> float:
        """Convert a relative stop-loss definition into an absolute price.

        Args:
            direction: 1 for a long position, -1 for a short position.
        """
        offset = self._resolve_offset(sl_type, sl_value, entry_price, atr)
        return entry_price - offset if direction == 1 else entry_price + offset

    def resolve_take_profit(
        self,
        entry_price: float,
        tp_type: str,
        tp_value: float,
        atr: float | None = None,
        direction: int = 1,
    ) -> float:
        """Convert a relative take-profit definition into an absolute price.

        Args:
            direction: 1 for a long position, -1 for a short position.
        """
        offset = self._resolve_offset(tp_type, tp_value, entry_price, atr)
        return entry_price + offset if direction == 1 else entry_price - offset

    def position_size(self, entry_price: float, stop_loss_price: float) -> int:
        """Whole shares such that hitting `stop_loss_price` loses at most
        `risk_per_trade_pct` of `account_equity`. Returns 0 if the stop is at
        (or on the wrong side of) the entry price."""
        risk_per_share = abs(entry_price - stop_loss_price)
        if risk_per_share <= 0:
            return 0

        risk_amount = self.account_equity * self.risk_per_trade_pct
        return int(risk_amount // risk_per_share)

    def volatility_parity_size(self, atr: float, atr_multiple: float = 2.0) -> int:
        """Whole shares sized off the asset's own ATR rather than a
        strategy-chosen stop distance.

        Unlike `position_size`, which sizes from the actual entry/stop
        price gap, this sizes from `atr_multiple * atr` directly - so two
        strategies with different stop placement on the same asset get the
        same position size for the same target volatility exposure, and
        position size scales inversely with the asset's volatility (a more
        volatile asset gets fewer shares for the same risk budget).

        This is a raw volatility-parity count and is *not* itself bounded by
        `risk_per_trade_pct` against the strategy's actual stop - if the
        resolved stop sits closer than `atr_multiple * atr`, this size risks
        more than the budget. `build_order` caps it for that reason; call
        this directly only if you are enforcing the bound yourself.

        Args:
            atr: The asset's current Average True Range (absolute price units).
            atr_multiple: Volatility multiple defining the assumed risk
                distance, e.g. `2.0` -> risk budget spans 2x ATR.

        Raises:
            ValueError: if `atr` or `atr_multiple` is not positive and finite.
                NaN is rejected explicitly - it would slip past a bare `<= 0`
                comparison and surface later as an opaque
                "cannot convert float NaN to integer".
        """
        if not math.isfinite(atr):
            raise ValueError("atr must be finite")
        if atr <= 0:
            raise ValueError("atr must be positive")
        if not math.isfinite(atr_multiple):
            raise ValueError("atr_multiple must be finite")
        if atr_multiple <= 0:
            raise ValueError("atr_multiple must be positive")

        risk_amount = self.account_equity * self.risk_per_trade_pct
        risk_per_share = atr_multiple * atr
        return int(risk_amount // risk_per_share)

    def build_order(
        self,
        entry_price: float,
        sl_type: str,
        sl_value: float,
        tp_type: str,
        tp_value: float,
        direction: int = 1,
        atr: float | None = None,
        sizing_method: str = "fixed_risk",
        atr_multiple: float = 2.0,
    ) -> Order:
        """Resolve SL/TP and size in one call.

        Args:
            direction: 1 for a long position, -1 for a short position.
            sizing_method: `'fixed_risk'` (default) sizes from the actual
                entry/stop price distance via `position_size`. `'vol_parity'`
                sizes from `atr_multiple * atr` via `volatility_parity_size`
                and then caps that at the fixed-risk size, so it can only
                ever size *down* relative to the risk budget - requires
                `atr`.

        Raises:
            ValueError: if `sizing_method` is not one of the above.
        """
        stop_loss = self.resolve_stop_loss(
            entry_price, sl_type, sl_value, atr, direction
        )
        take_profit = self.resolve_take_profit(
            entry_price, tp_type, tp_value, atr, direction
        )

        if sizing_method == "fixed_risk":
            shares = self.position_size(entry_price, stop_loss)
        elif sizing_method == "vol_parity":
            if atr is None:
                raise ValueError("atr must be supplied for vol_parity sizing")
            # Volatility-parity size, floored by the fixed-risk size so the
            # module invariant - a stopped-out trade loses at most
            # `risk_per_trade_pct` of equity - still holds. An ATR-derived
            # count says nothing about where the strategy actually put the
            # stop, so uncapped it can risk an unbounded multiple of the
            # budget (2x ATR of 0.5 against a 10% stop is 10x over).
            shares = min(
                self.volatility_parity_size(atr, atr_multiple),
                self.position_size(entry_price, stop_loss),
            )
        else:
            raise ValueError(f"Unknown sizing_method: {sizing_method!r}")

        risk_per_share = abs(entry_price - stop_loss)
        return Order(
            shares=shares,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            # Actual risk of this order, not the budget: these must agree
            # with `shares * risk_per_share` or consumers reporting
            # `risk_amount` understate the position.
            risk_amount=shares * risk_per_share,
            risk_per_share=risk_per_share,
        )

    @staticmethod
    def reward_to_risk(
        entry_price: float, stop_loss: float, take_profit: float
    ) -> float:
        """The `R` in "a 3R setup": reward distance over risk distance.

        Returns `0.0`, not inf, when the stop sits at the entry price - a
        zero-risk-distance order has no defined R and the caller (`build_
        risk_managed_order`) must treat that as a reject, not as an
        infinitely good trade.
        """
        risk = abs(entry_price - stop_loss)
        if risk <= 0:
            return 0.0
        reward = abs(take_profit - entry_price)
        return reward / risk

    @staticmethod
    def dynamic_risk_pct(
        reward_risk_ratio: float,
        min_r: float = MIN_REWARD_RISK_RATIO,
        r_cap: float = DYNAMIC_RISK_R_CAP,
        min_pct: float = MIN_RISK_PER_TRADE_PCT,
        max_pct: float = MAX_RISK_PER_TRADE_PCT,
    ) -> float:
        """Risk-per-trade fraction scaled by setup quality.

        Linear between `(min_r, min_pct)` and `(r_cap, max_pct)`; clamped to
        `min_pct` below `min_r` and `max_pct` at or above `r_cap`. A 2.5R
        setup risks 1% of equity, a 4R-or-better setup risks the full 2%,
        and everything between scales in proportion - so the account is
        risking more only where the trade structure justifies it.
        """
        if reward_risk_ratio <= min_r:
            return min_pct
        if reward_risk_ratio >= r_cap:
            return max_pct
        frac = (reward_risk_ratio - min_r) / (r_cap - min_r)
        return min_pct + frac * (max_pct - min_pct)

    def build_risk_managed_order(
        self,
        entry_price: float,
        sl_type: str,
        sl_value: float,
        tp_type: str,
        tp_value: float,
        direction: int = 1,
        atr: float | None = None,
        min_reward_risk_ratio: float = MIN_REWARD_RISK_RATIO,
        portfolio_open_risk_pct: float = 0.0,
        max_portfolio_risk_pct: float = MAX_PORTFOLIO_RISK_PCT,
    ) -> Order:
        """Resolve, gate, and size an entry signal under the full risk engine.

        Unlike `build_order`, this can refuse the trade outright: a returned
        `Order` with `rejected=True` and `shares=0` means "do not take this
        setup", not "size rounded to zero". Three independent gates, any one
        of which rejects:

        1. Reward:risk below `min_reward_risk_ratio` - a structurally bad
           trade no amount of sizing fixes.
        2. The account is already carrying `>= max_portfolio_risk_pct` of
           equity at risk across other open positions (`portfolio_open_
           risk_pct`), leaving no budget for another one.
        3. Sizing (dynamic risk pct, then the portfolio's remaining budget
           if that's tighter) still rounds to zero whole shares.

        Args:
            portfolio_open_risk_pct: Fraction of equity already at risk
                across every other currently-open position, e.g. `0.045` for
                4.5%. The caller (not this method) is responsible for
                tracking open positions and summing their `risk_amount` /
                `account_equity`.

        Raises:
            ValueError: via `resolve_stop_loss`/`resolve_take_profit` for a
                malformed sl/tp definition - the same failure mode as
                `build_order`.
        """
        stop_loss = self.resolve_stop_loss(
            entry_price, sl_type, sl_value, atr, direction
        )
        take_profit = self.resolve_take_profit(
            entry_price, tp_type, tp_value, atr, direction
        )
        risk_per_share = abs(entry_price - stop_loss)
        rr = self.reward_to_risk(entry_price, stop_loss, take_profit)

        def _rejected(reason: str) -> Order:
            return Order(
                shares=0,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_amount=0.0,
                risk_per_share=risk_per_share,
                reward_risk_ratio=rr,
                rejected=True,
                rejection_reason=reason,
            )

        if rr < min_reward_risk_ratio:
            return _rejected(
                f"Reward:risk {rr:.2f}R is below the {min_reward_risk_ratio:.1f}R minimum"
            )

        remaining_budget_pct = max_portfolio_risk_pct - portfolio_open_risk_pct
        if remaining_budget_pct <= 0:
            return _rejected(
                f"Portfolio risk cap reached: {portfolio_open_risk_pct * 100:.1f}% "
                f"of equity already at risk against a {max_portfolio_risk_pct * 100:.1f}% cap"
            )

        risk_pct = min(self.dynamic_risk_pct(rr), remaining_budget_pct)
        shares = int((self.account_equity * risk_pct) // risk_per_share)

        if shares <= 0:
            return _rejected(
                "Stop distance exceeds the available risk budget at this "
                "account size - sizing rounds to zero shares"
            )

        return Order(
            shares=shares,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_amount=shares * risk_per_share,
            risk_per_share=risk_per_share,
            reward_risk_ratio=rr,
            rejected=False,
            rejection_reason=None,
        )


class CircuitBreaker:
    """Rolling drawdown kill-switch: halts new long setups after a sharp
    equity slide, independent of any single trade's own risk math.

    Args:
        max_drawdown_pct: Trip threshold, e.g. `0.10` for 10%.
        lookback_days: Rolling window (in bars) the peak is measured over -
            a slide that happened further back than this has already been
            "recovered from" as far as the breaker is concerned.
    """

    def __init__(
        self,
        max_drawdown_pct: float = DRAWDOWN_KILL_SWITCH_PCT,
        lookback_days: int = DRAWDOWN_LOOKBACK_DAYS,
    ):
        if not 0 < max_drawdown_pct < 1:
            raise ValueError("max_drawdown_pct must be in (0, 1)")
        if lookback_days < 2:
            raise ValueError("lookback_days must be at least 2")

        self.max_drawdown_pct = max_drawdown_pct
        self.lookback_days = lookback_days

    def rolling_drawdown(self, equity_curve: pd.Series) -> pd.Series:
        """Peak-to-trough drawdown at each bar, measured against the
        trailing `lookback_days`' own running peak (not the all-time peak) -
        a fraction, e.g. `-0.12` for a 12% pullback from the recent high.

        Raises:
            ValueError: if `equity_curve` is empty.
        """
        if equity_curve.empty:
            raise ValueError("equity_curve is empty")

        equity = equity_curve.astype("float64")
        peak = equity.rolling(window=self.lookback_days, min_periods=1).max()
        return (equity - peak) / peak

    def is_triggered(self, equity_curve: pd.Series) -> bool:
        """Whether the latest bar's rolling drawdown breaches the threshold."""
        drawdown = self.rolling_drawdown(equity_curve)
        latest = float(drawdown.iloc[-1])
        return math.isfinite(latest) and latest <= -self.max_drawdown_pct
