# Testing Guide - Stock Data Download Agent

Complete guide to running tests and verifying the implementation.

## Quick Start

```bash
# Install test dependencies
pip install -r requirements.txt

# Run all tests
pytest tests/ -v

# Run with coverage report
pytest tests/ --cov=agent --cov-report=html

# Run specific test class
pytest tests/test_agent.py::TestCheckpointManagement -v

# Run tests matching a pattern
pytest -k "checkpoint" -v
```

## Test Organization

Tests are organized by functionality:

```
tests/test_agent.py
├── TestCheckpointManagement      # Checkpoint load/save/resume
├── TestOllamaIntegration         # Ollama LLM integration
├── TestVPNSwitching              # VPN switching logic
├── TestFileOperations            # File hashing and validation
├── TestWatchlistLoading          # Watchlist parsing
├── TestCacheValidation           # Cache checking
├── TestDownloadLogic             # Download behavior
├── TestDataRecording             # Result recording
├── TestVerification              # Data verification
├── TestReporting                 # Report generation
├── TestIntegration               # End-to-end workflows
├── TestEdgeCases                 # Boundary conditions
└── TestPerformance               # Performance metrics
```

## Running Tests

### Run All Tests

```bash
pytest tests/ -v
```

Output:
```
tests/test_agent.py::TestCheckpointManagement::test_load_missing_checkpoint_creates_new PASSED
tests/test_agent.py::TestCheckpointManagement::test_load_existing_checkpoint_preserves_data PASSED
tests/test_agent.py::TestCheckpointManagement::test_save_checkpoint_atomic_operation PASSED
...
=============================== 120 passed in 5.23s ===============================
```

### Run Specific Test Class

```bash
# All checkpoint tests
pytest tests/test_agent.py::TestCheckpointManagement -v

# All Ollama tests
pytest tests/test_agent.py::TestOllamaIntegration -v

# All download tests
pytest tests/test_agent.py::TestDownloadLogic -v
```

### Run Tests by Pattern

```bash
# All tests containing "checkpoint"
pytest -k "checkpoint" -v

# All tests containing "ollama"
pytest -k "ollama" -v

# All tests NOT containing "slow"
pytest -k "not slow" -v
```

### Run with Markers

```bash
# All checkpoint tests
pytest -m checkpoint -v

# All performance tests
pytest -m performance -v

# All integration tests
pytest -m integration -v

# All smoke tests (quick validation)
pytest -m smoke -v
```

## Test Coverage

Generate test coverage report:

```bash
# Terminal report
pytest tests/ --cov=agent --cov-report=term-missing

# HTML report (opens in browser)
pytest tests/ --cov=agent --cov-report=html
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

Expected coverage:
- Statements: >85%
- Branches: >75%
- Lines: >90%

## Test Categories

### 1. Unit Tests
Test individual methods in isolation:

```bash
pytest tests/test_agent.py::TestCheckpointManagement -v
pytest tests/test_agent.py::TestFileOperations -v
pytest tests/test_agent.py::TestWatchlistLoading -v
```

### 2. Integration Tests
Test multiple components working together:

```bash
pytest tests/test_agent.py::TestIntegration -v
```

Tests include:
- Download → Record → Save workflow
- Checkpoint → Resume workflow
- Full pipeline with mock data

### 3. Edge Case Tests
Test boundary conditions:

```bash
pytest tests/test_agent.py::TestEdgeCases -v
```

Covers:
- Special characters in tickers
- Large watchlists (1000+ tickers)
- Concurrent access patterns
- Old and new date ranges

### 4. Performance Tests
Verify performance characteristics:

```bash
pytest tests/test_agent.py::TestPerformance -v
```

Tests:
- Checkpoint load speed (<100ms for 1000 items)
- Large Parquet read speed (<500ms for 2600 rows)

## Mocking and Fixtures

Tests use fixtures and mocks for isolation:

### Key Fixtures

```python
@pytest.fixture
def temp_dir():
    """Temporary directory for test files"""

@pytest.fixture
def mock_config(temp_dir):
    """Mock configuration paths"""

@pytest.fixture
def sample_parquet(temp_dir):
    """Sample Parquet file for testing"""
```

### Common Mocks

```python
# Mock yfinance download
@patch("yfinance.download")
def test_download(mock_yf_download):
    mock_yf_download.return_value = mock_df

# Mock Ollama requests
@patch("requests.post")
def test_ollama(mock_post):
    mock_response = Mock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

# Mock VPN switching
@patch("subprocess.run")
def test_vpn(mock_run):
    mock_run.return_value = Mock(returncode=0)
```

## Continuous Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.8', '3.9', '3.10', '3.11']
    
    steps:
    - uses: actions/checkout@v2
    - uses: actions/setup-python@v2
      with:
        python-version: ${{ matrix.python-version }}
    
    - name: Install dependencies
      run: |
        pip install -r requirements.txt
    
    - name: Run tests
      run: |
        pytest tests/ --cov=agent
    
    - name: Upload coverage
      uses: codecov/codecov-action@v2
```

## Common Issues and Solutions

### Issue: "Ollama not found" in tests

**Solution:** Tests mock Ollama, so it doesn't need to be running.

If seeing real Ollama errors:
```python
# This shouldn't happen - tests mock requests
@patch("requests.post")
def test_ollama(mock_post):
    # Mock prevents real request
```

### Issue: Tests fail due to missing files

**Solution:** Fixtures create temporary directories automatically.

If you see path errors:
```bash
# Check temp directory handling
pytest tests/test_agent.py::TestWatchlistLoading -v -s
```

### Issue: Timeout errors in tests

**Solution:** Tests have 30-second timeout by default.

Increase timeout:
```bash
pytest tests/ --timeout=60 -v
```

Or for specific test:
```python
@pytest.mark.timeout(120)
def test_slow_operation():
    # This test has 120 second timeout
```

## Writing New Tests

Template for new tests:

```python
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

class TestNewFeature:
    """Tests for new feature."""
    
    def test_basic_behavior(self, mock_config):
        """Test basic functionality."""
        # Arrange
        input_data = "test"
        
        # Act
        result = some_function(input_data)
        
        # Assert
        assert result == expected
    
    @patch("module.external_function")
    def test_with_mock(self, mock_external):
        """Test with external dependency mocked."""
        # Mock behavior
        mock_external.return_value = "mocked"
        
        # Test code
        result = function_using_external()
        
        # Assertions
        assert result == "expected"
        mock_external.assert_called_once()
    
    def test_edge_case(self):
        """Test edge case handling."""
        # Empty input
        assert function([]) == default
        
        # Large input
        assert function([1] * 1000) == expected
        
        # Special characters
        assert function("!@#$") == expected
```

### Test Naming Convention

```python
# Good test names
def test_download_ticker_success():
def test_download_ticker_rate_limit_error():
def test_verify_all_data_corrupted_file():
def test_record_result_removes_duplicates():

# Bad test names
def test_1():
def test_stuff():
def test_works():
```

## Performance Benchmarking

Run performance tests:

```bash
# All performance tests
pytest tests/test_agent.py::TestPerformance -v

# Benchmark specific function
python -m pytest tests/test_agent.py::TestPerformance::test_checkpoint_load_speed -v --durations=0
```

Expected timings:
- Checkpoint load: <100ms for 1000 items
- Parquet read: <500ms for 2600 rows
- File hash: <200ms for 50MB file

## Test Data

### Sample Parquet Structure

Generated test Parquets have:
- 100 rows (for valid tests)
- Date index from 2020-01-01
- OHLCV columns:
  - Open: random 0-100
  - High: random 0-100
  - Low: random 0-100
  - Close: random 0-100
  - Volume: random 1M-10M

### Sample Checkpoint Structure

```json
{
  "started_at": "2024-01-15T10:30:45.123456",
  "last_updated": "2024-01-15T12:30:45.123456",
  "last_ticker_index": 50,
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

## Debugging Tests

### Verbose Output

```bash
# Very verbose output
pytest tests/ -vv

# Print print statements
pytest tests/ -v -s

# Show local variables on failure
pytest tests/ -v -l
```

### Step Through Tests

```bash
# Drop into debugger on failure
pytest tests/ --pdb

# Always drop into debugger
pytest tests/ --pdb --pdbcls=IPython.terminal.debugger:TerminalPdb
```

### Logging

Enable logging in tests:

```bash
pytest tests/ -v --log-cli-level=DEBUG
```

## Validation Checklist

Before committing:

- [ ] All tests pass: `pytest tests/ -v`
- [ ] Coverage >85%: `pytest tests/ --cov=agent`
- [ ] No warnings: `pytest tests/ -W error`
- [ ] Code style: `flake8 agent.py tests/`
- [ ] Type checking: `mypy agent.py`

Run full validation:

```bash
#!/bin/bash
echo "Running tests..."
pytest tests/ -v || exit 1

echo "Checking coverage..."
pytest tests/ --cov=agent --cov-report=term-missing || exit 1

echo "Checking code style..."
flake8 agent.py tests/ || exit 1

echo "Type checking..."
mypy agent.py || exit 1

echo "✓ All checks passed!"
```

## Test Results Summary

Expected test run output:

```
tests/test_agent.py::TestCheckpointManagement::test_load_missing_checkpoint_creates_new PASSED
tests/test_agent.py::TestCheckpointManagement::test_load_existing_checkpoint_preserves_data PASSED
tests/test_agent.py::TestCheckpointManagement::test_save_checkpoint_atomic_operation PASSED
tests/test_agent.py::TestOllamaIntegration::test_check_ollama_success PASSED
tests/test_agent.py::TestOllamaIntegration::test_check_ollama_failure PASSED
tests/test_agent.py::TestVPNSwitching::test_switch_vpn_script_missing PASSED
tests/test_agent.py::TestVPNSwitching::test_switch_vpn_successful PASSED
tests/test_agent.py::TestFileOperations::test_get_file_hash_sha256 PASSED
tests/test_agent.py::TestFileOperations::test_get_file_hash_consistent PASSED
tests/test_agent.py::TestWatchlistLoading::test_load_tickers_basic PASSED
tests/test_agent.py::TestWatchlistLoading::test_load_tickers_deduplicates PASSED
tests/test_agent.py::TestCacheValidation::test_local_file_is_valid_existing_file PASSED
tests/test_agent.py::TestDownloadLogic::test_download_ticker_success PASSED
tests/test_agent.py::TestDownloadLogic::test_download_ticker_rate_limit_error PASSED
tests/test_agent.py::TestDataRecording::test_record_result_success PASSED
tests/test_agent.py::TestIntegration::test_full_download_pipeline PASSED
tests/test_agent.py::TestEdgeCases::test_very_large_watchlist PASSED
tests/test_agent.py::TestPerformance::test_checkpoint_load_speed PASSED

======================== 120 passed in 5.23s ========================
```

## Support

For test issues:
1. Check `pytest.ini` configuration
2. Review fixture definitions
3. Ensure mocks are set up correctly
4. Check Python version compatibility (3.8+)
5. Verify all dependencies installed: `pip install -r requirements.txt`

Useful pytest options:
- `-v`: Verbose output
- `-s`: Show print statements
- `-x`: Stop on first failure
- `--tb=short`: Shorter traceback
- `--ff`: Run last failed first
- `--lf`: Run last failed only
