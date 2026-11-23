"""
Strategy scorecard (cycle 03 step 2).

Each registered strategy runs alone through `portfolio.run_portfolio_backtest`
on the build period (2016 to the split) and the held-out test period (the
split to the last bar) separately, long-only and, when the strategy emits
short entries, long+short. Signals are generated once over full history, so
the test window starts with warmed-up indicators.

Composite score (higher is better): the weighted sum of each criterion's
percentile rank across all rows (`COMPOSITE_WEIGHTS`, summing to 1):

    0.25 return vs exposure-matched SPY (percentage points)
    0.15 profit factor
    0.05 win rate
    0.05 payoff (average win / average loss)
    0.10 max drawdown (shallower is better)
    0.05 trade count (more trades, more confidence)
    0.10 year consistency (% of calendar years with a positive return)
    0.10 regime consistency (% of SPY macro regimes with a positive return)
    0.15 robustness (worst long-only build-period CAGR when each numeric
         parameter moves +/-20%, or +/-1 for small integers)

Win rate and payoff are light because profit factor already combines them.
Every criterion except robustness blends 70% test period with 30% build
period. Robustness uses the build period (2016-2022) only, so no test data
feeds parameter sensitivity. Rows flagged "too few trades" are halved and
sort below every other row; "test much worse than build" is a flag only.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import replace

import numpy as np
import pandas as pd

from .backtest import MIN_TRADES_FOR_CONFIDENCE, _finite, exposure_matched_curve
from .metrics import compute_metrics
from .portfolio import (
    PortfolioConfig,
    PortfolioResult,
    SignalMatrix,
    macro_regime,
    prepare_signals,
    run_portfolio_backtest,
)
from .strategies import REGISTRY, REQUIRES_BENCHMARK
from .strategies.base import BaseStrategy

BUILD_START = "2016-01-01"
SPLIT_DATE = "2023-01-01"
PERTURBATION = 0.20

FLAG_FEW_TRADES = "too few trades"
FLAG_TEST_WORSE = "test much worse than build"
FLAG_SHORT_BORROW = "short-side unmodelled borrow"

#: Criterion -> weight. All but "robustness" are period-stat keys blended
#: `TEST_SHARE` test / rest build; see the module docstring.
COMPOSITE_WEIGHTS = {
    "vs_spy_pct": 0.25,
    "profit_factor": 0.15,
    "win_rate_pct": 0.05,
    "payoff": 0.05,
    "max_drawdown_pct": 0.10,
    "trades": 0.05,
    "positive_years_pct": 0.10,
    "positive_regimes_pct": 0.10,
    "robustness": 0.15,
}
TEST_SHARE = 0.7


def _round(value, digits: int = 6):
    value = _finite(value)
    return None if value is None else round(value, digits)


def trade_stats(trades: pd.DataFrame) -> dict:
    """Trades, win rate, profit factor and payoff (avg win / avg loss).
    Undefined ratios (no losers, no trades) are None."""
    pnl = trades["pnl"] if "pnl" in trades else pd.Series(dtype=float)
    m = compute_metrics(pd.DataFrame({"pnl": pnl}))
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    payoff = (
        float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None
    )
    avg_r = (
        float(trades["r_multiple"].mean())
        if "r_multiple" in trades and len(trades)
        else None
    )
    return {
        "trades": int(len(pnl)),
        "win_rate_pct": _round(m["win_rate"] * 100),
        "profit_factor": _round(m["profit_factor"]) if len(losses) else None,
        "payoff": _round(payoff),
        "avg_r": _round(avg_r, 3),
    }


def curve_stats(
    equity: pd.Series,
    spy_close: pd.Series,
    invested: pd.Series,
    regime: pd.Series | None = None,
) -> dict:
    """Return, CAGR, drawdown, return vs exposure-matched SPY, per-year and
    per-regime returns of one equity curve. Each day's return is grouped by
    the regime at the prior close (what a trader knew)."""
    m = compute_metrics(pd.DataFrame({"pnl": []}), equity_curve=equity)
    first = float(equity.iloc[0])
    total = (float(equity.iloc[-1]) / first - 1) * 100
    spy = spy_close.reindex(equity.index).ffill().bfill()
    matched = exposure_matched_curve(spy, invested.reindex(equity.index), first)
    matched_total = (float(matched.iloc[-1]) / first - 1) * 100

    year_end = equity.groupby(equity.index.year).last()
    base = year_end.shift(1).fillna(first)
    yearly = (year_end / base - 1) * 100

    regime_returns: dict[str, float] = {}
    positive_regimes = None
    if regime is not None:
        daily = equity.pct_change().iloc[1:]
        prior = regime.reindex(equity.index).shift(1).iloc[1:].fillna("UNKNOWN")
        grouped = (1 + daily).groupby(prior).prod() - 1
        regime_returns = {str(k): _round(v * 100) for k, v in grouped.items()}
        known = grouped.drop("UNKNOWN", errors="ignore")
        if len(known):
            positive_regimes = (known > 0).mean() * 100

    return {
        "start": equity.index[0].date().isoformat(),
        "end": equity.index[-1].date().isoformat(),
        "total_return_pct": _round(total),
        "cagr_pct": _round(m["cagr_pct"]),
        "max_drawdown_pct": _round(m["max_drawdown_pct"]),
        "spy_matched_return_pct": _round(matched_total),
        "vs_spy_pct": _round(total - matched_total),
        "yearly_returns_pct": {int(y): _round(v) for y, v in yearly.items()},
        "positive_years_pct": _round((yearly > 0).mean() * 100),
        "regime_returns_pct": regime_returns,
        "positive_regimes_pct": _round(positive_regimes),
    }


def period_stats(result: PortfolioResult, spy: pd.DataFrame, regime) -> dict:
    trades = result.trades
    stats = curve_stats(result.equity, spy["Close"], result.invested, regime)
    stats.update(trade_stats(trades))
    stats["short_trades"] = int((trades["side"] == "short").sum()) if len(trades) else 0
    stats["exposure_pct"] = _round(result.exposure_pct)
    stats["avg_invested_pct"] = _round(result.avg_invested_pct)
    stats["skipped"] = dict(result.skipped)
    stats["flags"] = [f for f in result.flags if not f.startswith("suspect data")]
    if len(trades):
        stats["exit_reasons"] = trades["exit_reason"].value_counts().to_dict()
        stats["avg_holding_days"] = _round(trades["holding_days"].mean(), 1)
    return stats


def score_flags(build: dict, test: dict, short_trades: int = 0) -> list[str]:
    flags = []
    if min(build.get("trades", 0), test.get("trades", 0)) < MIN_TRADES_FOR_CONFIDENCE:
        flags.append(FLAG_FEW_TRADES)
    b, t = build.get("cagr_pct"), test.get("cagr_pct")
    if b is not None and t is not None:
        drop = b - t
        if drop > 5 and drop > 0.5 * abs(b):
            flags.append(FLAG_TEST_WORSE)
    if short_trades > 0:
        flags.append(FLAG_SHORT_BORROW)
    return flags


def numeric_params(cls: type) -> dict[str, int | float]:
    """Constructor parameters with an int/float default (bools excluded)."""
    out = {}
    for name, p in inspect.signature(cls.__init__).parameters.items():
        d = p.default
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            out[name] = d
    return out


def param_variants(name: str, cls: type | None = None) -> list[dict]:
    """Each numeric param at -20% and +20%. Ints round and stay >= 1; an int
    whose +/-20% rounds back to its default (0, 2) steps by -1 and +1."""
    cls = cls or REGISTRY[name]
    variants = []
    for param, default in numeric_params(cls).items():
        values = [default * (1 - PERTURBATION), default * (1 + PERTURBATION)]
        if isinstance(default, int):
            values = [int(round(v)) for v in values]
            if default in values:
                values = [default - 1, default + 1]
            else:
                values = [max(1, v) for v in values]
        variants += [{"param": param, "value": v} for v in values]
    return variants


def robustness_window(start=BUILD_START, split_date: str = SPLIT_DATE) -> tuple:
    """The build period, where robustness reruns live: [start, split)."""
    return start, pd.Timestamp(split_date) - pd.Timedelta(days=1)


def make_strategy(
    name: str, spy: pd.DataFrame, params: dict | None = None
) -> BaseStrategy:
    strategy = REGISTRY[name](**(params or {}))
    if name in REQUIRES_BENCHMARK:
        strategy.set_benchmark(spy)
    return strategy


def full_window_cagr(
    strategy: BaseStrategy,
    frames: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    config: PortfolioConfig,
    start=BUILD_START,
    end=None,
    regime: pd.Series | None = None,
    prepared: SignalMatrix | None = None,
) -> float | None:
    """Long-only CAGR over [start, end] - the robustness yardstick."""
    result = run_portfolio_backtest(
        strategy,
        frames,
        spy,
        replace(config, allow_shorts=False),
        start=start,
        end=end,
        regime=regime,
        prepared=prepared,
    )
    return curve_stats(result.equity, spy["Close"], result.invested)["cagr_pct"]


def perturbation_run(
    name: str,
    param: str,
    value,
    frames: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    config: PortfolioConfig,
    start=BUILD_START,
    end=None,
) -> dict:
    """One robustness rerun. A value the strategy rejects is reported, not
    raised."""
    try:
        strategy = make_strategy(name, spy, {param: value})
        cagr = full_window_cagr(strategy, frames, spy, config, start, end)
        return {"param": param, "value": value, "cagr_pct": cagr}
    except (ValueError, TypeError) as exc:
        return {"param": param, "value": value, "cagr_pct": None, "error": str(exc)}


def summarize_robustness(base_cagr: float | None, runs: list[dict]) -> dict | None:
    if not runs:
        return None
    cagrs = [r["cagr_pct"] for r in runs if r.get("cagr_pct") is not None]
    if base_cagr is not None:
        cagrs.append(base_cagr)
    if not cagrs:
        return {"runs": runs, "worst_cagr_pct": None}
    return {
        "base_cagr_pct": base_cagr,
        "worst_cagr_pct": _round(min(cagrs)),
        "best_cagr_pct": _round(max(cagrs)),
        "spread_pct": _round(max(cagrs) - min(cagrs)),
        "profitable_pct": _round(np.mean([c > 0 for c in cagrs]) * 100),
        "runs": runs,
    }


def score_strategy(
    name: str,
    frames: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    config: PortfolioConfig | None = None,
    split_date: str = SPLIT_DATE,
    strategy: BaseStrategy | None = None,
    robustness: bool = True,
    start=BUILD_START,
    end=None,
) -> dict:
    """Build and test-period stats for the long-only and (when the strategy
    emits shorts) long+short variants, flags, and the robustness spread.

    `strategy` overrides the registry instance (its class's numeric params
    still drive robustness)."""
    config = config or PortfolioConfig()
    strategy = strategy or make_strategy(name, spy)
    regime = macro_regime(spy)
    prepared = prepare_signals(strategy, frames, spy.index)
    split = pd.Timestamp(split_date)
    in_window = prepared.index >= pd.Timestamp(start)
    if end is not None:
        in_window &= prepared.index <= pd.Timestamp(end)
    has_shorts = bool((prepared.signal[in_window] == -1).any())

    variants: dict[str, dict | None] = {"long": None, "long_short": None}
    for variant, shorts in (("long", False), ("long_short", True)):
        if shorts and not has_shorts:
            continue
        cfg = replace(config, allow_shorts=shorts)

        def run(lo, hi):
            return run_portfolio_backtest(
                strategy, frames, spy, cfg, lo, hi, regime=regime, prepared=prepared
            )

        build = period_stats(run(start, split - pd.Timedelta(days=1)), spy, regime)
        test = period_stats(run(split, end), spy, regime)
        shorts_taken = build["short_trades"] + test["short_trades"]
        flags = score_flags(build, test, shorts_taken)
        flags += [f for f in build["flags"] + test["flags"] if f not in flags]
        variants[variant] = {"build": build, "test": test, "flags": flags}

    rob_start, rob_end = robustness_window(start, split_date)
    base_cagr = full_window_cagr(
        strategy,
        frames,
        spy,
        config,
        rob_start,
        rob_end,
        regime=regime,
        prepared=prepared,
    )
    runs = []
    if robustness and name in REGISTRY:
        runs = [
            perturbation_run(
                name, v["param"], v["value"], frames, spy, config, rob_start, rob_end
            )
            for v in param_variants(name, type(strategy))
        ]
    return {
        "strategy": name,
        "display_name": strategy.name,
        "params": numeric_params(type(strategy)),
        "build_cagr_pct": base_cagr,
        "variants": variants,
        "signal_errors": prepared.errors,
        "suspect_data": list(prepared.suspect),
        "robustness": summarize_robustness(base_cagr, runs) if runs else None,
    }


def scorecard_rows(scores: list[dict]) -> list[dict]:
    """One row per (strategy, variant run), ranked by `composite_rank`."""
    rows = []
    for s in scores:
        worst = (s.get("robustness") or {}).get("worst_cagr_pct")
        for variant, v in s["variants"].items():
            if v is None:
                continue
            rows.append(
                {
                    "strategy": s["strategy"],
                    "variant": variant,
                    "build": v["build"],
                    "test": v["test"],
                    "robustness_worst_cagr_pct": worst,
                    "flags": v["flags"],
                }
            )
    return composite_rank(rows)


def _pct_rank(values: list) -> pd.Series:
    series = pd.Series([np.nan if v is None else v for v in values], dtype=float)
    return series.rank(pct=True).fillna(0.0)


def composite_rank(rows: list[dict]) -> list[dict]:
    """Score rows (see module docstring) and return them best first, each
    with `score` and `rank`."""
    if not rows:
        return []
    score = pd.Series(0.0, index=range(len(rows)))
    for key, weight in COMPOSITE_WEIGHTS.items():
        if key == "robustness":
            values = [r.get("robustness_worst_cagr_pct") for r in rows]
            score += weight * _pct_rank(values)
            continue
        test = _pct_rank([r["test"].get(key) for r in rows])
        build = _pct_rank([r["build"].get(key) for r in rows])
        score += weight * (TEST_SHARE * test + (1 - TEST_SHARE) * build)
    ranked = []
    for i, r in enumerate(rows):
        few = FLAG_FEW_TRADES in r.get("flags", [])
        value = float(score[i]) * (0.5 if few else 1.0)
        ranked.append({**r, "score": round(value, 4), "_few": few})
    ranked.sort(key=lambda r: (r["_few"], -r["score"], r["strategy"], r["variant"]))
    for i, r in enumerate(ranked, start=1):
        r.pop("_few")
        r["rank"] = i
    return ranked


def _fmt(value, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.{digits}f}"


def scorecard_markdown(rows: list[dict], scores: list[dict]) -> str:
    """Ranked table plus per-strategy notes."""
    lines = [
        "| Rank | Strategy | Variant | Score | Test return % "
        "| Test exp-matched SPY % | Test vs SPY pp | Test CAGR % "
        "| Test PF | Test win % | Test payoff | Test max DD % | Test trades "
        "| Build CAGR % | Build vs SPY pp | Build trades "
        "| Worst ±20% build CAGR % | Flags |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:"
        "|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        t, b = r["test"], r["build"]
        lines.append(
            f"| {r['rank']} | {r['strategy']} | {r['variant']} | {r['score']:.3f} "
            f"| {_fmt(t['total_return_pct'])} | {_fmt(t['spy_matched_return_pct'])} "
            f"| {_fmt(t['vs_spy_pct'])} | {_fmt(t['cagr_pct'])} "
            f"| {_fmt(t['profit_factor'], 2)} | {_fmt(t['win_rate_pct'])} "
            f"| {_fmt(t['payoff'], 2)} | {_fmt(t['max_drawdown_pct'])} "
            f"| {t['trades']} | {_fmt(b['cagr_pct'])} | {_fmt(b['vs_spy_pct'])} "
            f"| {b['trades']} | {_fmt(r['robustness_worst_cagr_pct'])} "
            f"| {', '.join(r['flags']) or '-'} |"
        )
    lines.append("")
    for s in sorted(scores, key=lambda s: s["strategy"]):
        lines.append(f"### {s['strategy']} ({s['display_name']})")
        lines.append("")
        for variant, v in s["variants"].items():
            if v is None:
                lines.append(
                    f"- {variant}: not run separately - the strategy emits no "
                    "short entries, so it equals long-only."
                )
                continue
            for period in ("build", "test"):
                p = v[period]
                years = ", ".join(
                    f"{y}: {_fmt(x)}%" for y, x in p["yearly_returns_pct"].items()
                )
                regimes = ", ".join(
                    f"{k}: {_fmt(x)}%"
                    for k, x in sorted(p["regime_returns_pct"].items())
                )
                skipped = ", ".join(
                    f"{k} {n}"
                    for k, n in sorted(p["skipped"].items(), key=lambda kv: -kv[1])
                )
                lines.append(
                    f"- {variant} {period} ({p['start']} to {p['end']}): return "
                    f"{_fmt(p['total_return_pct'])}% vs exposure-matched SPY "
                    f"{_fmt(p['spy_matched_return_pct'])}%, {p['trades']} trades "
                    f"({p['short_trades']} short), avg R {_fmt(p['avg_r'], 2)}, "
                    f"invested {_fmt(p['avg_invested_pct'])}% on average, "
                    f"exposure {_fmt(p['exposure_pct'])}% of days."
                )
                lines.append(f"  - Years: {years or 'n/a'}")
                lines.append(f"  - By regime (prior close): {regimes or 'n/a'}")
                lines.append(f"  - Skipped/capped signals: {skipped or 'none'}")
        rob = s.get("robustness")
        if rob:
            lines.append(
                f"- Robustness (long-only, build period, each param ±20% or ±1, "
                f"{len(rob['runs'])} reruns): CAGR {_fmt(rob['worst_cagr_pct'])}% "
                f"to {_fmt(rob['best_cagr_pct'])}% (spread "
                f"{_fmt(rob['spread_pct'])} pp), {_fmt(rob['profitable_pct'], 0)}% "
                "of runs profitable."
            )
        if s.get("signal_errors"):
            lines.append(
                f"- Signal errors (tickers dropped): {', '.join(sorted(s['signal_errors']))}"
            )
        lines.append("")
    return "\n".join(lines)


def build_scorecard(
    frames: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    config: PortfolioConfig | None = None,
    split_date: str = SPLIT_DATE,
    strategies: list[str] | None = None,
    robustness: bool = True,
) -> dict:
    """Score every registered strategy serially; `scripts/run_scorecard.py`
    runs the same pieces in parallel."""
    scores = [
        score_strategy(name, frames, spy, config, split_date, robustness=robustness)
        for name in (strategies or list(REGISTRY))
    ]
    rows = scorecard_rows(scores)
    return {
        "rows": rows,
        "strategies": scores,
        "markdown": scorecard_markdown(rows, scores),
    }
