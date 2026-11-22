#!/usr/bin/env python3
"""
Day Trading Setups Scan — pre-market/daily scan for Hermes cron → Telegram.
Runs weekdays, delivers intraday momentum candidates: gap ups/downs,
volume spikes, key-level breaks. Uses yfinance (no API key needed).
Output: plain text, one line per ticker, sorted by signal strength.
"""

import yfinance as yf
import json
import sys
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────────────
# Liquid, day-trader-friendly tickers: mega-caps, momentum names, energy,
# tech, biotech. Add/remove to taste.
TICKERS = [
    # Mega-cap tech (gap + volume leaders)
    "NVDA", "META", "GOOGL", "MSFT", "AAPL", "AMZN", "TSLA", "AMD", "AVGO",
    # Energy (Hormuz + crude theme)
    "XOM", "COP", "CVI", "VLO", "MPC", "PSX", "DK", "DINO", "CLMT",
    # Enterprise software / AI buyer-side
    "TEAM", "NOW", "CRM", "ADBE", "INTU", "WDAY", "DLTR",
    # Semis / hyperscaler supply chain
    "ASML", "LITE", "PALO", "NET", "SNOW",
    # Biotech / momentum
    "TXG", "MRNA", "GEN", "BIIB", "AMGN",
    # Financials
    "JPM", "GS", "BX", "SMCI",
    # High-beta momentum
    "PLTR", "SOHU", "WW", "COIN", "LLY",
    # ETFs for context
    "SPY", "QQQ", "IWM", "XLE", "XLK", "XBI",
]

PREVIOUS_CLOSE_DAYS = 5  # look back this many days for avg volume

# ── Helpers ──────────────────────────────────────────────────────────────────
def sfmt(v, d=2):
    return f"{v:,.{d}f}" if v is not None else "N/A"

def pct(v):
    return f"{v:+.2f}%" if v is not None else "N/A"

def volfmt(v):
    if v is None:
        return "N/A"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return str(v)

def fetch(ticker):
    """Return dict of today's data + context, or None."""
    try:
        tkr = yf.Ticker(ticker)
        hist = tkr.history(period="10d")
        if len(hist) < 3:
            return None

        closes = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()
        highs = hist["High"].tolist()
        lows = hist["Low"].tolist()
        opens = hist["Open"].tolist()
        dates = [d.strftime("%Y-%m-%d") for d in hist.index]

        today = hist.iloc[-1]
        today_date = dates[-1]
        prev_close = closes[-2]
        prev_close_date = dates[-2]

        # Today's data
        day_open = float(today["Open"])
        day_high = float(today["High"])
        day_low = float(today["Low"])
        day_close = float(today["Close"])
        day_vol = int(today["Volume"])

        # Gap from previous close
        gap_pct = ((day_open - prev_close) / prev_close) * 100

        # Avg volume over lookback (excluding today)
        avg_vol = sum(volumes[-PREVIOUS_CLOSE_DAYS-1:-1]) / PREVIOUS_CLOSE_DAYS
        vol_ratio = day_vol / avg_vol if avg_vol > 0 else 0

        # 3-month return (approximate from 10d window + earlier data)
        # Fetch a bit more history for longer-term context
        try:
            hist_long = yf.Ticker(ticker).history(period="3mo")
            if len(hist_long) >= 2:
                long_close = float(hist_long["Close"].iloc[-1])
                long_prev = float(hist_long["Close"].iloc[0])
                ret_3m = ((day_close - long_prev) / long_prev) * 100
            else:
                ret_3m = None
        except Exception:
            ret_3m = None

        # 20-day SMA (approx from available data)
        sma_20 = None
        if len(closes) >= 5:
            sma_20 = sum(closes[-5:]) / 5  # rough proxy

        # Day range % (intraday volatility)
        day_range_pct = ((day_high - day_low) / day_low) * 100 if day_low > 0 else 0

        # Position in day's range (0=low, 1=high)
        position = (day_close - day_low) / (day_high - day_low) if day_high != day_low else 0.5

        return {
            "ticker": ticker,
            "date": today_date,
            "price": day_close,
            "open": day_open,
            "high": day_high,
            "low": day_low,
            "gap_pct": gap_pct,
            "prev_close": prev_close,
            "volume": day_vol,
            "avg_volume": int(avg_vol),
            "vol_ratio": vol_ratio,
            "day_range_pct": day_range_pct,
            "range_position": position,
            "ret_3m_pct": ret_3m,
            "sma_rough": sma_20,
            "week_high": max(highs[-5:]) if len(highs) >= 5 else max(highs),
            "week_low": min(lows[-5:]) if len(lows) >= 5 else min(lows),
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def signal_strength(d):
    """
    Score a ticker for day-trading relevance.
    Higher = more interesting for day trading.
    """
    score = 0.0
    reasons = []

    gap = d.get("gap_pct", 0) or 0
    vol_ratio = d.get("vol_ratio", 0) or 0
    pos = d.get("range_position", 0.5) or 0.5
    price = d.get("price", 0) or 0
    low = d.get("day_low", 0) or 0
    high = d.get("day_high", 0) or 0
    prev = d.get("prev_close", 0) or 0

    # Gap magnitude (big gaps = day-trading opportunity)
    if abs(gap) >= 2.0:
        score += 3.0
        reasons.append(f"gap {pct(gap)}")
    elif abs(gap) >= 1.0:
        score += 1.5
        reasons.append(f"gap {pct(gap)}")

    # Volume spike
    if vol_ratio >= 2.0:
        score += 2.5
        reasons.append(f"vol {volfmt(d.get('volume'))} ({vol_ratio:.1f}x avg)")
    elif vol_ratio >= 1.5:
        score += 1.0
        reasons.append(f"vol {volfmt(d.get('volume'))} ({vol_ratio:.1f}x avg)")

    # Intraday momentum (price in top/bottom quartile of range)
    if pos >= 0.75:
        score += 2.0
        reasons.append("bullish intraday (top quartile)")
    elif pos <= 0.25:
        score += 2.0
        reasons.append("bearish intraday (bottom quartile)")

    # Break of week high/low
    week_high = d.get("week_high") or 0
    week_low = d.get("week_low") or 0
    if price > week_high and week_high > 0:
        score += 1.5
        reasons.append(f"break weekly high ${sfmt(week_high)}")
    if price < week_low and week_low > 0:
        score += 1.5
        reasons.append(f"break weekly low ${sfmt(week_low)}")

    # 3-month momentum (for context)
    ret3 = d.get("ret_3m_pct")
    if ret3 is not None and ret3 > 20:
        score += 0.5
        reasons.append(f"3M {pct(ret3)}")

    return round(score, 1), reasons


def main():
    print(f"Day Trading Setup Scan — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", flush=True)
    print("=" * 60, flush=True)

    results = []
    for t in TICKERS:
        d = fetch(t)
        if d is None:
            continue
        if "error" in d:
            print(f"  {t}: error — {d['error']}", file=sys.stderr, flush=True)
            continue
        score, reasons = signal_strength(d)
        d["score"] = score
        d["reasons"] = reasons
        results.append(d)

    # Sort by signal strength, descending
    results.sort(key=lambda x: x["score"], reverse=True)

    # ── Output ─────────────────────────────────────────────────────────────
    # Top day-trading candidates (score >= 3.0)
    print("\n🔴 DAY TRADING SETUPS (pre-market scan, score ≥ 3.0)", flush=True)
    print("─" * 60, flush=True)

    top = [r for r in results if r["score"] >= 3.0]
    if not top:
        print("  No high-conviction day setups this scan.", flush=True)
    else:
        for r in top:
            gap_str = pct(r["gap_pct"])
            gap_dir = "▲ GAP UP" if r["gap_pct"] > 0 else "▼ GAP DOWN" if r["gap_pct"] < 0 else ""
            vol_str = f"vol {volfmt(r['volume'])} ({r['vol_ratio']:.1f}x)" if r['vol_ratio'] > 1.2 else ""
            reasons_str = ", ".join(r["reasons"]) if r["reasons"] else ""
            line = (
                f"  {r['ticker']:5s} ${sfmt(r['price'])} "
                f"{gap_dir} {gap_str} | "
                f"O:{sfmt(r['open'])} H:{sfmt(r['high'])} L:{sfmt(r['low'])} | "
                f"{vol_str} | "
                f"{reasons_str}"
            )
            print(line, flush=True)

    # ── Context: gaps + volume for all liquid names ────────────────────────
    print("\n📊 GAP & VOLUME WATCH (all scanned, score ≥ 1.0)", flush=True)
    print("─" * 60, flush=True)
    watch = [r for r in results if r["score"] >= 1.0 and r["score"] < 3.0]
    if watch:
        for r in watch[:20]:  # cap at 20
            gap_str = pct(r["gap_pct"])
            line = (
                f"  {r['ticker']:5s} ${sfmt(r['price'])} {gap_str} | "
                f"O:{sfmt(r['open'])} | "
                f"vol {volfmt(r['volume'])} ({r['vol_ratio']:.1f}x) | "
                f"range pos {r['range_position']:.0%}"
            )
            print(line, flush=True)
    else:
        print("  (nothing below threshold)", flush=True)

    # ── Market context ─────────────────────────────────────────────────────
    print("\n📈 MARKET CONTEXT", flush=True)
    print("─" * 60, flush=True)
    ctx = {r["ticker"]: r for r in results if r["ticker"] in ("SPY", "QQQ", "IWM", "XLE", "XLK")}
    for t in ["SPY", "QQQ", "IWM", "XLE", "XLK"]:
        if t in ctx:
            r = ctx[t]
            print(f"  {t}: ${sfmt(r['price'])} | gap {pct(r['gap_pct'])} | vol {volfmt(r['volume'])} ({r['vol_ratio']:.1f}x)", flush=True)

    # ── Summary stats ──────────────────────────────────────────────────────
    gaps = [r["gap_pct"] for r in results if r["gap_pct"] is not None]
    if gaps:
        up = sum(1 for g in gaps if g > 0)
        down = sum(1 for g in gaps if g < 0)
        print(f"\n  Scanned: {len(results)} tickers | Gaps up: {up} | Gaps down: {down}", flush=True)
        print(f"  Big gaps (>2%): {sum(1 for g in gaps if abs(g) >= 2)}", flush=True)
        print(f"  Volume spikes (>2x avg): {sum(1 for r in results if r['vol_ratio'] >= 2)}", flush=True)


if __name__ == "__main__":
    main()
