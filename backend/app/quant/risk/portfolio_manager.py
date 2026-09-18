"""
Portfolio-level allocation overlay.

Takes a ranked signal list (`ranker.SignalRanker.rank`'s output) and turns
it into a trimmed, broker-ready allocation manifest: sized via ATR
volatility parity, then walked in rank order applying a per-sector notional
cap and a total portfolio-heat (risk-at-stop) cap. This is a *greedy,
rank-ordered* allocator - the best-ranked candidate gets first claim on
both budgets, and anything ranked lower only gets what's left. It does not
re-optimize globally (e.g. it will not bump a lower-ranked, better-fitting
candidate ahead of a higher-ranked one that used up the sector budget) -
that is a deliberate simplicity trade-off, not an oversight.
"""

import math
from dataclasses import dataclass, field

from .ranker import RankedSignal

DIRECTION_LONG = "LONG"

#: Hard ceiling on total risk-at-stop across every allocated trade at once,
#: as a fraction of account equity.
DEFAULT_MAX_PORTFOLIO_HEAT_PCT = 0.03

#: Hard ceiling on notional exposure to any single sector, as a fraction of
#: account equity.
DEFAULT_MAX_SECTOR_CONCENTRATION_PCT = 0.20

#: Bucket for candidates with `sector is None` - still subject to the
#: concentration cap like any named sector, so a pile of unmapped tickers
#: can't collectively dodge the limit just because nobody labelled them.
UNKNOWN_SECTOR = "UNKNOWN"

#: The ATR volatility-parity multiplier (`baseline_vol / asset_vol`) is
#: clamped to this range. An asset with near-zero ATR would otherwise blow
#: the multiplier toward infinity and swamp the whole allocation; an
#: extremely volatile one would otherwise shrink to a token size.
VOL_PARITY_MULTIPLIER_BOUNDS = (0.25, 4.0)


@dataclass(frozen=True)
class AllocatedTrade:
    """One candidate's outcome after sizing and the sector/heat overlay -
    rejected candidates are kept (with `shares=0` and a `trim_reason`), not
    dropped, so a caller can show *why* a signal didn't make the cut."""

    ticker: str
    strategy: str
    sector: str
    composite_score: float
    rank: int
    entry_price: float
    stop_loss: float
    take_profit: float
    requested_shares: int
    shares: int
    risk_amount: float
    risk_pct_of_equity: float
    notional_value: float
    trimmed: bool
    rejected: bool
    trim_reason: str | None

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "strategy": self.strategy,
            "sector": self.sector,
            "composite_score": round(self.composite_score, 6),
            "rank": self.rank,
            "entry_price": round(self.entry_price, 4),
            "stop_loss": round(self.stop_loss, 4),
            "take_profit": round(self.take_profit, 4),
            "requested_shares": self.requested_shares,
            "shares": self.shares,
            "risk_amount": round(self.risk_amount, 2),
            "risk_pct_of_equity": round(self.risk_pct_of_equity, 6),
            "notional_value": round(self.notional_value, 2),
            "trimmed": self.trimmed,
            "rejected": self.rejected,
            "trim_reason": self.trim_reason,
        }


@dataclass(frozen=True)
class AllocationManifest:
    """The full result of one `PortfolioManager.build_allocation` call."""

    trades: list[AllocatedTrade] = field(default_factory=list)
    total_risk_pct: float = 0.0
    total_notional: float = 0.0
    sector_exposure_pct: dict[str, float] = field(default_factory=dict)
    baseline_vol_pct: float = 0.0

    def as_dict(self) -> dict:
        return {
            "trades": [t.as_dict() for t in self.trades],
            "total_risk_pct": round(self.total_risk_pct, 6),
            "total_notional": round(self.total_notional, 2),
            "sector_exposure_pct": {
                k: round(v, 6) for k, v in self.sector_exposure_pct.items()
            },
            "baseline_vol_pct": round(self.baseline_vol_pct, 6),
        }


class PortfolioManager:
    """Sizes and trims a ranked signal list under portfolio-wide risk caps.

    Args:
        account_equity: Current account equity.
        risk_per_trade_pct: Base per-trade risk fraction the ATR
            volatility-parity formula sizes from before any cap trims it.
        max_portfolio_heat_pct: Hard ceiling on summed risk-at-stop across
            every allocated trade, as a fraction of `account_equity`.
        max_sector_concentration_pct: Hard ceiling on notional exposure to
            any single sector, as a fraction of `account_equity`.
        baseline_vol_pct: Reference volatility (ATR / price, a fraction) the
            vol-parity multiplier is measured against. `None` (the default)
            computes it fresh per `build_allocation` call as the median
            asset volatility across that batch's ATR-bearing candidates -
            self-normalizing, so the median-volatility name in *today's*
            candidate set gets exactly the plain risk-based size, calmer
            names size up, choppier names size down, with no external
            benchmark fetch required. Pass an explicit value to instead
            size against a fixed reference across calls.

    Raises:
        ValueError: if `account_equity` is not positive, if
            `risk_per_trade_pct`/`max_portfolio_heat_pct`/
            `max_sector_concentration_pct` is not in `(0, 1]`, or if
            `baseline_vol_pct` is given and not positive.
    """

    def __init__(
        self,
        account_equity: float,
        risk_per_trade_pct: float = 0.02,
        max_portfolio_heat_pct: float = DEFAULT_MAX_PORTFOLIO_HEAT_PCT,
        max_sector_concentration_pct: float = DEFAULT_MAX_SECTOR_CONCENTRATION_PCT,
        baseline_vol_pct: float | None = None,
    ):
        if account_equity <= 0:
            raise ValueError("account_equity must be positive")
        if not 0 < risk_per_trade_pct <= 1:
            raise ValueError("risk_per_trade_pct must be in (0, 1]")
        if not 0 < max_portfolio_heat_pct <= 1:
            raise ValueError("max_portfolio_heat_pct must be in (0, 1]")
        if not 0 < max_sector_concentration_pct <= 1:
            raise ValueError("max_sector_concentration_pct must be in (0, 1]")
        if baseline_vol_pct is not None and baseline_vol_pct <= 0:
            raise ValueError("baseline_vol_pct must be positive if given")

        self.account_equity = account_equity
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_portfolio_heat_pct = max_portfolio_heat_pct
        self.max_sector_concentration_pct = max_sector_concentration_pct
        self.baseline_vol_pct = baseline_vol_pct

    @staticmethod
    def _asset_vol(candidate: RankedSignal, fallback: float) -> float:
        """ATR expressed as a fraction of entry price, so volatility is
        comparable across tickers of different absolute price. `fallback`
        (the batch's baseline) stands in for a candidate with no ATR - there
        is nothing to compare, so it gets a neutral 1.0x multiplier."""
        if candidate.atr is None or candidate.entry_price <= 0:
            return fallback
        return candidate.atr / candidate.entry_price

    def _resolve_baseline_vol(self, ranked: list[RankedSignal]) -> float:
        if self.baseline_vol_pct is not None:
            return self.baseline_vol_pct

        vols = sorted(
            c.atr / c.entry_price
            for c in ranked
            if c.atr is not None and c.entry_price > 0 and math.isfinite(c.atr)
        )
        if not vols:
            # Nothing to normalize against - every candidate falls back to
            # a neutral 1.0x multiplier via `_asset_vol`'s fallback path, so
            # the actual value here only needs to be positive and consistent.
            return 1.0

        mid = len(vols) // 2
        if len(vols) % 2 == 1:
            return vols[mid]
        return (vols[mid - 1] + vols[mid]) / 2

    def build_allocation(self, ranked: list[RankedSignal]) -> AllocationManifest:
        """Size and trim `ranked` (best first) under the sector and
        portfolio-heat caps.

        Processing is strictly in rank order: each candidate's sizing and
        trim decisions only ever look at budget already consumed by
        higher-ranked candidates processed earlier in this same call, never
        at candidates still to come.
        """
        baseline_vol = self._resolve_baseline_vol(ranked)
        heat_budget = self.max_portfolio_heat_pct * self.account_equity
        sector_budget = self.max_sector_concentration_pct * self.account_equity

        sector_notional_used: dict[str, float] = {}
        heat_used = 0.0
        trades: list[AllocatedTrade] = []

        for candidate in ranked:
            sector = candidate.sector or UNKNOWN_SECTOR

            if candidate.direction != DIRECTION_LONG:
                trades.append(
                    self._rejected(
                        candidate, sector, "not tradable: platform is long-only"
                    )
                )
                continue

            risk_per_share = candidate.entry_price - candidate.stop_loss
            if risk_per_share <= 0:
                trades.append(
                    self._rejected(
                        candidate, sector, "invalid stop: entry_price <= stop_loss"
                    )
                )
                continue

            asset_vol = self._asset_vol(candidate, baseline_vol)
            multiplier = baseline_vol / asset_vol if asset_vol > 0 else 1.0
            multiplier = min(
                max(multiplier, VOL_PARITY_MULTIPLIER_BOUNDS[0]),
                VOL_PARITY_MULTIPLIER_BOUNDS[1],
            )

            requested_shares = math.floor(
                (self.account_equity * self.risk_per_trade_pct)
                / risk_per_share
                * multiplier
            )
            requested_shares = max(requested_shares, 0)

            shares = requested_shares
            reasons: list[str] = []

            # Sector cap.
            sector_used = sector_notional_used.get(sector, 0.0)
            sector_room = sector_budget - sector_used
            if sector_room <= 0:
                trades.append(
                    self._rejected(
                        candidate,
                        sector,
                        "sector concentration cap reached",
                        requested_shares,
                    )
                )
                continue
            max_shares_for_sector = math.floor(sector_room / candidate.entry_price)
            if shares > max_shares_for_sector:
                shares = max_shares_for_sector
                reasons.append("sector concentration cap reached")

            if shares <= 0:
                trades.append(
                    self._rejected(
                        candidate,
                        sector,
                        "sector concentration cap reached",
                        requested_shares,
                    )
                )
                continue

            # Portfolio heat cap.
            heat_room = heat_budget - heat_used
            if heat_room <= 0:
                trades.append(
                    self._rejected(
                        candidate,
                        sector,
                        "portfolio heat cap reached",
                        requested_shares,
                    )
                )
                continue
            max_shares_for_heat = math.floor(heat_room / risk_per_share)
            if shares > max_shares_for_heat:
                shares = max_shares_for_heat
                reasons.append("portfolio heat cap reached")

            if shares <= 0:
                trades.append(
                    self._rejected(
                        candidate,
                        sector,
                        "portfolio heat cap reached",
                        requested_shares,
                    )
                )
                continue

            risk_amount = shares * risk_per_share
            notional_value = shares * candidate.entry_price
            sector_notional_used[sector] = sector_used + notional_value
            heat_used += risk_amount

            trades.append(
                AllocatedTrade(
                    ticker=candidate.ticker,
                    strategy=candidate.strategy,
                    sector=sector,
                    composite_score=candidate.composite_score,
                    rank=candidate.rank,
                    entry_price=candidate.entry_price,
                    stop_loss=candidate.stop_loss,
                    take_profit=candidate.take_profit,
                    requested_shares=requested_shares,
                    shares=shares,
                    risk_amount=risk_amount,
                    risk_pct_of_equity=risk_amount / self.account_equity,
                    notional_value=notional_value,
                    trimmed=shares < requested_shares,
                    rejected=False,
                    trim_reason=(", ".join(reasons) if reasons else None),
                )
            )

        total_risk_pct = sum(t.risk_pct_of_equity for t in trades if not t.rejected)
        total_notional = sum(t.notional_value for t in trades if not t.rejected)
        sector_exposure_pct = {
            sector: notional / self.account_equity
            for sector, notional in sector_notional_used.items()
        }

        return AllocationManifest(
            trades=trades,
            total_risk_pct=total_risk_pct,
            total_notional=total_notional,
            sector_exposure_pct=sector_exposure_pct,
            baseline_vol_pct=baseline_vol,
        )

    @staticmethod
    def _rejected(
        candidate: RankedSignal,
        sector: str,
        reason: str,
        requested_shares: int = 0,
    ) -> AllocatedTrade:
        return AllocatedTrade(
            ticker=candidate.ticker,
            strategy=candidate.strategy,
            sector=sector,
            composite_score=candidate.composite_score,
            rank=candidate.rank,
            entry_price=candidate.entry_price,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            requested_shares=requested_shares,
            shares=0,
            risk_amount=0.0,
            risk_pct_of_equity=0.0,
            notional_value=0.0,
            trimmed=False,
            rejected=True,
            trim_reason=reason,
        )
