"""
Multi-provider data fallback cascade.

Resolves OHLCV bars for one ticker by walking, in order: the local Parquet
cache, yfinance, then three paid-tier providers (Financial Modeling Prep,
Alpha Vantage, Polygon.io) - each skipped entirely, not attempted, if its
API key isn't configured. Exists to avoid free-tier rate limits stalling a
scan: if yfinance is throttled for a run, a configured paid tier picks up
the slack instead of the whole request failing.

This is a standalone orchestrator for now - `backend.app.data.loader.
load_prices` (the path `api/deps.py`'s `load_frames` actually calls in
production) is untouched. Wiring this cascade into that call path is a
separate, larger decision than adding the module.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from backend.app.config import Settings
from backend.app.data.loader import (
    DataUnavailableError,
    fetch_yfinance,
    load_cached,
    normalize_ohlcv,
)

log = logging.getLogger(__name__)

#: HTTP timeout for every provider request, in seconds.
REQUEST_TIMEOUT_SECONDS = 10.0

#: Retry policy for a rate-limited provider request.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0

#: Polygon requires an explicit date range; this is how far back "no start
#: given" defaults to.
DEFAULT_LOOKBACK_DAYS = 5 * 365


class RateLimitedError(RuntimeError):
    """A provider signalled a rate limit - HTTP 429, or (Alpha Vantage)
    a soft-throttle message embedded in an otherwise-200 JSON body.
    Distinct from `DataUnavailableError`: this is specifically retryable."""


def _with_backoff(
    fn,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
):
    """Call `fn()`, retrying on `RateLimitedError` with exponential backoff
    (`base_delay_seconds * 2**attempt`). Re-raises the last `RateLimitedError`
    if every attempt is exhausted. Any other exception propagates immediately
    - only a rate limit is worth retrying here."""
    last_exc: RateLimitedError | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except RateLimitedError as exc:
            last_exc = exc
            if attempt == max_attempts - 1:
                break
            delay = base_delay_seconds * (2**attempt)
            log.warning(
                "rate limited (attempt %d/%d): %s - retrying in %.1fs",
                attempt + 1,
                max_attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    raise last_exc


def _raise_for_rate_limit(response: requests.Response, provider: str) -> None:
    if response.status_code == 429:
        raise RateLimitedError(f"{provider}: HTTP 429")


def _frame_from_ohlcv_rows(
    rows: list[dict], ticker: str, provider: str
) -> pd.DataFrame:
    """Build the raw `Open/High/Low/Close/Volume` + DatetimeIndex frame
    `normalize_ohlcv` expects, from a provider-agnostic list of row dicts.

    Raises:
        DataUnavailableError: if `rows` is empty.
    """
    if not rows:
        raise DataUnavailableError(f"{provider}: no bars returned for {ticker}")

    df = pd.DataFrame(rows).set_index("date")
    df.index = pd.to_datetime(df.index)
    return df[["Open", "High", "Low", "Close", "Volume"]]


def fetch_fmp(ticker: str, api_key: str) -> pd.DataFrame:
    """Financial Modeling Prep historical daily bars.

    Raises:
        RateLimitedError: on HTTP 429.
        DataUnavailableError: on any other failure or empty response.
    """
    url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}"
    try:
        response = requests.get(
            url, params={"apikey": api_key}, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise DataUnavailableError(f"FMP: request failed for {ticker}: {exc}") from exc

    _raise_for_rate_limit(response, "FMP")
    if not response.ok:
        raise DataUnavailableError(f"FMP: HTTP {response.status_code} for {ticker}")

    payload = response.json()
    historical = payload.get("historical") if isinstance(payload, dict) else None
    if not historical:
        raise DataUnavailableError(f"FMP: no bars returned for {ticker}")

    rows = [
        {
            "date": bar["date"],
            "Open": bar["open"],
            "High": bar["high"],
            "Low": bar["low"],
            "Close": bar["close"],
            "Volume": bar["volume"],
        }
        for bar in historical
    ]
    return normalize_ohlcv(_frame_from_ohlcv_rows(rows, ticker, "FMP"), ticker)


def _is_alpha_vantage_throttled(payload: dict) -> str | None:
    """Alpha Vantage's free tier signals a rate limit inside a 200 response
    body via a `"Note"` or `"Information"` key rather than always using
    HTTP 429. Returns the throttle message, or `None` if not throttled."""
    for key in ("Note", "Information"):
        if key in payload:
            return str(payload[key])
    return None


def fetch_alpha_vantage(ticker: str, api_key: str) -> pd.DataFrame:
    """Alpha Vantage `TIME_SERIES_DAILY`, full history.

    Raises:
        RateLimitedError: on HTTP 429 or a detected soft-throttle body.
        DataUnavailableError: on any other failure or empty response.
    """
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": ticker,
        "outputsize": "full",
        "apikey": api_key,
    }
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise DataUnavailableError(
            f"Alpha Vantage: request failed for {ticker}: {exc}"
        ) from exc

    _raise_for_rate_limit(response, "Alpha Vantage")
    if not response.ok:
        raise DataUnavailableError(
            f"Alpha Vantage: HTTP {response.status_code} for {ticker}"
        )

    payload = response.json()
    throttle_message = _is_alpha_vantage_throttled(payload)
    if throttle_message is not None:
        raise RateLimitedError(f"Alpha Vantage: {throttle_message}")

    series = payload.get("Time Series (Daily)")
    if not series:
        raise DataUnavailableError(f"Alpha Vantage: no bars returned for {ticker}")

    rows = [
        {
            "date": date,
            "Open": bar["1. open"],
            "High": bar["2. high"],
            "Low": bar["3. low"],
            "Close": bar["4. close"],
            "Volume": bar["5. volume"],
        }
        for date, bar in series.items()
    ]
    return normalize_ohlcv(
        _frame_from_ohlcv_rows(rows, ticker, "Alpha Vantage"), ticker
    )


def fetch_polygon(
    ticker: str, api_key: str, start: str | None = None, end: str | None = None
) -> pd.DataFrame:
    """Polygon.io daily aggregates over `[start, end]` (inclusive), defaulting
    to a `DEFAULT_LOOKBACK_DAYS`-wide window ending today if unspecified.

    Raises:
        RateLimitedError: on HTTP 429.
        DataUnavailableError: on any other failure or empty response.
    """
    end_date = end or datetime.now().strftime("%Y-%m-%d")
    start_date = start or (
        datetime.now() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
    try:
        response = requests.get(
            url,
            params={"apiKey": api_key, "adjusted": "true", "sort": "asc"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise DataUnavailableError(
            f"Polygon: request failed for {ticker}: {exc}"
        ) from exc

    _raise_for_rate_limit(response, "Polygon")
    if not response.ok:
        raise DataUnavailableError(f"Polygon: HTTP {response.status_code} for {ticker}")

    payload = response.json()
    results = payload.get("results")
    if not results:
        raise DataUnavailableError(f"Polygon: no bars returned for {ticker}")

    rows = [
        {
            "date": pd.Timestamp(bar["t"], unit="ms"),
            "Open": bar["o"],
            "High": bar["h"],
            "Low": bar["l"],
            "Close": bar["c"],
            "Volume": bar["v"],
        }
        for bar in results
    ]
    return normalize_ohlcv(_frame_from_ohlcv_rows(rows, ticker, "Polygon"), ticker)


class ProviderFallbackCascade:
    """Walks cache -> yfinance -> FMP -> Alpha Vantage -> Polygon for one
    ticker's OHLCV bars, stopping at the first tier that succeeds.

    Args:
        data_dir: Parquet cache directory (tier 1).
        fmp_api_key: `None` skips the FMP tier entirely.
        alpha_vantage_api_key: `None` skips the Alpha Vantage tier entirely.
        polygon_api_key: `None` skips the Polygon tier entirely.
        max_attempts: Retry attempts per remote tier on a rate limit, before
            falling through to the next tier.
        base_delay_seconds: Backoff base delay per remote tier.
    """

    def __init__(
        self,
        data_dir,
        fmp_api_key: str | None = None,
        alpha_vantage_api_key: str | None = None,
        polygon_api_key: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
    ):
        self.data_dir = data_dir
        self.fmp_api_key = fmp_api_key
        self.alpha_vantage_api_key = alpha_vantage_api_key
        self.polygon_api_key = polygon_api_key
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> "ProviderFallbackCascade":
        return cls(
            data_dir=settings.data_dir,
            fmp_api_key=settings.fmp_api_key,
            alpha_vantage_api_key=settings.alpha_vantage_api_key,
            polygon_api_key=settings.polygon_api_key,
        )

    def _backoff(self, fn):
        return _with_backoff(fn, self.max_attempts, self.base_delay_seconds)

    def fetch(
        self, ticker: str, start: str | None = None, end: str | None = None
    ) -> pd.DataFrame:
        """Resolve `ticker`'s OHLCV bars via the cascade.

        Raises:
            DataUnavailableError: if every tier fails or is unconfigured.
        """
        ticker = ticker.upper()
        failures: list[str] = []

        cached = load_cached(ticker, self.data_dir)
        if cached is not None:
            log.debug("%s: satisfied by parquet cache", ticker)
            return cached
        log.info("%s: parquet cache miss, trying yfinance", ticker)

        try:
            return fetch_yfinance(ticker, start, end)
        except DataUnavailableError as exc:
            failures.append(f"yfinance: {exc}")
            log.warning("%s: yfinance failed: %s", ticker, exc)

        tiers = (
            ("FMP", self.fmp_api_key, lambda key: fetch_fmp(ticker, key)),
            (
                "Alpha Vantage",
                self.alpha_vantage_api_key,
                lambda key: fetch_alpha_vantage(ticker, key),
            ),
            (
                "Polygon",
                self.polygon_api_key,
                lambda key: fetch_polygon(ticker, key, start, end),
            ),
        )

        for name, api_key, call in tiers:
            if not api_key:
                log.debug("%s: %s not configured, skipping", ticker, name)
                continue

            log.info("%s: trying %s", ticker, name)
            try:
                result = self._backoff(lambda call=call, api_key=api_key: call(api_key))
                log.debug("%s: satisfied by %s", ticker, name)
                return result
            except (RateLimitedError, DataUnavailableError) as exc:
                failures.append(f"{name}: {exc}")
                log.warning("%s: %s failed: %s", ticker, name, exc)

        raise DataUnavailableError(
            f"{ticker}: every provider tier failed or was unconfigured - "
            + "; ".join(failures)
        )
