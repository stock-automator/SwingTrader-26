"""
Risk management package: per-trade sizing, portfolio-level constraints, and
signal ranking.

`manager.py` (`RiskManager`, `CircuitBreaker`, `Order`, and the risk-tuning
constants) is the original single-file `quant/risk.py` module, moved here
unchanged and re-exported below so every existing `from ..quant.risk import
RiskManager`-style import keeps working - this package split only adds
`ranker.py` (deterministic composite signal scoring) and
`portfolio_manager.py` (the sector/heat-capped allocation overlay on top of
a ranked signal list) alongside it, it does not change `manager.py`'s
public contract.
"""

from .manager import (
    DRAWDOWN_KILL_SWITCH_PCT,
    DRAWDOWN_LOOKBACK_DAYS,
    DYNAMIC_RISK_R_CAP,
    MAX_PORTFOLIO_RISK_PCT,
    MAX_RISK_PER_TRADE_PCT,
    MIN_REWARD_RISK_RATIO,
    MIN_RISK_PER_TRADE_PCT,
    CircuitBreaker,
    Order,
    RiskManager,
)
from .portfolio_manager import (
    DEFAULT_MAX_PORTFOLIO_HEAT_PCT,
    DEFAULT_MAX_SECTOR_CONCENTRATION_PCT,
    UNKNOWN_SECTOR,
    VOL_PARITY_MULTIPLIER_BOUNDS,
    AllocatedTrade,
    AllocationManifest,
    PortfolioManager,
)
from .ranker import CandidateSignal, RankedSignal, SignalRanker, composite_score

__all__ = [
    "DRAWDOWN_KILL_SWITCH_PCT",
    "DRAWDOWN_LOOKBACK_DAYS",
    "DYNAMIC_RISK_R_CAP",
    "MAX_PORTFOLIO_RISK_PCT",
    "MAX_RISK_PER_TRADE_PCT",
    "MIN_REWARD_RISK_RATIO",
    "MIN_RISK_PER_TRADE_PCT",
    "CircuitBreaker",
    "Order",
    "RiskManager",
    "DEFAULT_MAX_PORTFOLIO_HEAT_PCT",
    "DEFAULT_MAX_SECTOR_CONCENTRATION_PCT",
    "UNKNOWN_SECTOR",
    "VOL_PARITY_MULTIPLIER_BOUNDS",
    "AllocatedTrade",
    "AllocationManifest",
    "PortfolioManager",
    "CandidateSignal",
    "RankedSignal",
    "SignalRanker",
    "composite_score",
]
