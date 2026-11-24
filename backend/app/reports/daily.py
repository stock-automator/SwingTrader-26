"""The 08:00 UK daily brief: up to 3 long setups, or "No trades today",
then one shortlist section per strategy in `settings.shortlist_strategies`.

Research and paper trading only. Every strategy in the registry scans the
cached watchlist once; LONG rows that size to at least one whole share are
ranked by reward:risk, deduplicated by ticker, and passed through the same
earnings/split lockout as live entries. A lookup error excludes the setup
(fail closed). Text is plain (no Markdown) so Telegram can never reject it.

Shortlist (cycle 03 step 4): each section reuses that strategy's scan, ranks
its own LONGs by its own `signal_strength`, keeps up to 3 that pass the live
gate (`build_risk_managed_order`'s reward:risk floor, then the lockout) and
sizes them like the portfolio engine (`PortfolioConfig`: 1% risk, 25% max
position). Its header is the strategy's test-period scorecard line; a
missing scorecard says so loudly. When the market regime blocks new longs
every section says so instead of listing picks.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.trading.client import TradingClient
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
from ..quant.portfolio import PortfolioConfig
from ..quant.regime import MACRO_REGIME_UNKNOWN, MacroRegimeDetector
from ..quant.risk import MIN_REWARD_RISK_RATIO, CircuitBreaker, RiskManager
from ..quant.setups import DIRECTION_LONG, ScanReport, Setup, scan_universe
from ..quant.strategies import REGISTRY, build_strategy

log = logging.getLogger(__name__)

UK = ZoneInfo("Europe/London")
MAX_SETUPS = 3
#: Bars of each ticker handed to the signal scan (~3 years); the last-bar
#: signal of every registry strategy is unchanged by older history.
SIGNAL_LOOKBACK_BARS = 750
RISK_PER_TRADE_PCT = 0.02
REGULAR_CLOSE = time(16, 0)
#: Longest run of closed days to step back over (a holiday weekend is 4).
MAX_LOOKBACK_DAYS = 14
FOOTER = "Research only, paper trading. Not financial advice."
#: FINRA's pattern-day-trader limit: day trades allowed per 5 business days.
DAY_TRADE_LIMIT = 3
#: Where the scheduler (and `POST /reports/daily/refresh`) caches the brief,
#: relative to `settings.data_dir`.
CACHE_RELATIVE_PATH = "reports/daily_brief.json"
#: Picks per shortlist section.
SHORTLIST_PICKS = 3
#: Shortlist sizing: the scorecard's portfolio-engine account and limits.
SHORTLIST_ENGINE = PortfolioConfig()
SCORECARD_MISSING = "NO SCORECARD YET"


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
class ShortlistPick:
    ticker: str
    strategy: str
    entry: float
    stop: float
    target: float
    shares: int
    risk_usd: float
    risk_gbp: float | None
    strength: float | None


@dataclass(frozen=True)
class ShortlistSection:
    """One strategy's picks. `trails_spy` is `None` without a scorecard
    line; `note` says why there are no picks (or is empty)."""

    strategy: str
    scorecard: str
    trails_spy: bool | None
    note: str
    picks: list[ShortlistPick] = field(default_factory=list)


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
    shortlist: list[ShortlistSection] = field(default_factory=list)
    shortlist_equity: float = SHORTLIST_ENGINE.initial_capital


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


def _day_trades(settings: Settings) -> int | None:
    """Alpaca's rolling 5-day day-trade count; `None` (line omitted) when
    Alpaca isn't configured or the lookup fails."""
    if not settings.has_alpaca:
        return None
    try:
        client = TradingClient(
            settings.alpaca_api_key, settings.alpaca_api_secret, paper=True
        )
        return int(client.get_account().daytrade_count)
    except Exception as exc:  # noqa: BLE001 - display only, omit the line
        log.warning("Alpaca day-trade count unavailable: %s", exc)
        return None


def _reason(strategy: str, setup: Setup) -> str:
    parts = [f"{strategy} long signal", f"ticker regime {setup.regime}"]
    if setup.adx is not None:
        parts.append(f"ADX {setup.adx:.0f}")
    if setup.reward_risk_ratio is not None:
        parts.append(f"R:R {setup.reward_risk_ratio:.1f}")
    if setup.note:
        parts.append(setup.note)
    return ", ".join(parts).replace("\n", " ")


def _rr(setup: Setup) -> float:
    return setup.reward_risk_ratio or 0.0


def _long_block(spy: pd.DataFrame | None) -> str | None:
    """Why no new long may open today, or `None`."""
    if spy is None:
        return None
    detector = MacroRegimeDetector()
    if detector.is_long_blocked(spy):
        return f"market regime {detector.current_regime(spy)} blocks new longs"
    if len(spy) > 1 and CircuitBreaker().is_triggered(spy["Close"]):
        return "the SPY drawdown circuit breaker is active"
    return None


def _scan_all(
    frames: dict[str, pd.DataFrame], settings: Settings
) -> dict[str, ScanReport]:
    """One scan per registry strategy over the last `SIGNAL_LOOKBACK_BARS`
    bars, shared by the brief's setups and the shortlist."""
    risk_manager = RiskManager(
        account_equity=DEFAULT_SCREENER_EQUITY, risk_per_trade_pct=RISK_PER_TRADE_PCT
    )
    recent = {t: df.tail(SIGNAL_LOOKBACK_BARS) for t, df in frames.items()}
    return {
        name: scan_universe(
            recent,
            build_strategy(name),
            risk_manager,
            min_avg_volume=settings.screener_min_avg_volume,
            volume_lookback=settings.screener_volume_lookback,
            stale_after_days=settings.screener_stale_after_days,
        )
        for name in REGISTRY
    }


class _Lockout:
    """The live earnings/split lockout, one lookup per ticker per build.
    `check` returns "locked", "failed" (lookup error: fail closed) or `None`."""

    def __init__(self, settings: Settings, now: datetime):
        self.guard = EarningsLockoutGuard(settings.earnings_lockout_hours)
        self.now = now.astimezone(NY)
        self.seen: dict[str, str | None] = {}

    def check(self, ticker: str) -> str | None:
        if ticker not in self.seen:
            self.seen[ticker] = self._lookup(ticker)
        return self.seen[ticker]

    def _lookup(self, ticker: str) -> str | None:
        try:
            earnings = fetch_earnings_dates(ticker)
            splits = fetch_stock_splits(ticker)
        except Exception as exc:  # noqa: BLE001 - any lookup failure fails closed
            log.warning("earnings lookup failed for %s: %s", ticker, exc)
            return "failed"
        return (
            "locked" if self.guard.is_blocked(self.now, earnings, splits)[0] else None
        )


def _candidates(
    reports: dict[str, ScanReport],
) -> tuple[list[tuple[str, Setup]], int]:
    """Sized LONG setups across every strategy, best reward:risk first, one
    per ticker; plus how many tickers only had a 0-share LONG."""
    found: dict[str, tuple[str, Setup]] = {}
    too_wide: set[str] = set()
    for name, report in reports.items():
        for setup in report.setups:
            if setup.direction != DIRECTION_LONG:
                continue
            if not setup.shares:
                too_wide.add(setup.ticker)
            elif setup.tradable:
                best = found.get(setup.ticker)
                if best is None or _rr(setup) > _rr(best[1]):
                    found[setup.ticker] = (name, setup)
    ranked = sorted(found.values(), key=lambda item: -_rr(item[1]))
    return ranked, len(too_wide - found.keys())


def _pick(
    tickers: list[str],
    frames: dict[str, pd.DataFrame],
    block: str | None,
    reports: dict[str, ScanReport],
    lockout: _Lockout,
    rate: FxRate | None,
) -> tuple[list[BriefSetup], str]:
    """Up to `MAX_SETUPS` setups, and the reason when there are none."""
    if not tickers:
        return [], "the watchlist is empty"
    if not frames:
        return [], "no cached price data for the watchlist"
    if block:
        return [], block

    candidates, too_wide = _candidates(reports)
    gate = RiskManager(
        account_equity=DEFAULT_SCREENER_EQUITY, risk_per_trade_pct=RISK_PER_TRADE_PCT
    )
    picked: list[BriefSetup] = []
    locked = failed = low_rr = gated = 0
    for strategy, setup in candidates:
        if len(picked) == MAX_SETUPS:
            break
        entry, stop = float(setup.entry_price), float(setup.stop_loss)
        order = gate.build_risk_managed_order(
            entry_price=entry,
            sl_type="FIXED",
            sl_value=entry - stop,
            tp_type="FIXED",
            tp_value=float(setup.take_profit) - entry,
            atr=setup.atr,
        )
        if order.rejected:
            if order.reward_risk_ratio < MIN_REWARD_RISK_RATIO:
                low_rr += 1
            else:
                gated += 1
            continue
        blocked = lockout.check(setup.ticker)
        if blocked == "failed":
            failed += 1
            continue
        if blocked == "locked":
            locked += 1
            continue
        risk_usd = round(order.risk_amount, 2)
        picked.append(
            BriefSetup(
                ticker=setup.ticker,
                strategy=strategy,
                entry=round(entry, 2),
                stop=round(order.stop_loss, 2),
                target=round(order.take_profit, 2),
                shares=order.shares,
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
    if low_rr:
        reasons.append(
            f"{low_rr} setup(s) below the {MIN_REWARD_RISK_RATIO:.1f} "
            "minimum reward:risk"
        )
    if gated:
        reasons.append(f"{gated} setup(s) rejected by the risk gate")
    if too_wide:
        reasons.append(f"{too_wide} setup(s) too wide to size one whole share")
    if not reasons:
        reasons.append("no strategy gave a long setup on the last close")
    return [], "; ".join(reasons)


def _load_scorecard(path: Path) -> dict[str, dict] | str:
    """`{strategy: test-period stats}` for the long rows, or the loud
    "no scorecard" line when the file is missing or unreadable."""
    try:
        body = json.loads(path.read_text())
        return {
            row["strategy"]: row["test"]
            for row in body["rows"]
            if row.get("variant") == "long" and isinstance(row.get("test"), dict)
        }
    except FileNotFoundError:
        log.warning("shortlist: scorecard missing at %s", path)
        return (
            f"{SCORECARD_MISSING}: {path.name} not found, run scripts/run_scorecard.py"
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        log.warning("shortlist: scorecard unreadable at %s: %s", path, exc)
        return f"{SCORECARD_MISSING}: {path.name} unreadable"


def _scorecard_line(card: dict[str, dict] | str, name: str) -> tuple[str, bool | None]:
    """The section's scorecard line and whether it trails exposure-matched
    SPY (`None` when there is no line)."""
    if isinstance(card, str):
        return card, None
    test = card.get(name)
    try:
        vs_spy = float(test["vs_spy_pct"])
        line = (
            f"Scorecard test {test['start']} to {test['end']}: "
            f"{float(test['total_return_pct']):+.1f}% vs SPY "
            f"{float(test['spy_matched_return_pct']):+.1f}% at the same exposure "
            f"({vs_spy:+.1f} pp), PF {float(test['profit_factor']):.2f}, "
            f"max DD {float(test['max_drawdown_pct']):.1f}%"
        )
    except (TypeError, KeyError, ValueError):
        log.warning("shortlist: no usable long scorecard row for %s", name)
        return f"{SCORECARD_MISSING} for {name}: no long row in the scorecard", None
    return line, vs_spy < 0


def _strategy_picks(
    name: str, report: ScanReport, lockout: _Lockout, rate: FxRate | None
) -> tuple[list[ShortlistPick], str]:
    """Up to `SHORTLIST_PICKS` of `name`'s LONGs, strongest signal first,
    through the live gate and sized like the portfolio engine; plus why
    there are none."""
    engine = SHORTLIST_ENGINE
    equity = engine.initial_capital
    gate = RiskManager(
        account_equity=equity, risk_per_trade_pct=engine.risk_per_trade_pct
    )
    longs = [s for s in report.setups if s.direction == DIRECTION_LONG]
    longs.sort(key=lambda s: (s.strength is None, -(s.strength or 0.0), s.ticker))
    picks: list[ShortlistPick] = []
    low_rr = gated = too_wide = locked = failed = 0
    for setup in longs:
        if len(picks) == SHORTLIST_PICKS:
            break
        entry, stop = float(setup.entry_price), float(setup.stop_loss)
        target = float(setup.take_profit)
        order = gate.build_risk_managed_order(
            entry_price=entry,
            sl_type="FIXED",
            sl_value=entry - stop,
            tp_type="FIXED",
            tp_value=target - entry,
            atr=setup.atr,
        )
        if order.rejected:
            if order.reward_risk_ratio < MIN_REWARD_RISK_RATIO:
                low_rr += 1
            else:
                gated += 1
            continue
        shares = min(
            gate.position_size(entry, order.stop_loss),
            math.floor(engine.max_position_pct * equity / entry),
        )
        if shares <= 0:
            too_wide += 1
            continue
        blocked = lockout.check(setup.ticker)
        if blocked == "failed":
            failed += 1
            continue
        if blocked == "locked":
            locked += 1
            continue
        risk_usd = round(shares * (entry - order.stop_loss), 2)
        picks.append(
            ShortlistPick(
                ticker=setup.ticker,
                strategy=name,
                entry=round(entry, 2),
                stop=round(order.stop_loss, 2),
                target=round(order.take_profit, 2),
                shares=shares,
                risk_usd=risk_usd,
                risk_gbp=None if rate is None else round(risk_usd / rate.rate, 2),
                strength=None if setup.strength is None else round(setup.strength, 2),
            )
        )
    if picks:
        return picks, ""
    reasons = []
    if low_rr:
        reasons.append(
            f"{low_rr} signal(s) below the {MIN_REWARD_RISK_RATIO:.1f} "
            "minimum reward:risk"
        )
    if gated:
        reasons.append(f"{gated} signal(s) rejected by the risk gate")
    if too_wide:
        reasons.append(f"{too_wide} signal(s) too wide to size one whole share")
    if locked:
        reasons.append(f"{locked} signal(s) inside the earnings/split lockout")
    if failed:
        reasons.append(f"earnings lookup failed for {failed} signal(s)")
    if not reasons:
        reasons.append("no long signal on the last close")
    return [], "No picks: " + "; ".join(reasons) + "."


def _shortlist(
    settings: Settings,
    block: str | None,
    reports: dict[str, ScanReport],
    lockout: _Lockout,
    rate: FxRate | None,
) -> list[ShortlistSection]:
    """One section per `settings.shortlist_strategies`, never dropped."""
    card = _load_scorecard(Path(settings.scorecard_path))
    sections = []
    for name in settings.shortlist_strategies:
        line, trails = _scorecard_line(card, name)
        picks: list[ShortlistPick] = []
        if name not in REGISTRY:
            log.warning("shortlist: unknown strategy %r in SHORTLIST_STRATEGIES", name)
            note = f"Unknown strategy {name!r}: not in the registry, nothing scanned."
        elif block:
            note = f"No new longs today: {block}."
        elif name not in reports:
            note = "No picks: no cached price data for the watchlist."
        else:
            picks, note = _strategy_picks(name, reports[name], lockout, rate)
        sections.append(ShortlistSection(name, line, trails, note, picks))
    return sections


def _render_shortlist(sections: list[ShortlistSection], equity: float) -> list[str]:
    if not sections:
        return []
    engine = SHORTLIST_ENGINE
    lines = [
        "",
        f"Shortlist by strategy (${equity:,.0f} account, "
        f"{engine.risk_per_trade_pct:.0%} risk, {engine.max_position_pct:.0%} "
        f"max position, up to {SHORTLIST_PICKS} each)",
    ]
    for section in sections:
        mark = "trails S&P — paper only" if section.trails_spy else "paper only"
        lines += ["", f"{section.strategy}: {mark}", section.scorecard]
        if not section.picks:
            lines.append(section.note)
        for i, p in enumerate(section.picks, start=1):
            gbp = "£ n/a" if p.risk_gbp is None else f"£{p.risk_gbp:.2f}"
            strength = "" if p.strength is None else f", strength {p.strength:.2f}"
            lines += [
                f"{i}. {p.ticker} entry {p.entry:.2f}, stop {p.stop:.2f}, "
                f"target {p.target:.2f}",
                f"   {p.shares} shares, risk ${p.risk_usd:.2f} / {gbp}{strength}",
            ]
    return lines


def _render(
    now: datetime,
    as_of: date | None,
    last_session: date,
    regime: str,
    stale: bool,
    rate: FxRate | None,
    setups: list[BriefSetup],
    reason: str,
    day_trades: int | None = None,
    shortlist: list[ShortlistSection] | None = None,
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
    if day_trades is not None:
        lines.append(f"Day trades used: {day_trades}/{DAY_TRADE_LIMIT} (last 5 days)")
    for i, s in enumerate(setups, start=1):
        gbp = "£ n/a" if s.risk_gbp is None else f"£{s.risk_gbp:.2f}"
        lines += [
            "",
            f"{i}. {s.ticker} long ({s.strategy})",
            f"Entry {s.entry:.2f}, stop {s.stop:.2f}, target {s.target:.2f}",
            f"{s.shares} shares, risk ${s.risk_usd:.2f} / {gbp}",
            f"Why: {s.reason}",
        ]
    lines += _render_shortlist(shortlist or [], SHORTLIST_ENGINE.initial_capital)
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
    block = _long_block(spy) if frames else None
    reports = _scan_all(frames, settings) if frames and not block else {}
    lockout = _Lockout(settings, now)
    setups, reason = _pick(tickers, frames, block, reports, lockout, rate)
    shortlist = _shortlist(settings, block, reports, lockout, rate)
    text = _render(
        now,
        as_of,
        last_session,
        regime,
        stale,
        rate,
        setups,
        reason,
        _day_trades(settings),
        shortlist,
    )
    return DailyBrief(
        as_of=as_of,
        last_session=last_session,
        regime=regime,
        stale=stale,
        text=text,
        setups=setups,
        shortlist=shortlist,
    )


def brief_to_dict(brief: DailyBrief) -> dict:
    """JSON-ready form of `brief` (dates as ISO strings)."""
    body = dataclasses.asdict(brief)
    body["as_of"] = brief.as_of.isoformat() if brief.as_of else None
    body["last_session"] = brief.last_session.isoformat()
    return body


def cache_path(settings: Settings) -> Path:
    return Path(settings.data_dir) / CACHE_RELATIVE_PATH


#: One process-wide lock: brief builds and cache writes never overlap.
BRIEF_LOCK = threading.Lock()


def refresh_cached_brief(settings: Settings) -> DailyBrief:
    """Build today's brief and cache it with a UTC `computed_at` stamp.
    The write goes through a temp file so a reader never sees half a file;
    `BRIEF_LOCK` makes concurrent callers take turns."""
    with BRIEF_LOCK:
        return _refresh_cached_brief(settings)


def _refresh_cached_brief(settings: Settings) -> DailyBrief:
    brief = build_daily_brief(settings)
    body = brief_to_dict(brief)
    body["computed_at"] = datetime.now(timezone.utc).isoformat()
    path = cache_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2))
    tmp.replace(path)
    return brief


def load_cached_brief(settings: Settings) -> dict | None:
    """The cached brief, or `None` if there is none or it is unreadable."""
    path = cache_path(settings)
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("daily brief cache unreadable at %s: %s", path, exc)
        return None
