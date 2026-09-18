import pytest

from backend.app.analytics.console import format_metrics_table, render_table


class TestRenderTable:
    def test_aligns_columns_to_widest_cell(self):
        table = render_table(
            ["Ticker", "Return"], [["AAPL", "+1.2%"], ["MSFT", "-0.4%"]]
        )
        lines = table.splitlines()
        widths = {len(line) for line in lines}
        assert len(widths) == 1  # every line (borders + rows) is the same width

    def test_includes_title_when_given(self):
        table = render_table(["A"], [["1"]], title="SUMMARY")
        assert "SUMMARY" in table.splitlines()[0]

    def test_no_title_by_default(self):
        table = render_table(["A"], [["1"]])
        assert "SUMMARY" not in table


class TestFormatMetricsTable:
    def test_formats_known_metric_keys(self):
        metrics = {
            "total_trades": 42,
            "win_rate": 0.55,
            "expectancy": 12.5,
            "profit_factor": 1.8,
            "sharpe_ratio": 1.23,
            "sortino_ratio": 1.75,
            "max_drawdown_pct": -8.4,
            "cagr_pct": 15.2,
        }
        table = format_metrics_table(metrics)
        assert "42" in table
        assert "55.0%" in table
        assert "1.80" in table
        assert "-8.40%" in table

    def test_infinite_profit_factor_renders_as_inf(self):
        table = format_metrics_table({"profit_factor": float("inf")})
        assert "inf" in table

    def test_none_cagr_renders_as_na(self):
        table = format_metrics_table({"cagr_pct": None})
        assert "N/A" in table

    def test_ignores_unknown_keys(self):
        table = format_metrics_table({"total_trades": 1, "not_a_real_metric": 999})
        assert "999" not in table

    def test_custom_title_is_centered_in_header(self):
        table = format_metrics_table({"total_trades": 1}, title="AAPL RESULTS")
        assert "AAPL RESULTS" in table.splitlines()[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
