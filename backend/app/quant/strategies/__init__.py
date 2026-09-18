"""Strategy implementations and the name -> class registry the API resolves
`strategy` request fields through. Register a new strategy in `REGISTRY` and
it becomes available to the API, the screener and the demo script at once."""

from .base import BaseStrategy
from .donchian_breakout import DonchianBreakout
from .moving_average_cross import MovingAverageCross
from .relative_strength import RelativeStrengthStrategy
from .vcp_breakout import VCPBreakoutStrategy

#: Wire-format strategy id -> class. Keys are what `/api/v1/backtest`
#: accepts in its `strategy` field.
REGISTRY: dict[str, type[BaseStrategy]] = {
    "donchian_breakout": DonchianBreakout,
    "moving_average_cross": MovingAverageCross,
    "vcp_breakout": VCPBreakoutStrategy,
    "relative_strength": RelativeStrengthStrategy,
}

#: Strategy ids that require a benchmark frame wired in via `set_benchmark`
#: before `generate_signals` can run - `build_strategy`'s generic,
#: JSON-params-only instantiation has no way to supply one itself.
REQUIRES_BENCHMARK: frozenset[str] = frozenset({"relative_strength"})


def build_strategy(name: str, params: dict | None = None) -> BaseStrategy:
    """Instantiate a registered strategy by id.

    Args:
        name: A key of `REGISTRY`.
        params: Constructor keyword arguments, e.g. `{"breakout_period": 30}`.

    Raises:
        ValueError: if `name` is unregistered, or `params` carries a keyword
            the strategy does not accept. Both are surfaced as ValueError so
            the API layer can turn them into one 422 rather than leaking a
            TypeError as a 500.
    """
    if name not in REGISTRY:
        known = ", ".join(sorted(REGISTRY))
        raise ValueError(f"Unknown strategy {name!r}. Available: {known}")

    try:
        return REGISTRY[name](**(params or {}))
    except TypeError as exc:
        raise ValueError(f"Invalid params for strategy {name!r}: {exc}") from exc


__all__ = [
    "BaseStrategy",
    "DonchianBreakout",
    "MovingAverageCross",
    "RelativeStrengthStrategy",
    "VCPBreakoutStrategy",
    "REGISTRY",
    "REQUIRES_BENCHMARK",
    "build_strategy",
]
