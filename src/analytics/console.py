"""
Terminal output formatting for backtest/forward-test CLI scripts.

Pure string formatting - no external dependency (`tabulate`, `rich`, etc.) -
so output renders identically in any terminal. Consumed by the root
`backtest_*.py` scripts and available to any future CLI entry point.
"""

from typing import Optional, Sequence

METRIC_LABELS = {
    "total_trades": "Total Trades",
    "win_rate": "Win Rate",
    "expectancy": "Expectancy ($)",
    "profit_factor": "Profit Factor",
    "sharpe_ratio": "Sharpe Ratio",
    "sortino_ratio": "Sortino Ratio",
    "max_drawdown_pct": "Max Drawdown (%)",
    "cagr_pct": "CAGR (%)",
}


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    title: Optional[str] = None,
) -> str:
    """Render `headers`/`rows` (already-stringified cells) as an aligned ASCII table."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: Sequence[str]) -> str:
        padded = " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))
        return f"| {padded} |"

    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    lines = []
    if title:
        lines.append(title.center(len(sep)))
    lines.append(sep)
    lines.append(fmt_row(headers))
    lines.append(sep)
    for row in rows:
        lines.append(fmt_row(row))
    lines.append(sep)
    return "\n".join(lines)


def _format_metric_value(key: str, value) -> str:
    if value is None:
        return "N/A"
    if key == "win_rate":
        return f"{value:.1%}"
    if key in ("max_drawdown_pct", "cagr_pct"):
        return f"{value:+.2f}%"
    if key == "expectancy":
        return f"{value:+.2f}"
    if key == "profit_factor":
        return "inf" if value == float("inf") else f"{value:.2f}"
    if key in ("sharpe_ratio", "sortino_ratio"):
        return f"{value:.2f}"
    if key == "total_trades":
        return str(int(value))
    return str(value)


def format_metrics_table(metrics: dict, title: str = "PERFORMANCE METRICS") -> str:
    """Render a `compute_metrics()` dict as an aligned two-column ASCII table."""
    rows = [
        [METRIC_LABELS[key], _format_metric_value(key, metrics[key])]
        for key in METRIC_LABELS
        if key in metrics
    ]
    return render_table(["Metric", "Value"], rows, title=title)
