"""The 08:00 UK daily brief: up to 3 long setups, or "No trades today".

Research and paper trading only. Every strategy in the registry scans the
cached watchlist; LONG rows that size to at least one whole share are ranked
by reward:risk, deduplicated by ticker, and passed through the same
earnings/split lockout as live entries. A lookup error excludes the setup
(fail closed). Text is plain (no Markdown) so Telegram can never reject it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
    sunday_to_monday,
)

from ..api.deps import load_frames, load_watchlist
from ..api.screener import DEFAULT_SCREENER_EQUITY
from ..config import Settings
from ..data.fx import FxRate, fx_context, latest_gbp_rate
from ..data.loader import (
    SPY_TICKER,
    DataUnavailableError,
    fetch_earnings_dates,
    fetch_stock_splits,
    load_prices,
)
from ..execution.guards import EarningsLockoutGuard
from ..execution.market_clock import NY, _clock_for
from ..quant.regime import MACRO_REGIME_UNKNOWN, MacroRegimeDetector
from ..quant.risk import CircuitBreaker, RiskManager
from ..quant.setups import DIRECTION_LONG, Setup, scan_universe
from ..quant.strategies import REGISTRY, build_strategy

log = logging.getLogger(__name__)

UK = ZoneInfo("Europe/London")
MAX_SETUPS = 3
RISK_PER_TRADE_PCT = 0.02
REGULAR_CLOSE = time(16, 0)
#: Longest run of closed days to step back over (a holiday weekend is 4).
MAX_LOOKBACK_DAYS = 14
FOOTER = "Research only, paper trading. Not financial advice."


@dataclass(frozen=True)
class BriefSetup:
    ticker: str
    strategy: str
    entry: float
    stop: float
    target: float
    shares: int
    risk_usd: float
    risk_gbp: float | None
    reason: str


@dataclass(frozen=True)
class DailyBrief:
    """`as_of` is the newest cached bar; `last_session` the last completed
    NYSE session at build time. `stale` when the first is older."""

    as_of: date | None
    last_session: date
    regime: str
    stale: bool
    text: str
    setups: list[BriefSetup] = field(default_factory=list)


class _NyseHolidays(AbstractHolidayCalendar):
    """Full-day NYSE closures, for the offline (no Alpaca keys) clock only."""

    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=sunday_to_monday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday(
            "Juneteenth",
            month=6,
            day=19,
            start_date="2022-01-01",
            observance=nearest_workday,
        ),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas Day", month=12, day=25, observance=nearest_workday),
    ]


@lru_cache(maxsize=8)
def _holidays(year: int) -> frozenset[date]:
    days = _NyseHolidays().holidays(f"{year}-01-01", f"{year}-12-31")
    return frozenset(d.date() for d in days)


def _offline_close(day: date) -> datetime | None:
    if day.weekday() >= 5 or day in _holidays(day.year):
        return None
    return datetime.combine(day, REGULAR_CLOSE, NY)


def last_completed_session(
    now: datetime, close_on: Callable[[date], datetime | None] = _offline_close
) -> date:
    """The latest NYSE trading day whose regular session closed by `now`."""
    moment = now.astimezone(NY)
    day = moment.date()
    for _ in range(MAX_LOOKBACK_DAYS):
        close = close_on(day)
        if close is not None and close <= moment:
            return day
        day -= timedelta(days=1)
    raise RuntimeError(f"no completed session in the {MAX_LOOKBACK_DAYS} days to {now}")


def _last_session(settings: Settings, now: datetime) -> date:
    """Alpaca's calendar when keys are set (half-days, ad-hoc closures),
    else the offline weekday/holiday rules."""
    if settings.has_alpaca:
        clock = _clock_for(settings.alpaca_api_key, settings.alpaca_api_secret)
        try:
            return last_completed_session(now, clock.close_on)
        except Exception as exc:  # noqa: BLE001 - display only, fall back
            log.warning("Alpaca calendar unavailable, using offline rules: %s", exc)
    return last_completed_session(now)


def _load_spy(settings: Settings) -> pd.DataFrame | None:
    try:
        return load_prices(
            SPY_TICKER,
            data_dir=settings.data_dir,
            allow_download=settings.allow_downloads,
        )
    except DataUnavailableError as exc:
        log.warning("SPY unavailable for the daily brief: %s", exc)
        return None


def _gbp_rate(settings: Settings) -> FxRate | None:
    with fx_context(settings.data_dir, offline=not settings.allow_downloads):
        return latest_gbp_rate()


def _reason(strategy: str, setup: Setup) -> str:
    parts = [f"{strategy} long signal", f"ticker regime {setup.regime}"]
    if setup.adx is not None:
        parts.append(f"ADX {setup.adx:.0f}")
    if setup.reward_risk_ratio is not None:
        parts.append(f"R:R {setup.reward_risk_ratio:.1f}")
    if setup.note:
        parts.append(setup.note)
    return ", ".join(parts).replace("\n", " ")


def _candidates(
    frames: dict[str, pd.DataFrame], settings: Settings
) -> tuple[list[tuple[str, Setup]], int]:
    """Sized LONG setups across every strategy, best reward:risk first, one
    per ticker; plus how many tickers only had a 0-share LONG."""
    risk_manager = RiskManager(
        account_equity=DEFAULT_SCREENER_EQUITY, risk_per_trade_pct=RISK_PER_TRADE_PCT
    )
    found: dict[str, tuple[str, Setup]] = {}
    too_wide: set[str] = set()
    for name in REGISTRY:
        report = scan_universe(
            frames,
            build_strategy(name),
            risk_manager,
            min_avg_volume=settings.screener_min_avg_volume,
            volume_lookback=settings.screener_volume_lookback,
            stale_after_days=settings.screener_stale_after_days,
        )
        for setup in report.setups:
            if setup.direction != DIRECTION_LONG:
                continue
            if not setup.shares:
                too_wide.add(setup.ticker)
            elif setup.tradable and setup.ticker not in found:
                found[setup.ticker] = (name, setup)
    ranked = sorted(
        found.values(), key=lambda item: -(item[1].reward_risk_ratio or 0.0)
    )
    return ranked, len(too_wide - found.keys())


def _pick(
    settings: Settings,
    now: datetime,
    tickers: list[str],
    frames: dict[str, pd.DataFrame],
    spy: pd.DataFrame | None,
    rate: FxRate | None,
) -> tuple[list[BriefSetup], str]:
    """Up to `MAX_SETUPS` setups, and the reason when there are none."""
    if not tickers:
        return [], "the watchlist is empty"
    if not frames:
        return [], "no cached price data for the watchlist"
    if spy is not None:
        detector = MacroRegimeDetector()
        if detector.is_long_blocked(spy):
            regime = detector.current_regime(spy)
            return [], f"market regime {regime} blocks new longs"
        if len(spy) > 1 and CircuitBreaker().is_triggered(spy["Close"]):
            return [], "the SPY drawdown circuit breaker is active"

    candidates, too_wide = _candidates(frames, settings)
    guard = EarningsLockoutGuard(settings.earnings_lockout_hours)
    picked: list[BriefSetup] = []
    locked = failed = 0
    for strategy, setup in candidates:
        if len(picked) == MAX_SETUPS:
            break
        try:
            earnings = fetch_earnings_dates(setup.ticker)
            splits = fetch_stock_splits(setup.ticker)
        except Exception as exc:  # noqa: BLE001 - any lookup failure fails closed
            log.warning("earnings lookup failed for %s: %s", setup.ticker, exc)
            failed += 1
            continue
        if guard.is_blocked(now.astimezone(NY), earnings, splits)[0]:
            locked += 1
            continue
        entry, stop = float(setup.entry_price), float(setup.stop_loss)
        risk_usd = round(setup.shares * (entry - stop), 2)
        picked.append(
            BriefSetup(
                ticker=setup.ticker,
                strategy=strategy,
                entry=round(entry, 2),
                stop=round(stop, 2),
                target=round(float(setup.take_profit), 2),
                shares=int(setup.shares),
                risk_usd=risk_usd,
                risk_gbp=None if rate is None else round(risk_usd / rate.rate, 2),
                reason=_reason(strategy, setup),
            )
        )
    if picked:
        return picked, ""

    reasons = []
    if locked:
        reasons.append(f"{locked} setup(s) inside the earnings/split lockout")
    if failed:
        reasons.append(f"earnings lookup failed for {failed} setup(s)")
    if too_wide:
        reasons.append(f"{too_wide} setup(s) too wide to size one whole share")
    if not reasons:
        reasons.append("no strategy gave a long setup on the last close")
    return [], "; ".join(reasons)


def _render(
    now: datetime,
    as_of: date | None,
    last_session: date,
    regime: str,
    stale: bool,
    rate: FxRate | None,
    setups: list[BriefSetup],
    reason: str,
) -> str:
    lines = [] if setups else [f"No trades today: {reason}."]
    lines.append(f"SwingTrader daily brief, {now.astimezone(UK):%a %d %b %Y}")
    lines.append(f"Prices to the {as_of or 'n/a'} close. Market regime {regime}.")
    if stale:
        lines.append(
            f"Warning: data stale. Last bar {as_of or 'n/a'}, "
            f"last completed session {last_session}."
        )
    if rate is not None:
        lines.append(
            f"GBP/USD {rate.rate:.4f} ({rate.source}, {rate.as_of:%Y-%m-%d %H:%M})"
        )
    for i, s in enumerate(setups, start=1):
        gbp = "£ n/a" if s.risk_gbp is None else f"£{s.risk_gbp:.2f}"
        lines += [
            "",
            f"{i}. {s.ticker} long ({s.strategy})",
            f"Entry {s.entry:.2f}, stop {s.stop:.2f}, target {s.target:.2f}",
            f"{s.shares} shares, risk ${s.risk_usd:.2f} / {gbp}",
            f"Why: {s.reason}",
        ]
    lines += ["", FOOTER]
    return "\n".join(lines)


def build_daily_brief(settings: Settings, now: datetime | None = None) -> DailyBrief:
    """Build (not send) today's brief from the cached prices."""
    now = (now or datetime.now(UK)).astimezone(UK)
    tickers = load_watchlist(settings)
    frames, _ = load_frames(tickers, settings)
    spy = _load_spy(settings)
    regime = MACRO_REGIME_UNKNOWN
    if spy is not None:
        regime = MacroRegimeDetector().current_regime(spy)

    last_bars = [df.index[-1] for df in frames.values() if not df.empty]
    if not last_bars and spy is not None and not spy.empty:
        last_bars = [spy.index[-1]]
    as_of = max(last_bars).date() if last_bars else None
    last_session = _last_session(settings, now)
    stale = as_of is None or as_of < last_session

    rate = _gbp_rate(settings)
    setups, reason = _pick(settings, now, tickers, frames, spy, rate)
    text = _render(now, as_of, last_session, regime, stale, rate, setups, reason)
    return DailyBrief(
        as_of=as_of,
        last_session=last_session,
        regime=regime,
        stale=stale,
        text=text,
        setups=setups,
    )
