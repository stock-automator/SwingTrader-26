#!/usr/bin/env python3
"""
Reproducible baseline backtest (batch 02 F2).

Runs the pinned strategy, tickers and window in `config/baseline_backtest.json`
over cached bars only (no downloads), and prints the result as JSON. The same
config and data always give the same output, so a change in the numbers means
the code or the data changed.

    python scripts/run_baseline.py [path/to/config.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.app.quant.backtest import (  # noqa: E402
    comparison_to_payload,
    run_comparison,
)
from backend.app.quant.strategies import (  # noqa: E402
    REQUIRES_BENCHMARK,
    build_strategy,
)

CONFIG_PATH = ROOT / "config" / "baseline_backtest.json"

#: Payload keys kept in the baseline record (curves and trade rows are left out
#: so the output stays readable and diffable).
BASELINE_KEYS = (
    "strategy",
    "tickers",
    "start",
    "end",
    "headline",
    "summaries",
    "vs_spy",
    "vs_buy_and_hold",
    "trade_metrics",
    "skipped_signals",
    "warnings",
)


def run_baseline(
    config: dict, frames: dict[str, pd.DataFrame], spy_frame: pd.DataFrame | None
) -> dict:
    """Run the baseline described by `config` over `frames` (and SPY), with
    the same defaults and cost model as `POST /api/v1/backtest`: missing
    keys take the API request's defaults, and the FX cost per leg comes
    from settings unless the config sets `fx_cost_pct`."""
    import backend.app.config as app_config
    from backend.app.api.schemas import BacktestRequest

    def get(key: str):
        value = config.get(key)
        return BacktestRequest.model_fields[key].default if value is None else value

    strategy = build_strategy(config["strategy"], config.get("strategy_params"))
    if config["strategy"] in REQUIRES_BENCHMARK and spy_frame is not None:
        strategy.set_benchmark(spy_frame)

    settings = app_config.get_settings()
    fx_cost_pct = get("fx_cost_pct")
    if fx_cost_pct is None:
        fx_cost_pct = settings.fx_cost_pct * settings.fx_conversions_per_round_trip / 2

    comparison = run_comparison(
        strategy,
        frames,
        spy_frame=spy_frame,
        initial_capital=float(get("initial_capital")),
        risk_per_trade_pct=float(get("risk_per_trade_pct")),
        commission=float(get("commission")),
        slippage_pct=float(get("slippage_pct")),
        risk_free_rate=float(get("risk_free_rate")),
        include_buy_and_hold=bool(get("include_buy_and_hold")),
        fee_per_share=float(get("fee_per_share")),
        atr_slippage_multiple=float(get("atr_slippage_multiple")),
        fx_cost_pct=float(fx_cost_pct),
        fractional=bool(get("fractional")),
        use_risk_gate=bool(get("use_risk_gate")),
    )
    payload = comparison_to_payload(comparison)
    record = {key: payload[key] for key in BASELINE_KEYS}
    record["config"] = config
    record["trade_count"] = len(payload["trades"])
    # Round-trip through JSON so numpy scalars can't leak out.
    return json.loads(json.dumps(record, default=float))


def main(config_path: Path = CONFIG_PATH) -> dict:
    from backend.app.config import get_settings
    from backend.app.data.loader import (
        SPY_TICKER,
        DataUnavailableError,
        load_prices,
    )

    config = json.loads(Path(config_path).read_text())
    settings = get_settings()

    def cached(ticker: str) -> pd.DataFrame | None:
        try:
            return load_prices(
                ticker,
                start=config["start"],
                end=config["end"],
                data_dir=settings.data_dir,
                allow_download=False,
            )
        except DataUnavailableError as exc:
            print(f"skipping {ticker}: {exc}", file=sys.stderr)
            return None

    frames = {t: df for t in config["tickers"] if (df := cached(t)) is not None}
    result = run_baseline(config, frames, cached(SPY_TICKER))
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else CONFIG_PATH)
