"""
Deterministic composite signal ranking.

Turns a batch of candidate setups - drawn from the live signal matrix,
across possibly many strategies/tickers at once - into a single ordered
list a downstream allocator (`portfolio_manager.PortfolioManager`) can walk
top-to-bottom. The score blends three independent signal-quality axes so no
single one dominates: how well the strategy has actually held up out of
sample (`wfe`), its risk-adjusted historical return (`sharpe`), and how well
today's regime fits the strategy's assumptions (`regime_score`).
"""

from dataclasses import dataclass

#: Composite score weights - fixed, not configurable, so "the ranking" means
#: the same thing everywhere it's computed rather than drifting per caller.
WFE_WEIGHT = 0.4
SHARPE_WEIGHT = 0.3
REGIME_SCORE_WEIGHT = 0.3

#: Decimal places composite scores are rounded to before tie-breaking.
#: Two scores that differ only in float noise far below this precision are
#: for ranking purposes identical - without this, `rank()` could return a
#: different order across runs on the same input purely from summation
#: order/float representation, defeating the entire point of a
#: "deterministic" ranker feeding a downstream allocator.
TIE_BREAK_DECIMALS = 6


def composite_score(wfe: float, sharpe: float, regime_score: float) -> float:
    """`0.4*wfe + 0.3*sharpe + 0.3*regime_score`, exactly - kept as a
    standalone function so the formula itself is independently testable
    without constructing a `CandidateSignal`."""
    return (
        WFE_WEIGHT * wfe + SHARPE_WEIGHT * sharpe + REGIME_SCORE_WEIGHT * regime_score
    )


@dataclass(frozen=True)
class CandidateSignal:
    """One candidate setup, as fed into the ranker.

    Field vocabulary matches `quant/setups.py`: `direction` is one of
    `"LONG"`/`"SHORT"`/`"EXIT_LONG"`/`"FLAT"`. `sector`/`wfe`/`sharpe`/
    `regime_score` are supplied by the caller - this module has no data
    pipeline of its own to derive them from.

    Attributes:
        sector: `None` means unmapped; `portfolio_manager.PortfolioManager`
            buckets unmapped candidates under its own `UNKNOWN_SECTOR`
            constant rather than exempting them from the concentration cap.
        wfe: Walk-forward efficiency, a fraction (e.g. from
            `quant/walk_forward.py`'s output) - not bounded/validated here.
        sharpe: The strategy's backtested Sharpe ratio.
        regime_score: Caller-supplied 0-1 regime-fit score.
    """

    ticker: str
    strategy: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float | None
    sector: str | None
    wfe: float
    sharpe: float
    regime_score: float


@dataclass(frozen=True)
class RankedSignal(CandidateSignal):
    """A `CandidateSignal` plus its resolved rank."""

    composite_score: float = 0.0
    rank: int = 0


def _sort_key(candidate: CandidateSignal) -> tuple:
    """Descending composite score (rounded, so equal-within-noise scores tie
    rather than ordering on float dust), then descending wfe, then
    descending sharpe, then ascending ticker - the last four fields make
    `rank()` produce the exact same order on every call over the same input,
    which is the property a deterministic ranker exists to guarantee."""
    score = round(
        composite_score(candidate.wfe, candidate.sharpe, candidate.regime_score),
        TIE_BREAK_DECIMALS,
    )
    return (-score, -candidate.wfe, -candidate.sharpe, candidate.ticker)


class SignalRanker:
    """Ranks candidate signals by composite score, best first."""

    @staticmethod
    def rank(candidates: list[CandidateSignal]) -> list[RankedSignal]:
        """Sort `candidates` descending by composite score (see `_sort_key`
        for the full deterministic tie-break chain) and attach `rank`
        (1 = best)."""
        ordered = sorted(candidates, key=_sort_key)
        ranked = []
        for i, candidate in enumerate(ordered):
            fields = {
                f: getattr(candidate, f) for f in CandidateSignal.__dataclass_fields__
            }
            ranked.append(
                RankedSignal(
                    **fields,
                    composite_score=composite_score(
                        candidate.wfe, candidate.sharpe, candidate.regime_score
                    ),
                    rank=i + 1,
                )
            )
        return ranked
