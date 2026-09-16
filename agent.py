import json
import hashlib
import subprocess
import time
from pathlib import Path
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf

# ============================================================
# CONFIG
# ============================================================

DATA_DIR = Path("data/raw")
DATA_DIR.mkdir(parents=True, exist_ok=True)

CHECKPOINT_FILE = Path("download_checkpoint.json")
WATCHLIST_FILE = Path("watchlist.txt")

START_DATE = "2014-01-01"
END_DATE = datetime.now().strftime("%Y-%m-%d")

# Delay between normal downloads
DOWNLOAD_DELAY = 1.0

# Maximum attempts for temporary failures
MAX_RETRIES = 3

# VPN countries used only when rate limiting is detected
VPN_COUNTRIES = [
    "United States",
    "United Kingdom",
    "Netherlands",
    "Singapore",
    "Japan",
    "Canada",
]

# Ollama
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "phi"
OLLAMA_TIMEOUT = 10


# ============================================================
# DOWNLOAD AGENT
# ============================================================


class DownloadAgent:

    def __init__(self):

        self.checkpoint = self.load_checkpoint()

        self.stats = {
            "success": 0,
            "failed": 0,
            "unavailable": 0,
            "rate_limited": 0,
            "vpn_switches": 0,
            "retries": 0,
            "ollama_calls": 0,
            "skipped_existing": 0,
        }

        self.session_started = datetime.now().isoformat()

        self.check_ollama()

    # ========================================================
    # CHECKPOINT
    # ========================================================

    def load_checkpoint(self):

        if CHECKPOINT_FILE.exists():

            try:
                with open(CHECKPOINT_FILE, "r") as f:
                    data = json.load(f)

                # Make sure old checkpoint files have all fields
                data.setdefault("downloaded", [])
                data.setdefault("unavailable", [])
                data.setdefault("failed", [])
                data.setdefault("last_ticker_index", 0)

                return data

            except Exception as e:

                print(f"⚠️ Could not read checkpoint: {e}")
                print("Starting with a fresh checkpoint.\n")

        return {
            "started_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "last_ticker_index": 0,
            "downloaded": [],
            "unavailable": [],
            "failed": [],
        }

    def save_checkpoint(self):

        self.checkpoint["last_updated"] = datetime.now().isoformat()

        temp_file = CHECKPOINT_FILE.with_suffix(".tmp")

        with open(temp_file, "w") as f:
            json.dump(self.checkpoint, f, indent=2)

        temp_file.replace(CHECKPOINT_FILE)

    # ========================================================
    # OLLAMA
    # ========================================================

    def check_ollama(self):

        try:

            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": "Reply with OK",
                    "stream": False,
                },
                timeout=5,
            )

            if response.status_code == 200:

                print(f"✓ Ollama ({OLLAMA_MODEL}) is running\n")
                return True

        except Exception:
            pass

        print("⚠️ Ollama is not running.")
        print("Start it with:")
        print()
        print("    ollama serve")
        print()
        print("Then make sure the model exists:")
        print()
        print(f"    ollama pull {OLLAMA_MODEL}")
        print()

        raise SystemExit(1)

    def ask_ollama(self, question):

        try:

            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": question,
                    "stream": False,
                    "temperature": 0.1,
                },
                timeout=OLLAMA_TIMEOUT,
            )

            if response.status_code == 200:

                self.stats["ollama_calls"] += 1

                answer = response.json().get("response", "").strip()

                for country in VPN_COUNTRIES:

                    if country.lower() in answer.lower():
                        return country

        except requests.exceptions.Timeout:

            print("   ⚠️ Ollama timeout")

        except Exception as e:

            print(f"   ⚠️ Ollama error: {e}")

        return None

    # ========================================================
    # VPN
    # ========================================================

    def switch_vpn(self, country):

        script = Path(__file__).parent / "switch_vpn.sh"

        if not script.exists():

            print("   ⚠️ switch_vpn.sh not found")
            return False

        print(
            f"   🌍 Switching VPN to {country}...",
            end=" ",
            flush=True,
        )

        try:

            subprocess.run(
                [str(script), country],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=Path(__file__).parent,
            )

            self.stats["vpn_switches"] += 1

            print("✓")

            # Give VPN time to reconnect
            time.sleep(10)

            return True

        except subprocess.CalledProcessError as e:

            print("✗")

            if e.stderr:
                print(f"      {e.stderr[:200]}")

            return False

        except Exception as e:

            print(f"✗ {e}")

            return False

    def get_next_vpn_country(self, retry_count):

        tried = VPN_COUNTRIES[:retry_count]

        available = [country for country in VPN_COUNTRIES if country not in tried]

        if not available:

            return VPN_COUNTRIES[retry_count % len(VPN_COUNTRIES)]

        prompt = f"""
Yahoo Finance appears to be rate limiting stock data downloads.

Countries already tried:
{", ".join(tried) if tried else "none"}

Available countries:
{", ".join(available)}

Choose one country to try next.

Reply with ONLY the country name.
"""

        suggestion = self.ask_ollama(prompt)

        if suggestion in available:

            return suggestion

        return available[0]

    # ========================================================
    # HASH
    # ========================================================

    def get_file_hash(self, filepath):

        sha256_hash = hashlib.sha256()

        with open(filepath, "rb") as f:

            for block in iter(
                lambda: f.read(1024 * 1024),
                b"",
            ):
                sha256_hash.update(block)

        return sha256_hash.hexdigest()

    # ========================================================
    # WATCHLIST
    # ========================================================

    def load_tickers(self):

        if not WATCHLIST_FILE.exists():

            print(f"❌ {WATCHLIST_FILE} does not exist.")

            raise SystemExit(1)

        with open(WATCHLIST_FILE, "r") as f:

            tickers = []

            for line in f:

                ticker = line.strip().upper()

                if ticker and not ticker.startswith("#"):

                    tickers.append(ticker)

        # Remove duplicates while preserving order
        tickers = list(dict.fromkeys(tickers))

        if not tickers:

            print("❌ watchlist.txt is empty.")
            raise SystemExit(1)

        return tickers

    # ========================================================
    # CHECK LOCAL CACHE
    # ========================================================

    def local_file_is_valid(self, ticker):

        filepath = DATA_DIR / f"{ticker}.parquet"

        if not filepath.exists():

            return False

        try:

            df = pd.read_parquet(filepath)

            if df.empty:

                return False

            return True

        except Exception:

            return False

    # ========================================================
    # DOWNLOAD
    # ========================================================

    def download_ticker(
        self,
        ticker,
        retry_count=0,
    ):

        filepath = DATA_DIR / f"{ticker}.parquet"

        # ----------------------------------------------------
        # Already downloaded
        # ----------------------------------------------------

        if self.local_file_is_valid(ticker):

            print("✓ Already cached")

            self.stats["skipped_existing"] += 1

            return "cached"

        # ----------------------------------------------------
        # Download
        # ----------------------------------------------------

        try:

            print(
                f"Downloading {ticker}...",
                end=" ",
                flush=True,
            )

            df = yf.download(
                ticker,
                start=START_DATE,
                end=END_DATE,
                progress=False,
                auto_adjust=False,
                timeout=30,
                threads=False,
            )

            # ------------------------------------------------
            # NO DATA = unavailable/invalid ticker
            # ------------------------------------------------

            if df is None or df.empty:

                print("⚠️ NO DATA")

                return "unavailable"

            # ------------------------------------------------
            # Validate minimum data
            # ------------------------------------------------

            if len(df) < 50:

                print(f"⚠️ Only {len(df)} rows")

                return "unavailable"

            # ------------------------------------------------
            # Save
            # ------------------------------------------------

            df.to_parquet(
                filepath,
                engine="pyarrow",
            )

            file_hash = self.get_file_hash(filepath)

            print(f"✓ {len(df):,} rows")

            return {
                "status": "success",
                "rows": len(df),
                "hash": file_hash,
            }

        except Exception as e:

            error = str(e)

            # ------------------------------------------------
            # Rate limiting
            # ------------------------------------------------

            rate_limited = (
                "429" in error
                or "too many requests" in error.lower()
                or "rate limit" in error.lower()
                or "rate-limited" in error.lower()
            )

            if rate_limited:

                self.stats["rate_limited"] += 1

                print("✗ RATE LIMITED")

                if retry_count < MAX_RETRIES:

                    country = self.get_next_vpn_country(retry_count)

                    if self.switch_vpn(country):

                        self.stats["retries"] += 1

                        print("   Waiting for connection...")

                        time.sleep(10)

                        return self.download_ticker(
                            ticker,
                            retry_count + 1,
                        )

                print(f"   ⚠️ Giving up on {ticker}")

                return "failed"

            # ------------------------------------------------
            # Other error
            # ------------------------------------------------

            print(f"✗ ERROR: {error[:100]}")

            return "failed"

    # ========================================================
    # RECORD RESULT
    # ========================================================

    def record_result(
        self,
        ticker,
        result,
    ):

        # Remove old status for this ticker
        self.checkpoint["downloaded"] = [
            x for x in self.checkpoint["downloaded"] if x["ticker"] != ticker
        ]

        self.checkpoint["unavailable"] = [
            x for x in self.checkpoint["unavailable"] if x["ticker"] != ticker
        ]

        self.checkpoint["failed"] = [
            x for x in self.checkpoint["failed"] if x["ticker"] != ticker
        ]

        # -----------------------------------------------
        # Success
        # -----------------------------------------------

        if isinstance(result, dict):

            self.checkpoint["downloaded"].append(
                {
                    "ticker": ticker,
                    "rows": result["rows"],
                    "hash": result["hash"],
                    "timestamp": datetime.now().isoformat(),
                }
            )

            self.stats["success"] += 1

        # -----------------------------------------------
        # Cached
        # -----------------------------------------------

        elif result == "cached":

            # Don't count cached files as new downloads
            pass

        # -----------------------------------------------
        # Invalid/unavailable
        # -----------------------------------------------

        elif result == "unavailable":

            self.checkpoint["unavailable"].append(
                {
                    "ticker": ticker,
                    "timestamp": datetime.now().isoformat(),
                }
            )

            self.stats["unavailable"] += 1

        # -----------------------------------------------
        # Failed
        # -----------------------------------------------

        elif result == "failed":

            self.checkpoint["failed"].append(
                {
                    "ticker": ticker,
                    "timestamp": datetime.now().isoformat(),
                }
            )

            self.stats["failed"] += 1

    # ========================================================
    # VERIFY DATA
    # ========================================================

    def verify_all_data(self):

        print("\n")
        print("=" * 70)
        print("VERIFYING DOWNLOADED DATA")
        print("=" * 70)

        results = []

        for filepath in sorted(DATA_DIR.glob("*.parquet")):

            ticker = filepath.stem

            try:

                df = pd.read_parquet(filepath)

                if df.empty:

                    results.append(
                        {
                            "ticker": ticker,
                            "status": "EMPTY",
                            "rows": 0,
                        }
                    )

                    continue

                file_hash = self.get_file_hash(filepath)

                size_mb = filepath.stat().st_size / (1024 * 1024)

                start = df.index.min()
                end = df.index.max()

                results.append(
                    {
                        "ticker": ticker,
                        "status": "OK",
                        "rows": len(df),
                        "start": str(start)[:10],
                        "end": str(end)[:10],
                        "size_mb": round(size_mb, 2),
                        "hash": file_hash,
                    }
                )

                print(
                    f"✓ {ticker:<6} "
                    f"{len(df):>6,} rows "
                    f"{str(start)[:10]} → "
                    f"{str(end)[:10]}"
                )

            except Exception as e:

                results.append(
                    {
                        "ticker": ticker,
                        "status": "CORRUPT",
                        "error": str(e),
                    }
                )

                print(f"✗ {ticker:<6} CORRUPT")

        return results

    # ========================================================
    # FINAL REPORT
    # ========================================================

    def generate_report(
        self,
        tickers,
        verification,
        elapsed_seconds,
    ):

        verified_ok = [x for x in verification if x["status"] == "OK"]

        corrupted = [x for x in verification if x["status"] == "CORRUPT"]

        unavailable = self.checkpoint["unavailable"]

        failed = self.checkpoint["failed"]

        report_file = Path("download_report.txt")

        lines = []

        lines.append("=" * 70)
        lines.append("STOCK DATA DOWNLOAD REPORT")
        lines.append("=" * 70)
        lines.append("")

        lines.append(f"Started:       {self.session_started}")

        lines.append(f"Finished:      {datetime.now().isoformat()}")

        lines.append(f"Date range:    {START_DATE} → {END_DATE}")

        lines.append(f"Watchlist:     {len(tickers)} tickers")

        lines.append(f"Runtime:       {elapsed_seconds / 60:.1f} minutes")

        lines.append("")

        lines.append("-" * 70)
        lines.append("SUMMARY")
        lines.append("-" * 70)

        lines.append(f"New downloads:       {self.stats['success']}")

        lines.append(f"Already cached:      {self.stats['skipped_existing']}")

        lines.append(f"Unavailable tickers: {len(unavailable)}")

        lines.append(f"Failed downloads:    {len(failed)}")

        lines.append(f"Verified files:      {len(verified_ok)}")

        lines.append(f"Corrupt files:       {len(corrupted)}")

        lines.append(f"Rate-limit events:   {self.stats['rate_limited']}")

        lines.append(f"VPN switches:        {self.stats['vpn_switches']}")

        lines.append(f"Retries:             {self.stats['retries']}")

        lines.append(f"Ollama decisions:    {self.stats['ollama_calls']}")

        lines.append("")

        # ----------------------------------------------------
        # Unavailable
        # ----------------------------------------------------

        lines.append("-" * 70)
        lines.append("UNAVAILABLE / NO DATA")
        lines.append("-" * 70)

        if unavailable:

            for item in unavailable:

                lines.append(f"  {item['ticker']}")

        else:

            lines.append("  None")

        lines.append("")

        # ----------------------------------------------------
        # Failed
        # ----------------------------------------------------

        lines.append("-" * 70)
        lines.append("FAILED")
        lines.append("-" * 70)

        if failed:

            for item in failed:

                lines.append(f"  {item['ticker']}")

        else:

            lines.append("  None")

        lines.append("")

        # ----------------------------------------------------
        # Corrupt
        # ----------------------------------------------------

        lines.append("-" * 70)
        lines.append("CORRUPT FILES")
        lines.append("-" * 70)

        if corrupted:

            for item in corrupted:

                lines.append(f"  {item['ticker']}: " f"{item.get('error', '')}")

        else:

            lines.append("  None")

        lines.append("")

        # ----------------------------------------------------
        # Verified
        # ----------------------------------------------------

        lines.append("-" * 70)
        lines.append("VERIFIED DATA")
        lines.append("-" * 70)

        for item in verified_ok:

            lines.append(
                f"  {item['ticker']:<6} "
                f"{item['rows']:>6,} rows "
                f"{item['start']} → "
                f"{item['end']} "
                f"{item['size_mb']:.2f} MB"
            )

        lines.append("")

        lines.append("=" * 70)
        lines.append("END REPORT")
        lines.append("=" * 70)

        with open(report_file, "w") as f:

            f.write("\n".join(lines))

        print("\n")
        print("\n".join(lines))

        print(f"\n📄 Full report saved to: " f"{report_file}")

    # ========================================================
    # MAIN
    # ========================================================

    def run(self):

        start_time = time.time()

        tickers = self.load_tickers()

        total = len(tickers)

        print()
        print("=" * 70)
        print("STOCK DATA DOWNLOAD AGENT")
        print("=" * 70)

        print(f"Tickers:       {total}")

        print(f"Date range:    {START_DATE} → {END_DATE}")

        print(f"Data location: {DATA_DIR}")

        print(f"Ollama:        {OLLAMA_MODEL}")

        print("=" * 70)
        print()

        # ----------------------------------------------------
        # Process every ticker
        # ----------------------------------------------------

        for i, ticker in enumerate(
            tickers,
            start=1,
        ):

            print(
                f"[{i:3d}/{total}] " f"{ticker:<6} ",
                end="",
            )

            result = self.download_ticker(ticker)

            self.record_result(
                ticker,
                result,
            )

            self.checkpoint["last_ticker_index"] = i - 1

            self.save_checkpoint()

            # Move on immediately for invalid ticker
            # Otherwise respect normal delay
            time.sleep(DOWNLOAD_DELAY)

        # ----------------------------------------------------
        # Verification
        # ----------------------------------------------------

        verification = self.verify_all_data()

        elapsed = time.time() - start_time

        # ----------------------------------------------------
        # Final report
        # ----------------------------------------------------

        self.generate_report(
            tickers,
            verification,
            elapsed,
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    agent = DownloadAgent()

    agent.run()
