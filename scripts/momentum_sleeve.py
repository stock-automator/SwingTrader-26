#!/usr/bin/env python3
"""
Momentum sleeve rebalance in the Alpaca paper account (6-1, top 10, equal
weight, 2N hold buffer, no SPY filter; `backend/app/execution/
momentum_sleeve.py`).

Run by `scripts/paper_executor.py` (cron, 14:35 UK weekdays) before the swing
executor. It only trades after 09:35 New York on the first NYSE trading day
of a month (Alpaca's calendar via `market_clock`), ranks today's universe on
the previous month's last close from the price cache, and places fractional
MARKET DAY orders through `/api/v1/execution/orders` (buys ENTRY, sells EXIT,
every guard still runs), each tagged `mom-<YYYY-MM>-<TICKER>`: sells first,
then buys. Once per month: a rerun skips names already traded this month.

Fails closed, placing nothing, when the brief is missing or stale, when
positions, account, calendar, cache or the state file are unavailable, or
when fewer than `top_n` names are eligible. A failed sell (guard 422 or
error) skips every buy; a failed buy is reported and retried on a rerun.

    python scripts/momentum_sleeve.py                 # live (first trading day)
    python scripts/momentum_sleeve.py --dry-run --month 2026-12 \\
        --rank-day 2026-11-20                         # plan only, no orders
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.app.api.deps import load_watchlist  # noqa: E402
from backend.app.config import Settings, get_settings  # noqa: E402
from backend.app.execution import momentum_sleeve as ms  # noqa: E402
from backend.app.execution.market_clock import NY, _clock_for  # noqa: E402

log = logging.getLogger("momentum_sleeve")

BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 15
TRADE_AFTER = time(9, 35)


def _rooted(settings: Settings) -> Settings:
    """Cache and universe paths resolved from the repo root, whatever the
    working directory."""
    data = Path(settings.data_dir)
    universe = Path(settings.universe_path)
    return replace(
        settings,
        data_dir=data if data.is_absolute() else ROOT / data,
        universe_path=universe if universe.is_absolute() else ROOT / universe,
    )


def load_universe(settings: Settings) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Cached bars of today's universe and SPY; no downloads."""
    from scripts.run_scorecard import load_universe as load

    frames, spy, missing = load(Path(settings.data_dir), load_watchlist(settings))
    if missing:
        log.warning("momentum sleeve: %d universe names not cached", len(missing))
    return frames, spy


def _fetch(session, url: str, key: str):
    """`key` from a 200 JSON body, or `None`."""
    resp = session.get(url, timeout=TIMEOUT)
    if resp.status_code != 200:
        return None, resp.status_code
    return resp.json().get(key), resp.status_code


def run(
    base_url: str = BASE_URL,
    session=requests,
    now: datetime | None = None,
    settings: Settings | None = None,
    clock=None,
    load: Callable | None = None,
    dry_run: bool = False,
    month: str | None = None,
    rank_day: date | None = None,
) -> dict:
    """One rebalance attempt. Raises requests.RequestException when the
    backend is unreachable. `dry_run` skips the day/time and once-a-month
    checks and returns the plan without placing orders or writing state."""
    settings = _rooted(settings or get_settings())
    now = (now or datetime.now(NY)).astimezone(NY)
    day = (
        datetime.strptime(month, "%Y-%m").date() if month else now.date()
    )  # any day of the traded month
    month = f"{day:%Y-%m}"
    out: dict = {
        "month": month,
        "rank_day": None,
        "placed": [],
        "blocked": [],
        "errors": [],
        "conflicts": [],
        "dropped": [],
        "reason": "",
        "plan": None,
    }
    path = settings.momentum_state_path
    api = f"{base_url}/api/v1"

    if not dry_run and now.time() < TRADE_AFTER:
        out["reason"] = "before 09:35 New York"
        return out
    try:
        state = ms.load_state(path)
    except (OSError, ValueError) as exc:
        out["reason"] = f"sleeve state unreadable ({type(exc).__name__}): fail closed"
        return out
    if not dry_run and state.get("month") == month and state.get("complete"):
        out["reason"] = f"already rebalanced {month}"
        return out

    resp = session.get(f"{api}/reports/daily", timeout=TIMEOUT)
    if resp.status_code != 200:
        out["reason"] = f"brief unavailable (HTTP {resp.status_code}): fail closed"
        return out
    if resp.json().get("stale"):
        out["reason"] = "stale brief: fail closed"
        return out

    close_on = (clock or _clock_for(settings.alpaca_api_key, settings.alpaca_api_secret)).close_on
    try:
        if not dry_run and not ms.is_first_trading_day(now.date(), close_on):
            out["reason"] = "not the first trading day of the month"
            return out
        rank = rank_day or ms.rank_day_for(day, close_on)
    except Exception as exc:  # noqa: BLE001 - any calendar failure fails closed
        out["reason"] = f"market calendar unavailable ({type(exc).__name__}): fail closed"
        return out
    out["rank_day"] = rank.isoformat()

    positions, code = _fetch(session, f"{api}/execution/positions", "positions")
    if not isinstance(positions, list):
        out["reason"] = f"positions unavailable (HTTP {code}): fail closed"
        return out
    equity, code = _fetch(session, f"{api}/execution/account", "equity")
    if not isinstance(equity, (int, float)) or equity <= 0:
        out["reason"] = f"account equity unavailable (HTTP {code}): fail closed"
        return out
    if not 0 < settings.momentum_sleeve_pct <= 1:
        out["reason"] = f"momentum_sleeve_pct {settings.momentum_sleeve_pct} not in (0, 1]"
        return out

    frames, spy = (load or load_universe)(settings)
    sleeve = {t: float(q) for t, q in (state.get("holdings") or {}).items()}
    cfg = ms.SLEEVE_CONFIG
    try:
        targets, prices = ms.rank_targets(frames, spy, rank, set(sleeve), cfg)
    except ValueError as exc:
        out["reason"] = f"{exc}: fail closed"
        return out
    if len(targets) < cfg.top_n:
        out["reason"] = (
            f"only {len(targets)} eligible names on {rank}, need {cfg.top_n}: "
            "fail closed"
        )
        return out

    sleeve_value = settings.momentum_sleeve_pct * float(equity)
    plan = ms.plan_rebalance(targets, sleeve, positions, prices, sleeve_value, cfg.top_n)
    out.update(plan=plan, conflicts=plan.conflicts, dropped=plan.dropped)
    for t in plan.conflicts:
        log.info("momentum sleeve: %s already held by the swing executor, skipped", t)
    if dry_run:
        out["sleeve_value"] = sleeve_value
        return out

    if state.get("month") != month:
        # New month: chain the return since the last rebalance, then reset.
        held_value = sum(q * prices.get(t, 0.0) for t, q in sleeve.items())
        base = float(state.get("base_value") or 0.0)
        index = float(state.get("index") or 1.0)
        state.update(
            month=month,
            done=[],
            complete=False,
            index=index * held_value / base if base > 0 else index,
        )
        if "start" not in state:
            spy_close = spy["Close"].get(pd.Timestamp(rank))
            state["start"] = {"date": now.date().isoformat(), "spy": float(spy_close)}
    for t in plan.dropped:
        sleeve.pop(t, None)
        log.warning("momentum sleeve: %s no longer held by the account, dropped", t)
    done = state.setdefault("done", [])

    def place(order: ms.SleeveOrder) -> bool:
        if order.ticker in done:
            return True
        r = session.post(
            f"{api}/execution/orders", json=order.body(month), timeout=TIMEOUT
        )
        label = f"{order.side.upper()} {order.ticker} {order.qty:g}"
        if r.status_code == 200:
            held = sleeve.get(order.ticker, 0.0)
            held += order.qty if order.side == "buy" else -order.qty
            if held > 1e-9:
                sleeve[order.ticker] = round(held, ms.QTY_DECIMALS)
            else:
                sleeve.pop(order.ticker, None)
            done.append(order.ticker)
            out["placed"].append(label)
            state["holdings"] = sleeve
            ms.save_state(path, state)
            return True
        if r.status_code == 422:
            out["blocked"].append(f"{label}: {r.json().get('detail')}")
        else:
            out["errors"].append(f"{label}: HTTP {r.status_code}")
        return False

    sells_ok = all([place(o) for o in plan.sells])
    if sells_ok:
        for o in plan.buys:
            place(o)
    else:
        out["reason"] = "a sell failed: buys skipped (fail closed)"
    state["holdings"] = sleeve
    state["complete"] = sells_ok and not out["blocked"] and not out["errors"]
    state["base_value"] = round(
        sum(q * prices.get(t, 0.0) for t, q in sleeve.items()), 2
    )
    ms.save_state(path, state)
    return out


def summary(out: dict) -> str:
    return (
        f"momentum_sleeve {out['month']}: placed={out['placed']} "
        f"blocked={out['blocked']} errors={out['errors']} "
        f"swing_conflicts={out['conflicts']} dropped={out['dropped']} "
        f"reason={out['reason'] or '-'}"
    )


def _plan_lines(out: dict) -> list[str]:
    plan = out["plan"]
    lines = [
        f"Dry run {out['month']}, ranked on the {out['rank_day']} close, "
        f"sleeve ${out['sleeve_value']:,.2f}:"
    ]
    for o in plan.sells + plan.buys:
        price = "n/a" if o.price is None else f"{o.price:,.2f}"
        notional = "" if o.price is None else f" = ${o.qty * o.price:,.2f}"
        lines.append(f"  {o.side:4} {o.ticker:6} {o.qty:12.6f} @ {price}{notional}")
    for t in plan.conflicts:
        lines.append(f"  skip {t:6} held by the swing executor")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="plan only")
    parser.add_argument("--month", help="YYYY-MM to plan (dry run)")
    parser.add_argument(
        "--rank-day", type=date.fromisoformat, help="rank on this close (dry run)"
    )
    args = parser.parse_args(argv)
    if not args.dry_run and (args.month or args.rank_day):
        parser.error("--month and --rank-day are for --dry-run only")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        out = run(dry_run=args.dry_run, month=args.month, rank_day=args.rank_day)
    except requests.RequestException as exc:
        print(f"momentum_sleeve: backend unreachable ({type(exc).__name__})")
        return 1
    print(summary(out))
    if args.dry_run and out["plan"] is not None:
        print("\n".join(_plan_lines(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
