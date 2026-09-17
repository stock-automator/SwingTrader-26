"""
Dynamic risk & position sizing.

Converts the abstract, relative SL/TP definitions produced by a
`BaseStrategy` (`sl_type`/`sl_value`/`tp_type`/`tp_value`) into absolute
stop-loss and take-profit prices, and sizes the position so a stopped-out
trade loses no more than a fixed percentage of account equity.
"""

import math
from dataclasses import dataclass

from src.strategies.base_strategy import VALID_LEVEL_TYPES


@dataclass(frozen=True)
class Order:
    """Fully resolved, actionable order parameters for a single trade."""

    shares: int
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    risk_per_share: float


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
