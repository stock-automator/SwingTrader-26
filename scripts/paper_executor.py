"""Paper executor: places the cached daily brief's setups, then its
per-strategy shortlist picks, as bracket orders.

Run by cron at 14:35 UK on weekdays. All decisions (sizing, stop, target) come
from the backend brief; order guards (market hours, earnings) run server side.
Shortlist picks also respect the cycle 03 limits: at most `MAX_POSITIONS`
open (every held position counts) and open risk at most `MAX_HEAT_PCT` of the
brief's `shortlist_equity`. Open risk is the `risk_usd` recorded when this
executor placed a position (`open` in the state file); a held position it
did not place counts as `UNKNOWN_RISK_PCT`. Each pick carries its strategy in
Alpaca's `client_order_id` so the forward journal can attribute it.

Momentum sleeve: `main` first runs `scripts/momentum_sleeve.py` (it trades
on the first NYSE trading day of a month, catching up to the 10th). Names in
the sleeve's state file (`SLEEVE_PATH`) are the sleeve's: this executor never
trades them, and they count toward neither `MAX_POSITIONS` nor the heat cap.
An unreadable sleeve file counts nothing as sleeve, so the limits only get
tighter.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data/reports/executor_state.json"
#: The momentum sleeve's state (`Settings.momentum_state_path`).
SLEEVE_PATH = ROOT / "data/reports/momentum_sleeve.json"
TIMEOUT = 15
#: Cycle 03 Gate 1 limits (`PortfolioConfig`), applied to shortlist picks.
MAX_POSITIONS = 6
MAX_HEAT_PCT = 0.06
#: Assumed risk of a held position this executor has no record of.
UNKNOWN_RISK_PCT = 0.01


def _state() -> dict:
    try:
        state = json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _sleeve_tickers() -> set[str]:
    try:
        holdings = json.loads(SLEEVE_PATH.read_text()).get("holdings") or {}
    except (OSError, ValueError, AttributeError):
        return set()
    return {str(t).upper() for t in holdings} if isinstance(holdings, dict) else set()


def _save(day: str, tickers: list[str], book: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"date": day, "tickers": tickers, "open": book}))


def _bracket(s: dict) -> dict:
    return {
        "ticker": s["ticker"],
        "qty": s["shares"],
        "side": "buy",
        "order_type": "BRACKET",
        "stop_loss": s["stop"],
        "take_profit": s["target"],
        "intent": "ENTRY",
    }


def run_once(base_url: str = BASE_URL, session=requests, now=None) -> dict:
    """Place today's unheld brief setups. Raises requests.RequestException
    when the backend is unreachable."""
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    out = {"placed": [], "skipped": [], "blocked": [], "limited": [], "reason": ""}
    api = f"{base_url}/api/v1"

    resp = session.get(f"{api}/reports/daily", timeout=TIMEOUT)
    if resp.status_code == 404:
        out["reason"] = "no brief"
        return out
    brief = resp.json()
    if resp.status_code != 200 or brief.get("stale"):
        out["reason"] = "stale brief" if resp.status_code == 200 else "brief error"
        return out

    pos = session.get(f"{api}/execution/positions", timeout=TIMEOUT)
    positions = pos.json().get("positions") if pos.status_code == 200 else None
    if not isinstance(positions, list):
        # Limits need the live book: fail closed rather than see an empty one.
        out["reason"] = f"positions unavailable (HTTP {pos.status_code})"
        return out
    held = {p["symbol"] for p in positions}
    sleeve = _sleeve_tickers()
    # The sleeve's names are not this book's: no cap, no heat, no trades.
    book_held = held - sleeve
    state = _state()
    done = state.get("tickers", []) if state.get("date") == day else []
    book = {
        t: v for t, v in (state.get("open") or {}).items() if t in held or t in done
    }

    def place(s: dict, order: dict, strategy: str | None) -> None:
        t = s["ticker"]
        r = session.post(f"{api}/execution/orders", json=order, timeout=TIMEOUT)
        if r.status_code == 422:
            out["blocked"].append(f"{t}: {r.json().get('detail')}")
        elif r.status_code == 200:
            out["placed"].append(t)
            done.append(t)
            book[t] = {
                "strategy": strategy,
                "risk_usd": s.get("risk_usd"),
                "order_id": r.json().get("id"),
            }
            _save(day, done, book)
        else:
            out["skipped"].append(t)
            out["reason"] = f"{t}: HTTP {r.status_code}"

    def takeable(s: dict) -> bool:
        t, last = s["ticker"], s.get("last_price")
        stopped = last is not None and last <= s["stop"]
        if t in held or t in sleeve or t in done or stopped:
            out["skipped"].append(t)
            return False
        return True

    for s in brief.get("setups", []):
        if takeable(s):
            place(s, _bracket(s), s.get("strategy"))

    picks = [p for sec in brief.get("shortlist") or [] for p in sec.get("picks", [])]
    if not picks:
        return out
    equity = float(brief.get("shortlist_equity") or 0.0)
    if equity <= 0:
        out["reason"] = "no shortlist equity in the brief"
        return out

    def open_risk() -> float:
        return sum(
            float((book.get(t) or {}).get("risk_usd") or UNKNOWN_RISK_PCT * equity)
            for t in book_held | set(done)
        )

    for p in picks:
        if not takeable(p):
            continue
        t = p["ticker"]
        if len(book_held | set(done)) >= MAX_POSITIONS:
            out["limited"].append(f"{t}: max positions ({MAX_POSITIONS})")
            continue
        if open_risk() + float(p["risk_usd"]) > MAX_HEAT_PCT * equity + 1e-9:
            out["limited"].append(f"{t}: heat cap ({MAX_HEAT_PCT:.0%})")
            continue
        tag = f"st-{day}-{p['strategy']}-{t}"
        place(p, {**_bracket(p), "client_order_id": tag}, p["strategy"])
    return out


def run_sleeve() -> None:
    """The momentum sleeve's monthly rebalance; any failure is reported
    and never stops the swing run."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from scripts import momentum_sleeve

        print(momentum_sleeve.summary(momentum_sleeve.run()))
    except Exception as exc:  # noqa: BLE001 - the swing run still goes ahead
        print(f"momentum_sleeve: failed ({type(exc).__name__})")


def main() -> int:
    run_sleeve()
    try:
        out = run_once()
    except requests.RequestException as exc:
        print(f"paper_executor: backend unreachable ({type(exc).__name__})")
        return 1
    print(
        f"paper_executor: placed={out['placed']} skipped={out['skipped']} "
        f"blocked={out['blocked']} limited={out['limited']} "
        f"reason={out['reason'] or '-'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
