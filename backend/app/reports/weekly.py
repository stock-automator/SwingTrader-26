"""The Saturday 09:00 UK weekly forward-test report, built from `broker_fills`.
"This week" is the Monday-Saturday week ending on the latest Saturday, so a
mid-week build reports the last full week.

Plain text, one Telegram message: this week's closed trades, the record since
the start after FX costs (`fx_cost_pct` x `fx_conversions_per_round_trip` x
entry notional, as `api/order_ticket.py`), a vs-SPY line only from
`MIN_TRADES_FOR_SPY` trades, open positions and the last universe sync.
Average R is "n/a": fills carry no stop, so there is no 1R to divide by.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from ..alerts.dispatcher import TelegramAlertChannel
from ..config import Settings
from ..data.fx import FxRate, fx_context, latest_gbp_rate
from ..data.loader import SPY_TICKER, DataUnavailableError, load_prices
from ..execution.alpaca_client import AlpacaExecutionClient
from ..journal.executor import TradeJournal
from ..journal.fills import ingest_fills, load_fills, open_positions, round_trips

log = logging.getLogger(__name__)

UK = ZoneInfo("Europe/London")
MIN_TRADES_FOR_SPY = 30
SATURDAY = 5
MAX_TRADE_LINES = 15
MAX_NAMES = 10
FOOTER = "Research only, paper trading. Not investment advice."


@dataclass(frozen=True)
class WeeklyReport:
    week_start: date
    trades_this_week: int
    trades_total: int
    open_positions: int
    net_usd_total: float
    text: str


def _usd(amount: float) -> str:
    return f"{'-' if amount < 0 else '+'}${abs(amount):,.2f}"


def _gbp(amount: float, rate: FxRate | None) -> str:
    if rate is None:
        return "£ n/a"
    gbp = amount / rate.rate
    return f"{'-' if gbp < 0 else '+'}£{abs(gbp):,.2f}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _spy_line(settings: Settings, trades: pd.DataFrame) -> str:
    """Average net trade return vs SPY over the same holding windows."""
    n = len(trades)
    if n < MIN_TRADES_FOR_SPY:
        return f"vs SPY: n too small ({n}/{MIN_TRADES_FOR_SPY})"
    try:
        spy = load_prices(
            SPY_TICKER,
            data_dir=settings.data_dir,
            allow_download=settings.allow_downloads,
        )["Close"].dropna()
    except DataUnavailableError:
        return "vs SPY: SPY prices unavailable"
    entry = pd.DatetimeIndex(trades["entry_date"]).tz_convert(None).normalize()
    exit_ = pd.DatetimeIndex(trades["exit_date"]).tz_convert(None).normalize()
    spy_return = (spy.asof(exit_).to_numpy() / spy.asof(entry).to_numpy() - 1).mean()
    if pd.isna(spy_return):
        return "vs SPY: SPY prices do not cover the trades"
    trade_return = trades["pnl_pct"].mean()
    return (
        f"vs SPY: avg trade {trade_return:+.2%} after costs, "
        f"SPY {spy_return:+.2%} over the same days ({n} trades)"
    )


def _universe_lines(settings: Settings) -> list[str]:
    try:
        status = json.loads(settings.universe_status_path.read_text())
    except (OSError, ValueError):
        return ["Universe: no sync status yet"]
    when = str(status.get("last_attempt") or "")[:10] or "n/a"
    if status.get("kept_previous"):
        return [f"Universe (sync {when}): kept previous list: {status.get('reason')}"]
    changes = status.get("last_changes") or {}
    added, removed = changes.get("added") or [], changes.get("removed") or []
    skipped = len(changes.get("skipped") or [])
    lines = [
        f"Universe (sync {when}): +{len(added)} −{len(removed)}, {skipped} skipped"
    ]
    for label, names in (("Added", added), ("Removed", removed)):
        if names:
            more = len(names) - MAX_NAMES
            tail = f" (+{more} more)" if more > 0 else ""
            lines.append(f"{label}: {', '.join(names[:MAX_NAMES])}{tail}")
    return lines


def _render(
    settings: Settings,
    now: datetime,
    week_start: datetime,
    trades: pd.DataFrame,
    week: pd.DataFrame,
    n_open: int,
    rate: FxRate | None,
    journal_error: str | None,
) -> str:
    lines = [f"SwingTrader weekly report, week of {week_start:%a %d %b %Y}"]
    if journal_error:
        lines.append(
            f"Warning: journal not updated ({journal_error}); "
            "figures are from the last stored fills."
        )
    if week.empty:
        lines.append("This week: no closed trades")
    else:
        wins = int((week["pnl"] > 0).sum())
        net = week["pnl"].sum()
        lines.append(
            f"This week: {_plural(len(week), 'closed trade')}, "
            f"{_plural(wins, 'win')}, net {_usd(net)} / {_gbp(net, rate)}"
        )
        for t in week.sort_values("exit_date").head(MAX_TRADE_LINES).itertuples():
            lines.append(
                f"  {t.ticker} {_usd(t.pnl)} ({t.qty:g} sh, "
                f"{t.entry_price:.2f} to {t.exit_price:.2f})"
            )
        if len(week) > MAX_TRADE_LINES:
            lines.append(f"  and {len(week) - MAX_TRADE_LINES} more")
    if trades.empty:
        lines.append("Since start: no closed trades yet")
    else:
        stats = TradeJournal.from_frame(trades).analyze_all_trades()
        net, costs = trades["pnl"].sum(), trades["cost"].sum()
        wins = int((trades["pnl"] > 0).sum())
        avg_r = stats["avg_r_multiple"]
        avg_r_text = "n/a (no stops on fills)" if pd.isna(avg_r) else f"{avg_r:.2f}"
        lines.append(
            f"Since start: {_plural(len(trades), 'closed trade')}, "
            f"{_plural(wins, 'win')} ({stats['win_rate']:.0%}), "
            f"net {_usd(net)} / {_gbp(net, rate)} after {_usd(costs)[1:]} costs, "
            f"avg R {avg_r_text}"
        )
    lines.append(_spy_line(settings, trades))
    lines.append(f"Open positions: {n_open}")
    lines += _universe_lines(settings)
    if rate is not None:
        lines.append(
            f"GBP/USD {rate.rate:.4f} ({rate.source}, {rate.as_of:%Y-%m-%d %H:%M})"
        )
    lines += ["", FOOTER]
    return "\n".join(lines)


def build_weekly_report(
    settings: Settings, now: datetime | None = None, journal_error: str | None = None
) -> WeeklyReport:
    """Build (not send) the report from the stored fills; Alpaca is not
    called. `journal_error` adds a "journal not updated" warning."""
    now = (now or datetime.now(UK)).astimezone(UK)
    # The Mon-Sat week ending on the latest Saturday on or before `now`.
    saturday = now.date() - timedelta(days=(now.weekday() - SATURDAY) % 7)
    week_start = datetime.combine(saturday - timedelta(days=5), time(), UK)
    fills = load_fills()
    trades = round_trips(fills)
    if not trades.empty:
        trades = trades[trades["exit_date"] <= now].copy()
    notional = trades["entry_price"] * trades["qty"]
    fx_round_trip = settings.fx_cost_pct * settings.fx_conversions_per_round_trip
    trades["cost"] = notional * fx_round_trip
    trades["pnl"] = trades["pnl"] - trades["cost"]
    trades["pnl_pct"] = trades["pnl"] / notional
    week = trades
    if len(trades):
        exits = trades["exit_date"]
        week = trades[(exits >= week_start) & (exits < week_start + timedelta(7))]
    with fx_context(settings.data_dir, offline=not settings.allow_downloads):
        rate = latest_gbp_rate()
    n_open = open_positions(fills)
    text = _render(settings, now, week_start, trades, week, n_open, rate, journal_error)
    return WeeklyReport(
        week_start=week_start.date(),
        trades_this_week=len(week),
        trades_total=len(trades),
        open_positions=n_open,
        net_usd_total=round(float(trades["pnl"].sum()), 2),
        text=text,
    )


def ingest_then_build(settings: Settings, now: datetime | None = None) -> WeeklyReport:
    """Pull new Alpaca fills, then build. A failed pull (or no keys) still
    builds, with the "journal not updated" warning."""
    error = None
    if not settings.has_alpaca:
        error = "Alpaca keys not set"
    else:
        client = AlpacaExecutionClient(
            settings.alpaca_api_key, settings.alpaca_api_secret
        )
        try:
            added = ingest_fills(settings, client, now=now)
            log.info("fills ingest added %d fill(s)", added)
        except Exception as exc:  # noqa: BLE001 - the report still goes out
            error = f"Alpaca fills unavailable: {type(exc).__name__}"
            log.warning("fills ingest failed: %s", type(exc).__name__)
    return build_weekly_report(settings, now, journal_error=error)


def send_weekly_report(
    settings: Settings, token: str, chat_id: str, now: datetime | None = None
) -> dict:
    """Ingest, build and send to Telegram. A send failure comes back as
    `sent=False` with the bot token scrubbed from the error."""
    report = ingest_then_build(settings, now)
    try:
        parts = TelegramAlertChannel(token, chat_id).send_text(report.text)
    except requests.RequestException as exc:
        error = str(exc).replace(token, "<token>")
        log.warning("weekly report send failed: %s", error)
        return {"sent": False, "parts": getattr(exc, "parts_sent", 0), "error": error}
    return {"sent": True, "parts": parts, "error": None}


def report_to_dict(report: WeeklyReport) -> dict:
    return {
        "week_start": report.week_start.isoformat(),
        "trades_this_week": report.trades_this_week,
        "trades_total": report.trades_total,
        "open_positions": report.open_positions,
        "net_usd_total": report.net_usd_total,
        "text": report.text,
    }
