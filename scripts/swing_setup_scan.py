#!/usr/bin/env python3
"""
Swing Trading Setups Scan — daily scan for Hermes cron → Telegram.
Identifies multi-day swing candidates: relative strength vs SPY/QQQ,
trend (price vs key MAs), 3-month momentum, sector themes.
Uses yfinance (no API key needed).
Output: plain text, one line per ticker, sorted by signal strength.
"""

import yfinance as yf
import json
import sys
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────────────
# Broader universe for swing scanning — liquid names across sectors.
TICKERS = [
    # Mega-cap tech
    "NVDA", "META", "GOOGL", "MSFT", "AAPL", "AMZN", "TSLA", "AMD", "AVGO", "ORCL",
    # Enterprise software / AI buyer-side
    "TEAM", "NOW", "CRM", "ADBE", "INTU", "WDAY", "CDNS", "SNPS", "MDB",
    # Semis / supply chain
    "ASML", "LITE", "PANW", "NET", "SNOW", "ANET", "CDW",
    # Energy (Hormuz + crude)
    "XOM", "COP", "CVX", "CVI", "VLO", "MPC", "PSX", "DK", "DINO", "CLMT", "HAL",
    # Financials
    "JPM", "GS", "BX", "SCHW", "SMCI",
    # Healthcare / biotech (non-AI diversifier)
    "LLY", "MRNA", "GEN", "BIIB", "AMGN", "GILD", "VRTX", "TXG",
    # Consumer / retail
    "HD", "LOW", "ANF", "TGT", "DLTR", "WMT", "SBUX", "MCD",
    # Industrials / defense
    "CAT", "DE", "HON", "LMT", "NOC", "RTX", "GE", "GD",
    # High-beta momentum
    "PLTR", "COIN",
    # ETFs for context
    "SPY", "QQQ", "IWM", "XLE", "XLK", "XLI", "XBI", "XLF", "XLP",
]

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

def download_all(tickers):
    """Download ~1 year of daily bars for every ticker in ONE batched request.

    A year is needed so the 50-day SMA actually exists (the old per-ticker
    ``period="2mo"`` call only returned ~42 bars, so ``sma_50`` was always
    None and every 50-day rule in ``signal_strength`` was dead).
    """
    data = yf.download(
        tickers,
        period="1y",
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    frames = {}
    for t in tickers:
        try:
            df = data[t].dropna(subset=["Close"])
        except KeyError:
            continue
        if not df.empty:
            frames[t] = df
    return frames


def fetch(ticker, hist):
    """Build the swing-analysis context for one ticker from its daily bars."""
    try:
        if hist is None or len(hist) < 10:
            return None

        # 3-month window (~63 trading days) for the 3M return / volume / range
        # stats, the full year for the moving averages.
        hist_3mo = hist.iloc[-63:]
        closes = hist_3mo["Close"].tolist()
        volumes = hist_3mo["Volume"].tolist()
        highs = hist_3mo["High"].tolist()
        lows = hist_3mo["Low"].tolist()
        dates = [d.strftime("%Y-%m-%d") for d in hist_3mo.index]

        today = hist_3mo.iloc[-1]
        today_date = dates[-1]
        price = float(today["Close"])
        today_vol = int(today["Volume"])

        # Previous close (for 1-day change)
        prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
        change_1d = ((price - prev_close) / prev_close) * 100 if prev_close else None

        # 3-month return
        ret_3m = ((closes[-1] - closes[0]) / closes[0]) * 100 if len(closes) >= 2 else None

        all_closes = hist["Close"]
        sma_20 = float(all_closes.iloc[-20:].mean()) if len(all_closes) >= 20 else None
        sma_50 = float(all_closes.iloc[-50:].mean()) if len(all_closes) >= 50 else None

        # Avg volume (30-day lookback, excluding today so today's spike
        # doesn't dilute its own ratio)
        prior_vols = volumes[-31:-1] or volumes
        avg_vol_30 = sum(prior_vols) / len(prior_vols) if prior_vols else 0
        vol_ratio_30 = today_vol / avg_vol_30 if avg_vol_30 > 0 else 0

        # Prior-week high/low: the 5 sessions BEFORE today. Including today's
        # own high made "close > week high" impossible.
        prior_highs = highs[-6:-1] if len(highs) >= 6 else highs[:-1]
        prior_lows = lows[-6:-1] if len(lows) >= 6 else lows[:-1]
        week_high = max(prior_highs) if prior_highs else None
        week_low = min(prior_lows) if prior_lows else None

        # Distance from 50-day SMA (percentage)
        dist_sma50 = ((price - sma_50) / sma_50) * 100 if sma_50 and sma_50 > 0 else None

        # Trend: price above/below key MAs
        above_sma20 = price > sma_20 if sma_20 else None
        above_sma50 = price > sma_50 if sma_50 else None

        return {
            "ticker": ticker,
            "date": today_date,
            "price": price,
            "change_1d": change_1d,
            "ret_3m": ret_3m,
            "sma_20": sma_20,
            "sma_50": sma_50,
            "dist_sma50_pct": dist_sma50,
            "above_sma20": above_sma20,
            "above_sma50": above_sma50,
            "volume": today_vol,
            "avg_volume_30": int(avg_vol_30),
            "vol_ratio_30": vol_ratio_30,
            "week_high": week_high,
            "week_low": week_low,
            "week_change": ((price - week_low) / week_low * 100) if week_low else None,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def signal_strength(d, spy_3m=None):
    """
    Score a ticker for swing-trading relevance.
    Higher = better swing candidate (long).
    """
    score = 0.0
    reasons = []

    ret3 = d.get("ret_3m")
    above_20 = d.get("above_sma20")
    above_50 = d.get("above_sma50")
    dist50 = d.get("dist_sma50_pct")
    vol_ratio = d.get("vol_ratio_30", 0) or 0
    price = d.get("price", 0) or 0
    week_high = d.get("week_high") or 0
    week_low = d.get("week_low") or 0

    # 3-month momentum (primary swing filter)
    if ret3 is not None:
        if ret3 >= 30:
            score += 4.0
            reasons.append(f"3M {pct(ret3)} (strong)")
        elif ret3 >= 15:
            score += 2.5
            reasons.append(f"3M {pct(ret3)}")
        elif ret3 >= 5:
            score += 1.0
            reasons.append(f"3M {pct(ret3)}")

    # Trend: above key MAs
    if above_50 is True:
        score += 2.0
        reasons.append("above 50-day SMA")
    elif above_50 is False:
        reasons.append("below 50-day SMA (caution)")

    if above_20 is True and above_50 is True:
        score += 1.0
        reasons.append("above 20 & 50-day (confirmed uptrend)")

    # Distance from 50-day (extended = pullback candidate, close = momentum)
    if dist50 is not None:
        if dist50 > 15:
            reasons.append(f"extended +{dist50:.1f}% vs 50-day (pullback risk)")
        elif dist50 > 5:
            score += 0.5
            reasons.append(f"+{dist50:.1f}% vs 50-day (healthy)")

    # Volume confirmation
    if vol_ratio >= 1.5:
        score += 0.5
        reasons.append(f"vol {volfmt(d.get('volume'))} ({vol_ratio:.1f}x 30d avg)")

    # Break of week high
    if price > week_high and week_high > 0:
        score += 1.0
        reasons.append(f"break weekly high ${sfmt(week_high)}")

    # Relative strength vs SPY (if we have it)
    if spy_3m is not None and ret3 is not None:
        rs = ret3 - spy_3m
        if rs > 10:
            score += 2.0
            reasons.append(f"RS +{rs:.1f}% vs SPY (leading)")
        elif rs > 5:
            score += 1.0
            reasons.append(f"RS +{rs:.1f}% vs SPY")
        elif rs < -5:
            reasons.append(f"RS {rs:.1f}% vs SPY (lagging)")

    return round(score, 1), reasons


def main():
    print(f"Swing Trading Setup Scan — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", flush=True)
    print("=" * 60, flush=True)

    # Fetch SPY first for relative strength baseline
    frames = download_all(TICKERS)
    spy_data = fetch("SPY", frames.get("SPY"))
    spy_3m = spy_data.get("ret_3m") if spy_data else None
    if spy_3m:
        print(f"SPY 3M return (RS baseline): {pct(spy_3m)}", flush=True)

    results = []
    for t in TICKERS:
        if t == "SPY":
            continue  # already fetched
        d = fetch(t, frames.get(t))
        if d is None:
            print(f"  {t}: no data", file=sys.stderr, flush=True)
            continue
        if "error" in d:
            print(f"  {t}: error — {d['error']}", file=sys.stderr, flush=True)
            continue
        score, reasons = signal_strength(d, spy_3m)
        d["score"] = score
        d["reasons"] = reasons
        results.append(d)

    # Sort by signal strength
    results.sort(key=lambda x: x["score"], reverse=True)

    # ── Output ─────────────────────────────────────────────────────────────
    # Top swing long candidates (score >= 5.0)
    print("\n🟢 SWING TRADING SETUPS — LONG (score ≥ 5.0)", flush=True)
    print("─" * 60, flush=True)

    top = [r for r in results if r["score"] >= 5.0]
    if not top:
        print("  No high-conviction swing longs this scan.", flush=True)
    else:
        for r in top:
            reasons_str = ", ".join(r["reasons"]) if r["reasons"] else ""
            sma_str = ""
            if r.get("sma_50"):
                pos = "ABOVE" if r.get("above_sma50") else "BELOW"
                sma_str = f" | 50DMA: {pos}"
            line = (
                f"  {r['ticker']:5s} ${sfmt(r['price'])} | "
                f"3M:{pct(r['ret_3m'])} | "
                f"1D:{pct(r['change_1d'])} | "
                f"vol {volfmt(r['volume'])} |"
                f"{sma_str} | "
                f"{reasons_str}"
            )
            print(line, flush=True)

    # ── Watch list (score 3.0–5.0) ─────────────────────────────────────────
    print("\n📋 SWING WATCH LIST (score 3.0–5.0, monitor for entry)", flush=True)
    print("─" * 60, flush=True)
    watch = [r for r in results if 3.0 <= r["score"] < 5.0]
    if watch:
        for r in watch[:25]:
            reasons_str = ", ".join(r["reasons"]) if r["reasons"] else ""
            line = (
                f"  {r['ticker']:5s} ${sfmt(r['price'])} | "
                f"3M:{pct(r['ret_3m'])} | "
                f"1D:{pct(r['change_1d'])} | "
                f"{reasons_str}"
            )
            print(line, flush=True)
    else:
        print("  (nothing in watch zone)", flush=True)

    # ── Weak/declining (potential shorts or avoid) ─────────────────────────
    print("\n🔴 SWING WEAKNESS (score < 2.0 or below 50-day, potential shorts/avoid)", flush=True)
    print("─" * 60, flush=True)
    weak = [r for r in results if r["score"] < 2.0 or (r.get("above_sma50") is False and r.get("ret_3m") is not None and r["ret_3m"] < -5)]
    if weak:
        for r in weak[:15]:
            reasons_str = ", ".join(r["reasons"]) if r["reasons"] else "no clear signal"
            line = (
                f"  {r['ticker']:5s} ${sfmt(r['price'])} | "
                f"3M:{pct(r['ret_3m'])} | "
                f"1D:{pct(r['change_1d'])} | "
                f"{reasons_str}"
            )
            print(line, flush=True)
    else:
        print("  (no notable weakness)", flush=True)

    # ── Sector context ─────────────────────────────────────────────────────
    print("\n📊 SECTOR / THEME CONTEXT", flush=True)
    print("─" * 60, flush=True)
    sectors = {
        "Tech (XLK)": "XLK",
        "Energy (XLE)": "XLE",
        "Financials (XLF)": "XLF",
        "Biotech (XBI)": "XBI",
        "Industrials (XLI)": "XLI",
        "S&P 500 (SPY)": "SPY",
        "Nasdaq (QQQ)": "QQQ",
    }
    for label, t in sectors.items():
        match = [r for r in results if r["ticker"] == t]
        if match:
            r = match[0]
            print(f"  {label}: ${sfmt(r['price'])} | 3M:{pct(r['ret_3m'])} | 1D:{pct(r['change_1d'])}", flush=True)

    # ── Summary ────────────────────────────────────────────────────────────
    longs = sum(1 for r in results if r["score"] >= 5.0)
    watch_cnt = sum(1 for r in results if 3.0 <= r["score"] < 5.0)
    print(f"\n  Scanned: {len(results)} tickers | Strong longs: {longs} | Watch: {watch_cnt}", flush=True)
    if spy_3m:
        print(f"  SPY 3M (RS baseline): {pct(spy_3m)} — names beating this have relative strength", flush=True)


if __name__ == "__main__":
    main()
