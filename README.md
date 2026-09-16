# Stock Data Download Agent

A sophisticated Python agent that downloads historical stock market data from Yahoo Finance with intelligent rate-limiting, VPN management, and data validation.

## Overview

This project automates the collection of OHLC (Open, High, Low, Close) stock data for a large universe of tickers. It includes:

- **Intelligent Rate Limiting**: Detects API rate limits and automatically switches VPNs
- **LLM-Powered Decisions**: Uses Ollama (local LLM) to intelligently select VPN countries
- **Checkpoint System**: Resumes interrupted downloads without re-processing
- **Data Validation**: Ensures minimum data quality (50+ rows, valid OHLC)
- **Comprehensive Logging**: Tracks success, failures, and statistics
- **Hash Verification**: Validates file integrity with SHA256 checksums

## Features

### 1. Download Agent (`DownloadAgent` class)
- Downloads historical data from 2014-01-01 to present day
- Saves data as Parquet files (efficient columnar format)
- Automatic retry with VPN switching on rate limits
- Checkpoint-based resumption for long-running jobs
- Detailed statistics tracking

### 2. Data Update System
- Incremental updates for existing files
- Only re-downloads data from the last known date
- Merges new data with existing data (no duplicates)
- Configurable refresh window (default: 30 days)

### 3. Configuration
- Watchlist support from text file (one ticker per line)
- Configurable date ranges
- Adjustable retry limits and delays
- Multiple VPN country options

## Installation

### Requirements
- Python 3.8+
- `pandas`, `yfinance`, `pyarrow`, `requests`
- Ollama (for intelligent VPN selection)

### Setup

```bash
# Install dependencies
pip install pandas yfinance pyarrow requests

# Create watchlist file
cat > watchlist.txt << 'EOF'
AAPL
MSFT
GOOGL
AMZN
TSLA
EOF

# Ensure Ollama is running
ollama serve
ollama pull phi  # or your preferred model
```

## Usage

### Basic Usage

```python
from agent import DownloadAgent

# Create agent
agent = DownloadAgent()

# Load tickers from watchlist
tickers = agent.load_tickers()

# Download all tickers
for ticker in tickers:
    print(f"Processing {ticker}...")
    result = agent.download_ticker(ticker)
    agent.record_result(ticker, result)

# Save checkpoint
agent.save_checkpoint()

# Verify all downloaded data
verification_results = agent.verify_all_data()

# Generate report
agent.generate_report(tickers, verification_results, elapsed_seconds=0)
```

### Command Line

```bash
# Download initial dataset
python agent.py

# Update existing data (uses separate update_ticker function)
python agent.py --update
```

## Configuration

Key configuration parameters in `agent.py`:

```python
# Data storage
DATA_DIR = Path("data/raw")           # Output directory for Parquet files
CHECKPOINT_FILE = Path("download_checkpoint.json")  # Checkpoint tracking

# Date range
START_DATE = "2014-01-01"
END_DATE = datetime.now().strftime("%Y-%m-%d")

# Rate limiting
DOWNLOAD_DELAY = 1.0                  # Seconds between downloads
MAX_RETRIES = 3                       # Retry attempts on rate limit

# VPN switching
VPN_COUNTRIES = [
    "United States",
    "United Kingdom",
    "Netherlands",
    "Singapore",
    "Japan",
    "Canada",
]

# Ollama (local LLM)
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "phi"
OLLAMA_TIMEOUT = 10
```

## File Format

### Output: Parquet Files
Data is saved as Parquet files for efficient storage and retrieval:
- Location: `data/raw/{TICKER}.parquet`
- Format: Columnar storage (Apache Parquet)
- Columns: Date, Open, High, Low, Close, Adjusted Close, Volume
- Index: DatetimeIndex (date-based access)

### Checkpoint File
JSON file tracking download progress:
```json
{
  "started_at": "2024-01-15T10:30:45.123456",
  "last_updated": "2024-01-15T12:45:30.654321",
  "last_ticker_index": 150,
  "downloaded": [
    {
      "ticker": "AAPL",
      "rows": 2516,
      "hash": "abc123...",
      "timestamp": "2024-01-15T10:35:10.123456"
    }
  ],
  "unavailable": [
    {
      "ticker": "INVALID",
      "timestamp": "2024-01-15T10:45:20.123456"
    }
  ],
  "failed": [
    {
      "ticker": "FAILED",
      "timestamp": "2024-01-15T10:50:30.123456"
    }
  ]
}
```

### Watchlist File
Text file with one ticker per line:
```
AAPL
MSFT
GOOGL
# Comments start with #
TSLA
```

## Statistics

The agent tracks comprehensive statistics:

```python
stats = {
    "success": 42,              # Successfully downloaded
    "failed": 3,                # Failed after retries
    "unavailable": 5,           # No data available
    "rate_limited": 8,          # Rate limits encountered
    "vpn_switches": 4,          # VPN country switches
    "retries": 12,              # Total retry attempts
    "ollama_calls": 8,          # LLM invocations
    "skipped_existing": 85,     # Already cached
}
```

## Error Handling

### Rate Limiting
When detected (HTTP 429, "too many requests", "rate limit"):
1. Displays "RATE LIMITED" message
2. Invokes Ollama to select next VPN country
3. Switches VPN using `switch_vpn.sh`
4. Retries download (up to MAX_RETRIES times)

### Data Validation
Files are rejected if:
- Less than 50 rows of data
- Empty or null response from Yahoo Finance
- Parquet read fails (corrupted file)

### Checkpointing
Ensures no data loss:
- Saves after each ticker processed
- Temp file used during write (atomic operation)
- Resumes from `last_ticker_index` on restart

## Verification

Run data verification to check integrity:

```python
agent = DownloadAgent()
results = agent.verify_all_data()

# Results include:
# - status: "OK", "EMPTY", or "CORRUPT"
# - rows: number of records
# - date range: start and end dates
# - size: file size in MB
# - hash: SHA256 checksum
```

## Dependencies and Requirements

### Python Packages
- `pandas>=1.0.0` - Data manipulation
- `yfinance>=0.1.70` - Yahoo Finance API
- `pyarrow>=1.0.0` - Parquet file support
- `requests>=2.20.0` - HTTP requests

### External Tools
- `ollama` - Local LLM server for intelligent VPN selection
- `switch_vpn.sh` - Shell script for VPN switching (must be in same directory)

### System Requirements
- ~100GB disk space (for full S&P 500 dataset)
- Reliable internet connection
- VPN client configured with multiple countries

## Performance

Expected performance metrics:

- **Download speed**: ~1-2 tickers/second (with 1s delay between requests)
- **Full S&P 500**: ~8-12 hours for initial download
- **Updates**: ~15-30 minutes for daily refresh
- **Storage**: ~50-100MB per ticker (10 years of data)
- **Rate limits**: ~50-80 per 1000 requests from Yahoo Finance

## API Reference

### DownloadAgent

#### `__init__()`
Initialize agent and check Ollama availability.

#### `load_checkpoint()`
Load or create checkpoint file for resuming downloads.

#### `save_checkpoint()`
Save current progress to checkpoint file.

#### `check_ollama()`
Verify Ollama is running and accessible.

#### `ask_ollama(question: str) -> str`
Query Ollama with a prompt, returns response or None.

#### `switch_vpn(country: str) -> bool`
Switch to VPN country using `switch_vpn.sh`.

#### `get_file_hash(filepath: Path) -> str`
Calculate SHA256 hash of file.

#### `load_tickers() -> list[str]`
Load and deduplicate tickers from watchlist file.

#### `local_file_is_valid(ticker: str) -> bool`
Check if cached file exists and is readable.

#### `download_ticker(ticker: str, retry_count: int = 0) -> dict | str`
Download data for single ticker. Returns:
- `dict` with status="success", rows, hash
- `"cached"` if already downloaded
- `"unavailable"` if no data available
- `"failed"` if download failed

#### `record_result(ticker: str, result: dict | str)`
Update checkpoint with download result.

#### `verify_all_data() -> list[dict]`
Verify all Parquet files for integrity.

#### `generate_report(tickers, verification, elapsed_seconds)`
Generate text report of download session.

## Troubleshooting

### Ollama Not Running
```
⚠️ Ollama is not running.
Start it with:
    ollama serve
Then make sure the model exists:
    ollama pull phi
```

### VPN Script Not Found
```
⚠️ switch_vpn.sh not found
```
Ensure `switch_vpn.sh` exists in same directory as `agent.py`.

### Rate Limiting Continues
- Check VPN connection status
- Verify VPN script works correctly
- Try manual VPN switch: `bash switch_vpn.sh "United Kingdom"`
- Increase `DOWNLOAD_DELAY` or `MAX_RETRIES`

### Corrupted Parquet Files
```
✗ {ticker} CORRUPT
```
Delete the corrupted file and re-run download:
```bash
rm data/raw/{ticker}.parquet
```

### Watchlist Not Found
```
❌ watchlist.txt does not exist.
```
Create a watchlist file with tickers.

## Testing

Run the test suite:

```bash
# All tests
pytest tests/ -v

# Specific test class
pytest tests/test_agent.py::TestDownloadAgent -v

# With coverage
pytest tests/ --cov=agent --cov-report=html
```

See `tests/` directory for comprehensive test coverage.

## Contributing

- Add tests for new features
- Run verification before commits
- Update documentation
- Follow existing code style

## License

MIT

## References

- [Yahoo Finance API](https://finance.yahoo.com/)
- [yfinance Documentation](https://github.com/ranaroussi/yfinance)
- [Ollama](https://ollama.ai/)
- [Apache Parquet](https://parquet.apache.org/)
