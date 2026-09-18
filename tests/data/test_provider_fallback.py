"""Tests for `quant.data.provider_fallback`: the cache -> yfinance -> FMP ->
Alpha Vantage -> Polygon cascade. Every HTTP call and every cache/yfinance
call is mocked - no test in this file touches the network."""

from types import SimpleNamespace

import pandas as pd
import pytest

import backend.app.quant.data.provider_fallback as pf
from backend.app.data.loader import DataUnavailableError


def _bars(n: int = 5) -> pd.DataFrame:
    index = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series([100.0 + i for i in range(n)], index=index)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": pd.Series([1_000_000] * n, index=index),
        },
        index=index,
    )


def _response(status_code=200, json_body=None):
    return SimpleNamespace(
        status_code=status_code,
        ok=200 <= status_code < 300,
        json=lambda: json_body or {},
    )


@pytest.fixture
def cascade():
    return pf.ProviderFallbackCascade(
        data_dir="unused",
        fmp_api_key="fmp-key",
        alpha_vantage_api_key="av-key",
        polygon_api_key="polygon-key",
    )


class TestCacheAndYfinanceTiers:
    def test_cache_hit_short_circuits_everything_else(self, monkeypatch, cascade):
        cached = _bars()
        monkeypatch.setattr(pf, "load_cached", lambda ticker, data_dir: cached)
        monkeypatch.setattr(
            pf,
            "fetch_yfinance",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        )
        monkeypatch.setattr(
            pf.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError())
        )

        result = cascade.fetch("AAPL")
        assert result is cached

    def test_cache_miss_falls_through_to_yfinance(self, monkeypatch, cascade):
        yf_bars = _bars()
        monkeypatch.setattr(pf, "load_cached", lambda ticker, data_dir: None)
        monkeypatch.setattr(pf, "fetch_yfinance", lambda ticker, start, end: yf_bars)

        result = cascade.fetch("AAPL")
        assert result is yf_bars

    def test_yfinance_failure_falls_through_to_fmp(self, monkeypatch, cascade):
        monkeypatch.setattr(pf, "load_cached", lambda ticker, data_dir: None)

        def _fail_yf(ticker, start, end):
            raise DataUnavailableError("yfinance down")

        monkeypatch.setattr(pf, "fetch_yfinance", _fail_yf)

        fmp_body = {
            "historical": [
                {
                    "date": "2024-01-02",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 2_000_000,
                }
            ]
        }
        monkeypatch.setattr(
            pf.requests, "get", lambda *a, **k: _response(200, fmp_body)
        )

        result = cascade.fetch("AAPL")
        assert list(result["Close"]) == [100.5]


class TestUnconfiguredTiersAreSkipped:
    def test_unconfigured_provider_is_never_called(self, monkeypatch):
        cascade = pf.ProviderFallbackCascade(
            data_dir="unused",
            fmp_api_key=None,
            alpha_vantage_api_key=None,
            polygon_api_key=None,
        )
        monkeypatch.setattr(pf, "load_cached", lambda ticker, data_dir: None)

        def _fail_yf(ticker, start, end):
            raise DataUnavailableError("yfinance down")

        monkeypatch.setattr(pf, "fetch_yfinance", _fail_yf)

        calls = []
        monkeypatch.setattr(
            pf.requests,
            "get",
            lambda *a, **k: calls.append(1) or _response(200, {}),
        )

        with pytest.raises(DataUnavailableError):
            cascade.fetch("AAPL")

        assert calls == []


class TestRateLimitBackoff:
    def test_429_retries_then_succeeds(self, monkeypatch, cascade):
        monkeypatch.setattr(pf, "load_cached", lambda ticker, data_dir: None)

        def _fail_yf(ticker, start, end):
            raise DataUnavailableError("yfinance down")

        monkeypatch.setattr(pf, "fetch_yfinance", _fail_yf)
        monkeypatch.setattr(pf.time, "sleep", lambda seconds: None)

        fmp_body = {
            "historical": [
                {
                    "date": "2024-01-02",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 2_000_000,
                }
            ]
        }
        responses = iter([_response(429), _response(200, fmp_body)])
        monkeypatch.setattr(pf.requests, "get", lambda *a, **k: next(responses))

        result = cascade.fetch("AAPL")
        assert list(result["Close"]) == [100.5]

    def test_429_exhausts_retries_and_falls_to_next_tier(self, monkeypatch, cascade):
        monkeypatch.setattr(pf, "load_cached", lambda ticker, data_dir: None)

        def _fail_yf(ticker, start, end):
            raise DataUnavailableError("yfinance down")

        monkeypatch.setattr(pf, "fetch_yfinance", _fail_yf)
        monkeypatch.setattr(pf.time, "sleep", lambda seconds: None)

        av_body = {
            "Time Series (Daily)": {
                "2024-01-02": {
                    "1. open": "100.0",
                    "2. high": "101.0",
                    "3. low": "99.0",
                    "4. close": "100.5",
                    "5. volume": "2000000",
                }
            }
        }

        call_log = []

        def _get(url, *args, **kwargs):
            call_log.append(url)
            if "financialmodelingprep" in url:
                return _response(429)
            if "alphavantage" in url:
                return _response(200, av_body)
            raise AssertionError(f"unexpected url {url}")

        monkeypatch.setattr(pf.requests, "get", _get)

        result = cascade.fetch("AAPL")
        assert list(result["Close"]) == [100.5]
        # FMP was actually retried DEFAULT_MAX_ATTEMPTS times before falling
        # through, not just called once.
        fmp_calls = [u for u in call_log if "financialmodelingprep" in u]
        assert len(fmp_calls) == pf.DEFAULT_MAX_ATTEMPTS

    def test_alpha_vantage_soft_throttle_is_treated_as_rate_limit(
        self, monkeypatch, cascade
    ):
        monkeypatch.setattr(pf, "load_cached", lambda ticker, data_dir: None)

        def _fail_yf(ticker, start, end):
            raise DataUnavailableError("yfinance down")

        monkeypatch.setattr(pf, "fetch_yfinance", _fail_yf)
        monkeypatch.setattr(pf.time, "sleep", lambda seconds: None)

        polygon_body = {
            "results": [
                {
                    "t": 1704171600000,
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.0,
                    "c": 100.5,
                    "v": 3_000_000,
                }
            ]
        }

        def _get(url, *args, **kwargs):
            if "financialmodelingprep" in url:
                return _response(200, {})  # empty -> DataUnavailableError, not retried
            if "alphavantage" in url:
                return _response(
                    200,
                    {"Note": "Thank you for using Alpha Vantage! Our standard API..."},
                )
            if "polygon" in url:
                return _response(200, polygon_body)
            raise AssertionError(f"unexpected url {url}")

        monkeypatch.setattr(pf.requests, "get", _get)

        result = cascade.fetch("AAPL")
        assert list(result["Close"]) == [100.5]


class TestAllTiersFail:
    def test_raises_data_unavailable_when_every_tier_fails(self, monkeypatch, cascade):
        monkeypatch.setattr(pf, "load_cached", lambda ticker, data_dir: None)

        def _fail_yf(ticker, start, end):
            raise DataUnavailableError("yfinance down")

        monkeypatch.setattr(pf, "fetch_yfinance", _fail_yf)
        monkeypatch.setattr(pf.time, "sleep", lambda seconds: None)
        monkeypatch.setattr(pf.requests, "get", lambda *a, **k: _response(200, {}))

        with pytest.raises(DataUnavailableError, match="every provider tier failed"):
            cascade.fetch("AAPL")


class TestFromSettings:
    def test_builds_from_settings_fields(self):
        from backend.app.config import Settings

        settings = Settings(
            fmp_api_key="a", alpha_vantage_api_key="b", polygon_api_key="c"
        )
        cascade = pf.ProviderFallbackCascade.from_settings(settings)
        assert cascade.fmp_api_key == "a"
        assert cascade.alpha_vantage_api_key == "b"
        assert cascade.polygon_api_key == "c"
