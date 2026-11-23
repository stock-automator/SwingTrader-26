"""Paper executor: places the cached daily brief's setups as bracket orders.

Run by cron at 14:35 UK on weekdays. All decisions (sizing, stop, target) come
from the backend brief; order guards (market hours, earnings) run server side.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8000"
STATE_PATH = Path(__file__).resolve().parents[1] / "data/reports/executor_state.json"
TIMEOUT = 15


def _submitted_today(day: str) -> list[str]:
    try:
        state = json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return []
    return state.get("tickers", []) if state.get("date") == day else []


def _save(day: str, tickers: list[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"date": day, "tickers": tickers}))


def run_once(base_url: str = BASE_URL, session=requests, now=None) -> dict:
    """Place today's unheld brief setups. Raises requests.RequestException
    when the backend is unreachable."""
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    out = {"placed": [], "skipped": [], "blocked": [], "reason": ""}
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
    held = {p["symbol"] for p in pos.json().get("positions", [])}
    done = _submitted_today(day)
    for s in brief.get("setups", []):
        t = s["ticker"]
        last = s.get("last_price")
        if t in held or t in done or (last is not None and last <= s["stop"]):
            out["skipped"].append(t)
            continue
        order = {
            "ticker": t,
            "qty": s["shares"],
            "side": "buy",
            "order_type": "BRACKET",
            "stop_loss": s["stop"],
            "take_profit": s["target"],
            "intent": "ENTRY",
        }
        r = session.post(f"{api}/execution/orders", json=order, timeout=TIMEOUT)
        if r.status_code == 422:
            out["blocked"].append(f"{t}: {r.json().get('detail')}")
        elif r.status_code == 200:
            out["placed"].append(t)
            done.append(t)
            _save(day, done)
        else:
            out["skipped"].append(t)
            out["reason"] = f"{t}: HTTP {r.status_code}"
    return out


def main() -> int:
    try:
        out = run_once()
    except requests.RequestException as exc:
        print(f"paper_executor: backend unreachable ({type(exc).__name__})")
        return 1
    print(
        f"paper_executor: placed={out['placed']} skipped={out['skipped']} "
        f"blocked={out['blocked']} reason={out['reason'] or '-'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
