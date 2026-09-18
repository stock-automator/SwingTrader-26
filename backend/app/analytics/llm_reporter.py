"""
LLM performance reporter.

Ingests a raw performance-metrics dict (from `analytics/metrics.py`) and a
trade log, and asks an LLM to write a plain-English performance report:
overall performance, major drawdown periods, and risk factors.

Defaults to a local Ollama model (matching the pattern already used in
`src/agent.py` for VPN selection) so no API key or external cost is
required. `provider="anthropic"` / `"openai"` are stubbed for future use.
"""

from typing import Optional

import pandas as pd
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "phi"
OLLAMA_TIMEOUT = 30

SUPPORTED_PROVIDERS = {"ollama", "anthropic", "openai"}


class LLMReporter:
    """Generates a plain-English performance report via an LLM.

    Args:
        provider: One of `SUPPORTED_PROVIDERS`. Only `"ollama"` is
            implemented today.
        model: Model name passed to the provider.
        ollama_url: Ollama's generate endpoint.
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        provider: str = "ollama",
        model: str = OLLAMA_MODEL,
        ollama_url: str = OLLAMA_URL,
        timeout: int = OLLAMA_TIMEOUT,
    ):
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unknown provider: {provider!r}. Choose from {SUPPORTED_PROVIDERS}"
            )

        self.provider = provider
        self.model = model
        self.ollama_url = ollama_url
        self.timeout = timeout

    def generate_report(
        self,
        metrics: dict,
        trades_df: pd.DataFrame,
        equity_curve: Optional[pd.Series] = None,
    ) -> str:
        """Build a prompt from `metrics`/`trades_df`/`equity_curve` and ask
        the configured LLM for a plain-English report.

        Returns a human-readable error string (never raises) if the LLM
        backend is unreachable, so a report-generation failure doesn't
        crash an automated pipeline.
        """
        prompt = self._build_prompt(metrics, trades_df, equity_curve)

        if self.provider == "ollama":
            return self._call_ollama(prompt)
        if self.provider == "anthropic":
            return self._call_anthropic(prompt)
        return self._call_openai(prompt)

    def _build_prompt(
        self,
        metrics: dict,
        trades_df: pd.DataFrame,
        equity_curve: Optional[pd.Series],
    ) -> str:
        lines = [
            "You are a quantitative trading performance analyst. Write a clear, "
            "plain-English report for a swing trading strategy's backtest/forward-test "
            "results below. Cover: (1) overall performance summary, (2) major drawdown "
            "periods and what they mean for the trader, (3) key risk factors to watch. "
            "Be concise and avoid jargon where possible.",
            "",
            "## Performance Metrics",
        ]
        for key, value in metrics.items():
            lines.append(f"- {key}: {value}")

        drawdowns = (
            _extract_drawdown_periods(equity_curve) if equity_curve is not None else []
        )
        lines.append("")
        lines.append("## Major Drawdown Periods (>5%)")
        if drawdowns:
            for dd in drawdowns:
                lines.append(
                    f"- {dd['start']} to {dd['end']}: trough {dd['depth_pct']:.1f}% on {dd['trough_time']}"
                )
        else:
            lines.append("- None observed above the 5% threshold.")

        lines.append("")
        lines.append("## Trade Log Summary")
        if len(trades_df) > 0 and "pnl" in trades_df.columns:
            streak = _max_consecutive_losses(trades_df)
            lines.append(f"- Total trades: {len(trades_df)}")
            lines.append(f"- Longest losing streak: {streak}")
        else:
            lines.append("- No closed trades available.")

        return "\n".join(lines)

    def _call_ollama(self, prompt: str) -> str:
        try:
            response = requests.post(
                self.ollama_url,
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            return f"LLM report unavailable: could not reach Ollama at {self.ollama_url} ({exc})"

        if response.status_code != 200:
            return (
                f"LLM report unavailable: Ollama returned HTTP {response.status_code}"
            )

        return response.json().get("response", "").strip()

    def _call_anthropic(self, prompt: str) -> str:
        raise NotImplementedError(
            "Anthropic provider not wired up yet. Use provider='ollama', or install the "
            "`anthropic` SDK and implement this method."
        )

    def _call_openai(self, prompt: str) -> str:
        raise NotImplementedError(
            "OpenAI provider not wired up yet. Use provider='ollama', or install the "
            "`openai` SDK and implement this method."
        )


def _extract_drawdown_periods(
    equity_curve: pd.Series, threshold_pct: float = 5.0
) -> list[dict]:
    """Peak-to-recovery drawdown periods deeper than `threshold_pct`."""
    if equity_curve is None or len(equity_curve) < 2:
        return []

    running_max = equity_curve.cummax()
    drawdown_pct = (equity_curve - running_max) / running_max * 100

    periods = []
    in_drawdown = False
    start = trough_time = None
    trough = 0.0

    for timestamp, dd in drawdown_pct.items():
        if dd < 0 and not in_drawdown:
            in_drawdown = True
            start = timestamp
            trough = dd
            trough_time = timestamp
        elif dd < 0 and in_drawdown:
            if dd < trough:
                trough = dd
                trough_time = timestamp
        elif dd >= 0 and in_drawdown:
            in_drawdown = False
            if abs(trough) >= threshold_pct:
                periods.append(
                    {
                        "start": start,
                        "trough_time": trough_time,
                        "end": timestamp,
                        "depth_pct": trough,
                    }
                )

    if in_drawdown and abs(trough) >= threshold_pct:
        periods.append(
            {
                "start": start,
                "trough_time": trough_time,
                "end": equity_curve.index[-1],
                "depth_pct": trough,
            }
        )

    return periods


def _max_consecutive_losses(trades_df: pd.DataFrame) -> int:
    """Longest run of consecutive losing trades, in trade order."""
    is_loss = (trades_df["pnl"] < 0).to_numpy()

    max_streak = current_streak = 0
    for loss in is_loss:
        current_streak = current_streak + 1 if loss else 0
        max_streak = max(max_streak, current_streak)

    return max_streak
