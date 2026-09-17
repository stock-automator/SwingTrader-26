"""
Tests for analytics/llm_reporter.py

The Ollama HTTP call is mocked throughout - these tests never require a
live Ollama server.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analytics.llm_reporter import (
    LLMReporter,
    _extract_drawdown_periods,
    _max_consecutive_losses,
)


@pytest.fixture
def sample_metrics():
    return {
        "total_trades": 10,
        "win_rate": 0.6,
        "sharpe_ratio": 1.2,
        "max_drawdown_pct": -12.5,
    }


@pytest.fixture
def sample_trades():
    return pd.DataFrame({"pnl": [10, -5, 20, -8, -3]})


class TestConstruction:
    def test_rejects_unknown_provider(self):
        with pytest.raises(ValueError):
            LLMReporter(provider="not-a-real-provider")


class TestGenerateReportOllama:
    @patch("src.analytics.llm_reporter.requests.post")
    def test_successful_call_returns_response_text(
        self, mock_post, sample_metrics, sample_trades
    ):
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"response": "Solid strategy."}
        )

        reporter = LLMReporter(provider="ollama")
        report = reporter.generate_report(sample_metrics, sample_trades)

        assert report == "Solid strategy."
        # Prompt sent to Ollama should embed the metrics we gave it.
        sent_prompt = mock_post.call_args.kwargs["json"]["prompt"]
        assert "sharpe_ratio" in sent_prompt
        assert "1.2" in sent_prompt

    @patch("src.analytics.llm_reporter.requests.post")
    def test_connection_error_returns_friendly_message(
        self, mock_post, sample_metrics, sample_trades
    ):
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")

        reporter = LLMReporter(provider="ollama")
        report = reporter.generate_report(sample_metrics, sample_trades)

        assert "unavailable" in report.lower()

    @patch("src.analytics.llm_reporter.requests.post")
    def test_non_200_status_returns_friendly_message(
        self, mock_post, sample_metrics, sample_trades
    ):
        mock_post.return_value = MagicMock(status_code=500)

        reporter = LLMReporter(provider="ollama")
        report = reporter.generate_report(sample_metrics, sample_trades)

        assert "unavailable" in report.lower()

    @patch("src.analytics.llm_reporter.requests.post")
    def test_includes_drawdown_periods_in_prompt(
        self, mock_post, sample_metrics, sample_trades
    ):
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"response": "ok"}
        )

        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        equity_curve = pd.Series([100.0, 100.0, 70.0, 70.0, 100.0], index=dates)

        reporter = LLMReporter(provider="ollama")
        reporter.generate_report(
            sample_metrics, sample_trades, equity_curve=equity_curve
        )

        sent_prompt = mock_post.call_args.kwargs["json"]["prompt"]
        assert "Major Drawdown Periods" in sent_prompt
        assert "trough" in sent_prompt


class TestUnimplementedProviders:
    def test_anthropic_raises_not_implemented(self, sample_metrics, sample_trades):
        reporter = LLMReporter(provider="anthropic")
        with pytest.raises(NotImplementedError):
            reporter.generate_report(sample_metrics, sample_trades)

    def test_openai_raises_not_implemented(self, sample_metrics, sample_trades):
        reporter = LLMReporter(provider="openai")
        with pytest.raises(NotImplementedError):
            reporter.generate_report(sample_metrics, sample_trades)


class TestExtractDrawdownPeriods:
    def test_no_drawdown_below_threshold(self):
        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        equity = pd.Series([100.0, 101.0, 99.0, 100.0, 102.0], index=dates)
        assert _extract_drawdown_periods(equity, threshold_pct=5.0) == []

    def test_detects_drawdown_above_threshold(self):
        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        equity = pd.Series([100.0, 100.0, 70.0, 70.0, 100.0], index=dates)
        periods = _extract_drawdown_periods(equity, threshold_pct=5.0)

        assert len(periods) == 1
        assert periods[0]["depth_pct"] == pytest.approx(-30.0)

    def test_ongoing_drawdown_at_series_end_is_captured(self):
        dates = pd.date_range("2024-01-01", periods=4, freq="D")
        equity = pd.Series([100.0, 100.0, 60.0, 55.0], index=dates)
        periods = _extract_drawdown_periods(equity, threshold_pct=5.0)

        assert len(periods) == 1
        assert periods[0]["depth_pct"] == pytest.approx(-45.0)


class TestMaxConsecutiveLosses:
    def test_counts_longest_streak(self):
        trades_df = pd.DataFrame({"pnl": [10, -1, -2, -3, 5, -1]})
        assert _max_consecutive_losses(trades_df) == 3

    def test_no_losses(self):
        trades_df = pd.DataFrame({"pnl": [10, 20]})
        assert _max_consecutive_losses(trades_df) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
