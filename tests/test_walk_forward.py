"""
Tests for quant/walk_forward.py: rolling IS/OOS windowing, Walk-Forward
Efficiency, and parameter-sensitivity ("cliff") detection.
"""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.strategies.moving_average_cross import MovingAverageCross
from backend.app.quant.walk_forward import (
    ParameterSensitivityPoint,
    ParameterSensitivityResult,
    WalkForwardWindow,
    analyze_parameter_sensitivity,
    generate_windows,
    run_walk_forward,
)


def _trending_ohlcv(n: int = 900, seed: int = 5) -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    close = 100 + 0.05 * np.arange(n) + rng.normal(0, 1, n).cumsum() * 0.05
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=dates,
    )


class TestGenerateWindows:
    def test_rejects_non_positive_is_months(self):
        idx = pd.date_range("2020-01-01", periods=500, freq="B")
        with pytest.raises(ValueError, match="is_months"):
            generate_windows(idx, is_months=0, oos_months=3)

    def test_rejects_non_positive_oos_months(self):
        idx = pd.date_range("2020-01-01", periods=500, freq="B")
        with pytest.raises(ValueError, match="oos_months"):
            generate_windows(idx, is_months=12, oos_months=0)

    def test_rejects_empty_index(self):
        with pytest.raises(ValueError, match="non-empty"):
            generate_windows(pd.DatetimeIndex([]), is_months=12, oos_months=3)

    def test_windows_are_contiguous_by_default(self):
        idx = pd.date_range("2020-01-01", periods=900, freq="B")
        windows = generate_windows(idx, is_months=12, oos_months=3)

        assert len(windows) > 0
        for is_start, is_end, oos_start, oos_end in windows:
            assert is_end == oos_start  # OOS picks up exactly where IS ends
            assert is_start < is_end < oos_end

    def test_step_defaults_to_oos_length(self):
        idx = pd.date_range("2020-01-01", periods=900, freq="B")
        windows = generate_windows(idx, is_months=12, oos_months=3)

        first_is_start = windows[0][0]
        second_is_start = windows[1][0]
        assert second_is_start == first_is_start + pd.DateOffset(months=3)

    def test_custom_step_months(self):
        idx = pd.date_range("2020-01-01", periods=900, freq="B")
        windows = generate_windows(idx, is_months=12, oos_months=3, step_months=1)

        first_is_start = windows[0][0]
        second_is_start = windows[1][0]
        assert second_is_start == first_is_start + pd.DateOffset(months=1)

    def test_no_windows_when_data_too_short(self):
        idx = pd.date_range("2020-01-01", periods=30, freq="B")
        assert generate_windows(idx, is_months=12, oos_months=3) == []

    def test_last_window_never_exceeds_data_end(self):
        idx = pd.date_range("2020-01-01", periods=900, freq="B")
        windows = generate_windows(idx, is_months=12, oos_months=3)
        assert windows[-1][3] <= idx.max()


class TestWalkForwardWindowEfficiency:
    def _window(self, is_return, oos_return) -> WalkForwardWindow:
        return WalkForwardWindow(
            is_start=pd.Timestamp("2020-01-01"),
            is_end=pd.Timestamp("2021-01-01"),
            oos_start=pd.Timestamp("2021-01-01"),
            oos_end=pd.Timestamp("2021-04-01"),
            is_return_pct=is_return,
            oos_return_pct=oos_return,
        )

    def test_efficiency_is_ratio_of_oos_to_is(self):
        window = self._window(10.0, 5.0)
        assert window.efficiency == pytest.approx(0.5)

    def test_efficiency_none_when_is_return_missing(self):
        assert self._window(None, 5.0).efficiency is None

    def test_efficiency_none_when_oos_return_missing(self):
        assert self._window(10.0, None).efficiency is None

    def test_efficiency_none_when_is_return_near_zero(self):
        assert self._window(1e-12, 5.0).efficiency is None

    def test_as_dict_is_json_ready(self):
        payload = self._window(10.0, 5.0).as_dict()
        assert payload["is_start"] == "2020-01-01"
        assert payload["efficiency"] == pytest.approx(0.5)


class TestRunWalkForward:
    def test_produces_one_result_per_window(self):
        df = _trending_ohlcv()
        strategy = MovingAverageCross(fast_period=5, slow_period=15, sl_pct=0.10)

        result = run_walk_forward(strategy, df, is_months=6, oos_months=2)

        expected_windows = generate_windows(df.index, is_months=6, oos_months=2)
        assert len(result.windows) == len(expected_windows)
        assert result.is_window_months == 6
        assert result.oos_window_months == 2

    def test_mean_and_median_efficiency_are_defined_when_windows_have_returns(self):
        df = _trending_ohlcv()
        strategy = MovingAverageCross(fast_period=5, slow_period=15, sl_pct=0.10)

        result = run_walk_forward(strategy, df, is_months=6, oos_months=2)

        assert result.mean_efficiency is not None
        assert result.median_efficiency is not None

    def test_efficiency_ignores_undefined_windows(self):
        df = _trending_ohlcv()
        strategy = MovingAverageCross(fast_period=5, slow_period=15, sl_pct=0.10)
        result = run_walk_forward(strategy, df, is_months=6, oos_months=2)

        defined = [w.efficiency for w in result.windows if w.efficiency is not None]
        if defined:
            assert result.mean_efficiency == pytest.approx(sum(defined) / len(defined))

    def test_no_windows_returns_none_efficiency(self):
        df = _trending_ohlcv(n=30)
        strategy = MovingAverageCross(fast_period=5, slow_period=15)

        result = run_walk_forward(strategy, df, is_months=12, oos_months=3)

        assert result.windows == []
        assert result.mean_efficiency is None
        assert result.median_efficiency is None

    def test_as_dict_is_json_ready(self):
        df = _trending_ohlcv()
        strategy = MovingAverageCross(fast_period=5, slow_period=15, sl_pct=0.10)
        result = run_walk_forward(strategy, df, is_months=6, oos_months=2)

        payload = result.as_dict()
        assert payload["is_window_months"] == 6
        assert isinstance(payload["windows"], list)


class TestParameterSensitivityCliffDetection:
    def _points(self, returns: list[float]) -> list[ParameterSensitivityPoint]:
        return [
            ParameterSensitivityPoint(
                param_value=float(i), return_pct=r, sharpe_ratio=None
            )
            for i, r in enumerate(returns)
        ]

    def test_smooth_gradient_is_not_a_cliff(self):
        result = ParameterSensitivityResult(
            param_name="x",
            baseline_value=10.0,
            points=self._points([10.0, 12.0, 14.0, 16.0, 18.0]),
            cliff_threshold=0.6,
        )
        assert result.is_cliff is False

    def test_one_abrupt_jump_is_a_cliff(self):
        result = ParameterSensitivityResult(
            param_name="x",
            baseline_value=10.0,
            points=self._points([10.0, 10.5, 11.0, -40.0, -39.5]),
            cliff_threshold=0.6,
        )
        assert result.is_cliff is True

    def test_fewer_than_two_defined_points_is_not_a_cliff(self):
        result = ParameterSensitivityResult(
            param_name="x",
            baseline_value=10.0,
            points=self._points([10.0]),
            cliff_threshold=0.6,
        )
        assert result.is_cliff is False

    def test_all_none_returns_is_not_a_cliff(self):
        points = [
            ParameterSensitivityPoint(
                param_value=float(i), return_pct=None, sharpe_ratio=None
            )
            for i in range(5)
        ]
        result = ParameterSensitivityResult(
            param_name="x", baseline_value=10.0, points=points, cliff_threshold=0.6
        )
        assert result.is_cliff is False

    def test_flat_returns_are_not_a_cliff(self):
        result = ParameterSensitivityResult(
            param_name="x",
            baseline_value=10.0,
            points=self._points([5.0, 5.0, 5.0, 5.0]),
            cliff_threshold=0.6,
        )
        assert result.is_cliff is False


class TestAnalyzeParameterSensitivity:
    def test_rejects_empty_perturbations(self):
        df = _trending_ohlcv(n=300)
        with pytest.raises(ValueError, match="non-empty"):
            analyze_parameter_sensitivity(
                lambda v: MovingAverageCross(fast_period=int(round(v)), slow_period=15),
                param_name="fast_period",
                baseline_value=5.0,
                df=df,
                perturbation_pcts=(),
            )

    def test_sweeps_the_default_plus_minus_20_pct_bounds(self):
        df = _trending_ohlcv(n=300)
        result = analyze_parameter_sensitivity(
            lambda v: MovingAverageCross(
                fast_period=max(int(round(v)), 1), slow_period=15
            ),
            param_name="fast_period",
            baseline_value=5.0,
            df=df,
        )

        assert result.param_name == "fast_period"
        assert len(result.points) == 5
        values = [p.param_value for p in result.points]
        assert values[0] == pytest.approx(5.0 * 0.8)
        assert values[-1] == pytest.approx(5.0 * 1.2)

    def test_a_failing_strategy_factory_yields_none_points_not_a_raise(self):
        df = _trending_ohlcv(n=300)

        def always_fails(value):
            raise RuntimeError("bad params")

        result = analyze_parameter_sensitivity(
            always_fails, param_name="x", baseline_value=5.0, df=df
        )

        assert all(p.return_pct is None for p in result.points)
        assert result.is_cliff is False

    def test_as_dict_is_json_ready(self):
        df = _trending_ohlcv(n=300)
        result = analyze_parameter_sensitivity(
            lambda v: MovingAverageCross(
                fast_period=max(int(round(v)), 1), slow_period=15
            ),
            param_name="fast_period",
            baseline_value=5.0,
            df=df,
        )
        payload = result.as_dict()
        assert payload["param_name"] == "fast_period"
        assert "is_cliff" in payload
        assert len(payload["points"]) == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
