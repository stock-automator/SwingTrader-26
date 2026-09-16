# Stock Data Download Agent - API Documentation

Complete reference for all classes, methods, and functions.

## Table of Contents
1. [DownloadAgent](#downloadagent)
2. [Checkpoint Methods](#checkpoint-methods)
3. [Ollama Methods](#ollama-methods)
4. [VPN Methods](#vpn-methods)
5. [File Methods](#file-methods)
6. [Watchlist Methods](#watchlist-methods)
7. [Download Methods](#download-methods)
8. [Verification Methods](#verification-methods)
9. [Standalone Functions](#standalone-functions)

---

## DownloadAgent

Main class for downloading stock data with rate limiting and checkpoint management.

### `__init__()`

Initialize the DownloadAgent and verify system requirements.

**Signature:**
```python
def __init__(self):
```

**Behavior:**
- Loads checkpoint from `CHECKPOINT_FILE`
- Initializes empty statistics dictionary
- Records session start time
- Checks if Ollama is running and accessible
- Raises `SystemExit(1)` if Ollama is not available

**Example:**
```python
from agent import DownloadAgent

agent = DownloadAgent()
# If successful, Ollama is running
```

**Raises:**
- `SystemExit(1)` - If Ollama server is not running

---

## Checkpoint Methods

Methods for managing download progress checkpointing.

### `load_checkpoint()`

Load checkpoint from disk or create a new one.

**Signature:**
```python
def load_checkpoint(self) -> dict:
```

**Returns:**
```python
{
    "started_at": "2024-01-15T10:30:45.123456",
    "last_updated": "2024-01-15T10:30:45.123456",
    "last_ticker_index": 0,
    "downloaded": [],
    "unavailable": [],
    "failed": [],
}
```

**Behavior:**
- Reads `CHECKPOINT_FILE` if it exists
- Adds missing fields to old checkpoints
- Returns fresh checkpoint if file doesn't exist or is corrupted
- Prints warning if checkpoint file is unreadable

**Example:**
```python
checkpoint = agent.load_checkpoint()
print(f"Last completed at: {checkpoint['last_updated']}")
print(f"Successfully downloaded: {len(checkpoint['downloaded'])}")
```

### `save_checkpoint()`

Save current checkpoint progress to disk.

**Signature:**
```python
def save_checkpoint(self) -> None:
```

**Behavior:**
- Updates `last_updated` timestamp
- Writes to temporary file for atomic operation
- Replaces actual checkpoint file with temp file
- Ensures no data loss if process crashes

**Example:**
```python
# Download some data
agent.download_ticker("AAPL")
agent.record_result("AAPL", result)

# Save progress
agent.save_checkpoint()
```

**Note:** Atomic writes ensure checkpoint integrity. Even if process crashes during save, the previous checkpoint remains intact.

---

## Ollama Methods

Methods for interacting with the Ollama local LLM server.

### `check_ollama()`

Verify Ollama server is running and model is available.

**Signature:**
```python
def check_ollama(self) -> bool:
```

**Returns:**
- `True` if Ollama is running and responding
- `False` if connection fails

**Behavior:**
- Sends test request to Ollama server
- Checks HTTP 200 response
- Prints setup instructions if not running
- Called automatically during initialization

**Example:**
```python
if agent.check_ollama():
    print("✓ Ollama is ready")
else:
    print("✗ Ollama is not running")
```

**Setup Instructions:**
If Ollama is not running, start it with:
```bash
ollama serve

# In another terminal, pull the model:
ollama pull phi
```

### `ask_ollama(question: str) -> str | None`

Query Ollama with a prompt and get a response.

**Signature:**
```python
def ask_ollama(self, question: str) -> str | None:
```

**Parameters:**
- `question` (str): The prompt to send to Ollama

**Returns:**
- Country name if found in response (e.g., "United Kingdom")
- `None` if no valid country found, timeout, or error

**Behavior:**
- Sends prompt to Ollama with temperature=0.1 (deterministic)
- Extracts country name from response
- Handles timeouts gracefully
- Tracks Ollama call statistics

**Temperature Setting:**
- Low temperature (0.1) makes LLM responses more deterministic
- Ensures consistent VPN country selection

**Example:**
```python
prompt = """
Rate limiting detected. Available countries:
United States, United Kingdom, Netherlands

Choose the best country for next retry.
Reply with ONLY the country name.
"""

country = agent.ask_ollama(prompt)
if country:
    print(f"Ollama suggests: {country}")
```

**Timeout:**
- Default timeout is 10 seconds
- Prints warning and returns `None` on timeout

**Supported Countries:**
The method looks for these country names in the response:
- United States
- United Kingdom
- Netherlands
- Singapore
- Japan
- Canada

---

## VPN Methods

Methods for intelligent VPN switching based on rate limiting.

### `switch_vpn(country: str) -> bool`

Switch VPN to specified country.

**Signature:**
```python
def switch_vpn(self, country: str) -> bool:
```

**Parameters:**
- `country` (str): Country name to switch to (e.g., "United Kingdom")

**Returns:**
- `True` if VPN switch was successful
- `False` if script not found or command failed

**Behavior:**
- Executes `switch_vpn.sh` script with country parameter
- Prints status messages
- Waits 10 seconds for reconnection
- Tracks VPN switch statistics
- Handles subprocess errors gracefully

**Example:**
```python
success = agent.switch_vpn("United Kingdom")

if success:
    print("✓ VPN switched successfully")
    # Retry download now
else:
    print("✗ VPN switch failed")
    # Fall back to other retry logic
```

**Requirements:**
- `switch_vpn.sh` script must exist in same directory as `agent.py`
- Script must accept country name as first argument
- VPN client must be configured

**Shell Script Template:**
```bash
#!/bin/bash
COUNTRY=$1

# Switch VPN to $COUNTRY
# Implementation depends on your VPN client
# Example (ExpressVPN):
# expressvpn connect "$COUNTRY"

echo "Switched to $COUNTRY"
```

### `get_next_vpn_country(retry_count: int) -> str`

Get next VPN country to try based on retry attempt.

**Signature:**
```python
def get_next_vpn_country(self, retry_count: int) -> str:
```

**Parameters:**
- `retry_count` (int): Number of retries attempted so far

**Returns:**
- Country name (str) to switch to

**Behavior:**
- Tracks which countries have been tried
- Uses Ollama to intelligently select untried countries
- Falls back to first available country if Ollama fails
- Cycles through countries if all tried

**Example:**
```python
# First rate limit - try different country
country1 = agent.get_next_vpn_country(0)  # Might return "United Kingdom"

# Second rate limit - try another country
country2 = agent.get_next_vpn_country(1)  # Might return "Netherlands"

# Third rate limit - Ollama helps choose best one
country3 = agent.get_next_vpn_country(2)  # Ollama-selected country
```

**Ollama Prompt:**
```
Yahoo Finance appears to be rate limiting stock data downloads.

Countries already tried:
United States, United Kingdom

Available countries:
Netherlands, Singapore, Japan, Canada

Choose one country to try next.

Reply with ONLY the country name.
```

---

## File Methods

Methods for file operations, hashing, and validation.

### `get_file_hash(filepath: Path) -> str`

Calculate SHA256 hash of file.

**Signature:**
```python
def get_file_hash(self, filepath: Path) -> str:
```

**Parameters:**
- `filepath` (Path): Path to file to hash

**Returns:**
- 64-character hexadecimal SHA256 hash string

**Behavior:**
- Reads file in 1MB chunks (memory efficient)
- Calculates running SHA256 hash
- Works with large files without loading entire file

**Example:**
```python
filepath = Path("data/raw/AAPL.parquet")

hash_value = agent.get_file_hash(filepath)
print(f"File hash: {hash_value}")

# Hash is deterministic - same file always produces same hash
hash_again = agent.get_file_hash(filepath)
assert hash_value == hash_again
```

**Performance:**
- Processing speed: ~100-200 MB/second
- Memory usage: ~1 MB (only stores one chunk at a time)

---

## Watchlist Methods

Methods for loading and parsing ticker watchlist.

### `load_tickers() -> list[str]`

Load ticker list from watchlist file.

**Signature:**
```python
def load_tickers(self) -> list[str]:
```

**Returns:**
- List of uppercase ticker symbols

**Behavior:**
- Reads `WATCHLIST_FILE` line by line
- Ignores lines starting with `#` (comments)
- Ignores blank lines
- Converts to uppercase
- Removes duplicates while preserving order
- Raises `SystemExit(1)` if file missing or empty

**Example:**
```python
tickers = agent.load_tickers()
print(f"Will process {len(tickers)} tickers")

for ticker in tickers:
    print(f"  - {ticker}")
```

**Watchlist Format:**
```
# S&P 500 Stocks
AAPL
MSFT
GOOGL
# Comments are ignored
TSLA

# Blank lines are ignored too

META
```

**File Requirements:**
- File must exist at `WATCHLIST_FILE` path
- File must contain at least one valid ticker
- One ticker per line

---

## Download Methods

Methods for downloading stock data from Yahoo Finance.

### `local_file_is_valid(ticker: str) -> bool`

Check if cached file exists and is valid.

**Signature:**
```python
def local_file_is_valid(self, ticker: str) -> bool:
```

**Parameters:**
- `ticker` (str): Stock ticker symbol

**Returns:**
- `True` if file exists, is readable, and non-empty
- `False` if file missing, empty, or corrupted

**Behavior:**
- Checks file existence at `data/raw/{ticker}.parquet`
- Attempts to read file to verify integrity
- Returns `False` on any read error (corruption)

**Example:**
```python
if agent.local_file_is_valid("AAPL"):
    print("✓ AAPL data is cached")
else:
    print("⚠️ AAPL needs to be downloaded")
```

### `download_ticker(ticker: str, retry_count: int = 0) -> dict | str`

Download historical data for a single ticker.

**Signature:**
```python
def download_ticker(
    self,
    ticker: str,
    retry_count: int = 0,
) -> dict | str:
```

**Parameters:**
- `ticker` (str): Stock ticker symbol
- `retry_count` (int): Current retry attempt (used internally)

**Returns:**
- Success: `{"status": "success", "rows": 2516, "hash": "abc123..."}`
- Cached: `"cached"` (already downloaded)
- Unavailable: `"unavailable"` (no data available)
- Failed: `"failed"` (download failed after retries)

**Behavior:**
1. Checks if file is already cached locally
2. If cached, returns `"cached"` immediately
3. Downloads data from Yahoo Finance (2014-01-01 to today)
4. Validates minimum 50 rows
5. Saves as Parquet file
6. Calculates SHA256 hash
7. Handles rate limiting with VPN retry logic
8. Recursively retries up to `MAX_RETRIES` times

**Example:**
```python
# Download AAPL
result = agent.download_ticker("AAPL")

if isinstance(result, dict):
    print(f"✓ Downloaded {result['rows']:,} rows")
    print(f"  Hash: {result['hash']}")
elif result == "cached":
    print("✓ Already cached")
elif result == "unavailable":
    print("⚠️ No data available")
else:  # failed
    print("✗ Download failed")
```

**Rate Limiting Logic:**
```
Download attempt fails with 429 error
  ↓
Print "RATE LIMITED"
  ↓
Ask Ollama for next VPN country
  ↓
Switch VPN to selected country
  ↓
Wait 10 seconds for reconnection
  ↓
Retry download (recursive call with retry_count + 1)
  ↓
If max retries exceeded → return "failed"
```

**Data Validation:**
- Minimum row requirement: 50 rows
- Empty DataFrame check
- File existence check after save

---

## Data Recording Methods

### `record_result(ticker: str, result: dict | str) -> None`

Record download result in checkpoint.

**Signature:**
```python
def record_result(
    self,
    ticker: str,
    result: dict | str,
) -> None:
```

**Parameters:**
- `ticker` (str): Stock ticker symbol
- `result` (dict | str): Result from `download_ticker()`

**Behavior:**
- Removes old status for ticker (deduplication)
- Records success with rows and hash
- Ignores cached results (no tracking)
- Records unavailable tickers
- Records failed downloads
- Updates statistics

**Example:**
```python
for ticker in ["AAPL", "MSFT"]:
    result = agent.download_ticker(ticker)
    agent.record_result(ticker, result)

agent.save_checkpoint()
```

**Deduplication:**
If same ticker downloaded twice, only latest result is kept:
```python
# First download
agent.record_result("AAPL", {"status": "success", "rows": 2000})

# Re-download (updated data)
agent.record_result("AAPL", {"status": "success", "rows": 2516})

# Checkpoint has only the second record
```

---

## Verification Methods

### `verify_all_data() -> list[dict]`

Verify integrity of all downloaded Parquet files.

**Signature:**
```python
def verify_all_data(self) -> list[dict]:
```

**Returns:**
```python
[
    {
        "ticker": "AAPL",
        "status": "OK",
        "rows": 2516,
        "start": "2014-01-02",
        "end": "2024-01-15",
        "size_mb": 45.23,
        "hash": "abc123...",
    },
    {
        "ticker": "INVALID",
        "status": "CORRUPT",
        "error": "ParquetException: ...",
    },
]
```

**Behavior:**
- Scans all `.parquet` files in `DATA_DIR`
- Reads each file to verify integrity
- Calculates file hash
- Detects empty, corrupted, or invalid files
- Prints status for each file
- Returns detailed verification results

**Status Values:**
- `"OK"` - File is valid and readable
- `"EMPTY"` - File exists but has no data
- `"CORRUPT"` - File cannot be read (corrupted)

**Example:**
```python
results = agent.verify_all_data()

ok_files = [r for r in results if r["status"] == "OK"]
corrupt_files = [r for r in results if r["status"] == "CORRUPT"]

print(f"✓ {len(ok_files)} valid files")
print(f"✗ {len(corrupt_files)} corrupted files")

for r in corrupt_files:
    print(f"  - {r['ticker']}: {r['error']}")
```

---

## Report Generation

### `generate_report(tickers: list, verification: list, elapsed_seconds: float) -> None`

Generate summary report of download session.

**Signature:**
```python
def generate_report(
    self,
    tickers: list[str],
    verification: list[dict],
    elapsed_seconds: float,
) -> None:
```

**Behavior:**
- Writes text report to `download_report.txt`
- Includes session timing
- Summary of downloads, failures, and verifications
- Detailed statistics
- Verification results

**Report Contents:**
```
======================================================================
STOCK DATA DOWNLOAD REPORT
======================================================================

Started:       2024-01-15T10:30:45.123456
Finished:      2024-01-15T12:45:30.654321
Date range:    2014-01-01 → 2024-01-15
Watchlist:     500 tickers
Runtime:       135.8 minutes

----------------------------------------------------------------------
SUMMARY
----------------------------------------------------------------------
New downloads:       485
Already cached:      15
Unavailable tickers: 0
Failed downloads:    0
Verified files:      500

----------------------------------------------------------------------
STATISTICS
----------------------------------------------------------------------
Success:          485
Failed:           0
Unavailable:      0
Rate Limited:     2
VPN Switches:     1
Retries:          2
Ollama Calls:     2
Skipped Existing: 15

======================================================================
```

---

## Standalone Functions

Functions for independent use (update pipeline).

### `read_watchlist() -> list[str]`

Load tickers from watchlist file.

**Signature:**
```python
def read_watchlist() -> list[str]:
```

**Returns:**
- List of ticker symbols

**Behavior:**
- Same as `DownloadAgent.load_tickers()`
- Can be used independently

### `update_ticker(ticker: str) -> str`

Update existing ticker data with new data.

**Signature:**
```python
def update_ticker(ticker: str) -> str:
```

**Returns:**
- `"success"` - Data updated
- `"missing"` - File doesn't exist
- `"empty"` - File is empty
- `"no_data"` - No new data available
- `"failed"` - Update failed

**Behavior:**
- Checks if file exists
- Downloads latest data
- Merges with existing data (no duplicates)
- Saves combined data back to file

---

## Statistics Dictionary

The agent tracks comprehensive statistics during execution:

```python
agent.stats = {
    "success": 485,           # Successfully downloaded tickers
    "failed": 5,              # Failed after retries
    "unavailable": 10,        # Tickers with no data
    "rate_limited": 2,        # Rate limit encountered
    "vpn_switches": 1,        # VPN switches performed
    "retries": 2,             # Total retry attempts
    "ollama_calls": 2,        # LLM queries made
    "skipped_existing": 15,   # Already cached files
}
```

Access statistics at any time:
```python
print(f"Downloads: {agent.stats['success']}")
print(f"Rate limits: {agent.stats['rate_limited']}")
```

---

## Complete Example

```python
from agent import DownloadAgent
from datetime import datetime
import time

# Initialize
print("Initializing agent...")
agent = DownloadAgent()

# Load tickers
tickers = agent.load_tickers()
print(f"Processing {len(tickers)} tickers\n")

# Download all
start = time.time()
for i, ticker in enumerate(tickers, 1):
    print(f"[{i:3d}/{len(tickers)}] {ticker:<6} ", end="", flush=True)
    
    result = agent.download_ticker(ticker)
    agent.record_result(ticker, result)

elapsed = time.time() - start

# Save progress
agent.save_checkpoint()

# Verify data
print("\nVerifying data...")
verification = agent.verify_all_data()

# Generate report
agent.generate_report(tickers, verification, elapsed)

# Print summary
verified = [v for v in verification if v["status"] == "OK"]
print(f"\n✓ Successfully downloaded {len(verified)} tickers")
print(f"⚠️ Rate limits: {agent.stats['rate_limited']}")
print(f"🌍 VPN switches: {agent.stats['vpn_switches']}")
print(f"⏱️  Runtime: {elapsed / 60:.1f} minutes")
```

---

## Error Handling

All methods use appropriate error handling:

- **File errors**: Return `False` or `None`, print warning
- **Network errors**: Retry with VPN, return `"failed"`
- **Parsing errors**: Log and continue
- **Ollama timeout**: Return `None`, try next country

Always wrap downloads in try-except for production use:
```python
try:
    result = agent.download_ticker(ticker)
    agent.record_result(ticker, result)
except KeyboardInterrupt:
    print("Interrupted by user")
    agent.save_checkpoint()
    raise
except Exception as e:
    print(f"Unexpected error: {e}")
    agent.save_checkpoint()
    raise
```

---

## Constants

Configuration constants (modify in `agent.py`):

```python
DATA_DIR = Path("data/raw")                    # Output directory
CHECKPOINT_FILE = Path("download_checkpoint.json")  # Checkpoint file
WATCHLIST_FILE = Path("watchlist.txt")        # Watchlist file
START_DATE = "2014-01-01"                    # Download from date
END_DATE = datetime.now().strftime("%Y-%m-%d")  # Download to date
DOWNLOAD_DELAY = 1.0                         # Seconds between requests
MAX_RETRIES = 3                              # Retry attempts
VPN_COUNTRIES = [...]                        # Available countries
OLLAMA_URL = "http://localhost:11434/api/generate"  # Ollama server
OLLAMA_MODEL = "phi"                         # Ollama model name
OLLAMA_TIMEOUT = 10                          # Timeout in seconds
REFRESH_DAYS = 30                            # Update older than X days
```
