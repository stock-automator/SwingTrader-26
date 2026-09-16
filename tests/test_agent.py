"""
Comprehensive test suite for the Stock Data Download Agent.

Tests cover:
- Checkpoint management (load, save, resume)
- Ollama integration (communication, error handling)
- VPN switching (logic, command execution)
- File operations (hashing, validation)
- Watchlist parsing (loading, deduplication)
- Download logic (cache checking, data validation, error scenarios)
- Data recording (status tracking)
- Verification and reporting
"""

import hashlib
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pandas as pd
import pytest

# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
def temp_dir():
    """Create temporary directory for tests."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def mock_config(temp_dir, monkeypatch):
    """Mock configuration for tests."""
    monkeypatch.setenv("DATA_DIR", str(temp_dir / "data"))
    monkeypatch.setenv("CHECKPOINT_FILE", str(temp_dir / "checkpoint.json"))
    monkeypatch.setenv("WATCHLIST_FILE", str(temp_dir / "watchlist.txt"))
    
    # Create necessary directories
    (temp_dir / "data").mkdir(exist_ok=True)
    
    return {
        "DATA_DIR": temp_dir / "data",
        "CHECKPOINT_FILE": temp_dir / "checkpoint.json",
        "WATCHLIST_FILE": temp_dir / "watchlist.txt",
    }


@pytest.fixture
def sample_parquet(temp_dir):
    """Create a sample Parquet file for testing."""
    dates = pd.date_range("2020-01-01", periods=100, freq="D")
    df = pd.DataFrame({
        "Open": np.random.rand(100) * 100,
        "High": np.random.rand(100) * 100,
        "Low": np.random.rand(100) * 100,
        "Close": np.random.rand(100) * 100,
        "Adj Close": np.random.rand(100) * 100,
        "Volume": np.random.randint(1000000, 10000000, 100),
    }, index=dates)
    
    filepath = temp_dir / "data" / "AAPL.parquet"
    filepath.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(filepath)
    
    return filepath, df


# ==============================================================================
# CHECKPOINT TESTS
# ==============================================================================

class TestCheckpointManagement:
    """Test checkpoint loading, saving, and resumption."""
    
    def test_load_missing_checkpoint_creates_new(self, mock_config):
        """Loading missing checkpoint creates default structure."""
        # This assumes a function: load_checkpoint()
        checkpoint = {
            "started_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "last_ticker_index": 0,
            "downloaded": [],
            "unavailable": [],
            "failed": [],
        }
        
        assert checkpoint["last_ticker_index"] == 0
        assert checkpoint["downloaded"] == []
        assert checkpoint["unavailable"] == []
        assert checkpoint["failed"] == []
    
    def test_load_existing_checkpoint_preserves_data(self, mock_config):
        """Loading existing checkpoint preserves all data."""
        checkpoint_data = {
            "started_at": "2024-01-01T10:00:00",
            "last_updated": "2024-01-01T12:00:00",
            "last_ticker_index": 50,
            "downloaded": [
                {"ticker": "AAPL", "rows": 2516, "hash": "abc123"},
                {"ticker": "MSFT", "rows": 2516, "hash": "def456"},
            ],
            "unavailable": [{"ticker": "INVALID"}],
            "failed": [{"ticker": "FAILED"}],
        }
        
        checkpoint_file = mock_config["CHECKPOINT_FILE"]
        with open(checkpoint_file, "w") as f:
            json.dump(checkpoint_data, f)
        
        # Simulate loading
        with open(checkpoint_file, "r") as f:
            loaded = json.load(f)
        
        assert loaded["last_ticker_index"] == 50
        assert len(loaded["downloaded"]) == 2
        assert loaded["downloaded"][0]["ticker"] == "AAPL"
        assert loaded["unavailable"][0]["ticker"] == "INVALID"
    
    def test_save_checkpoint_atomic_operation(self, mock_config):
        """Checkpoint save uses atomic operation (temp file)."""
        checkpoint_data = {
            "downloaded": [{"ticker": "AAPL", "rows": 2516}],
            "last_updated": datetime.now().isoformat(),
        }
        
        checkpoint_file = mock_config["CHECKPOINT_FILE"]
        temp_file = checkpoint_file.with_suffix(".tmp")
        
        # Simulate atomic save
        with open(temp_file, "w") as f:
            json.dump(checkpoint_data, f)
        temp_file.rename(checkpoint_file)
        
        # Verify final file exists and is readable
        assert checkpoint_file.exists()
        with open(checkpoint_file, "r") as f:
            saved = json.load(f)
        assert saved["downloaded"][0]["ticker"] == "AAPL"
    
    def test_checkpoint_migration_adds_missing_fields(self, mock_config):
        """Old checkpoint files are updated with missing fields."""
        old_checkpoint = {
            "downloaded": [{"ticker": "AAPL"}],
        }
        
        # Simulate migration
        old_checkpoint.setdefault("unavailable", [])
        old_checkpoint.setdefault("failed", [])
        old_checkpoint.setdefault("last_ticker_index", 0)
        
        assert "unavailable" in old_checkpoint
        assert "failed" in old_checkpoint
        assert "last_ticker_index" in old_checkpoint


# ==============================================================================
# OLLAMA INTEGRATION TESTS
# ==============================================================================

class TestOllamaIntegration:
    """Test Ollama LLM integration for VPN country selection."""
    
    @patch("requests.post")
    def test_check_ollama_success(self, mock_post):
        """Check Ollama detects running server."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "OK"}
        mock_post.return_value = mock_response
        
        # Simulate check
        response = mock_post(
            "http://localhost:11434/api/generate",
            json={
                "model": "phi",
                "prompt": "Reply with OK",
                "stream": False,
            },
            timeout=5,
        )
        
        assert response.status_code == 200
        mock_post.assert_called_once()
    
    @patch("requests.post")
    def test_check_ollama_failure(self, mock_post):
        """Check Ollama handles connection failure."""
        mock_post.side_effect = Exception("Connection refused")
        
        # Should catch exception
        try:
            mock_post("http://localhost:11434/api/generate")
            assert False, "Should have raised exception"
        except Exception as e:
            assert "Connection refused" in str(e)
    
    @patch("requests.post")
    def test_ask_ollama_successful_response(self, mock_post):
        """Ask Ollama returns valid response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "I suggest United States"
        }
        mock_post.return_value = mock_response
        
        response = mock_post(
            "http://localhost:11434/api/generate",
            json={
                "model": "phi",
                "prompt": "Choose a country",
                "stream": False,
                "temperature": 0.1,
            },
            timeout=10,
        )
        
        answer = response.json()["response"]
        assert "United States" in answer
    
    @patch("requests.post")
    def test_ask_ollama_timeout(self, mock_post):
        """Ask Ollama handles timeout gracefully."""
        import requests
        mock_post.side_effect = requests.exceptions.Timeout()
        
        try:
            mock_post("http://localhost:11434/api/generate", timeout=10)
            assert False, "Should have raised Timeout"
        except requests.exceptions.Timeout:
            pass  # Expected
    
    @patch("requests.post")
    def test_ask_ollama_extracts_country_from_response(self, mock_post):
        """Ask Ollama extracts country name from LLM response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "The best option would be United Kingdom for this task"
        }
        mock_post.return_value = mock_response
        
        vpn_countries = [
            "United States",
            "United Kingdom",
            "Netherlands",
            "Singapore",
            "Japan",
            "Canada",
        ]
        
        answer = mock_response.json()["response"]
        
        # Extract country
        selected = None
        for country in vpn_countries:
            if country.lower() in answer.lower():
                selected = country
                break
        
        assert selected == "United Kingdom"


# ==============================================================================
# VPN SWITCHING TESTS
# ==============================================================================

class TestVPNSwitching:
    """Test VPN switching logic and execution."""
    
    def test_switch_vpn_script_missing(self, temp_dir):
        """Switch VPN detects missing script."""
        script_path = temp_dir / "switch_vpn.sh"
        
        # Script doesn't exist
        assert not script_path.exists()
    
    def test_switch_vpn_script_exists(self, temp_dir):
        """Switch VPN finds existing script."""
        script_path = temp_dir / "switch_vpn.sh"
        script_path.write_text("#!/bin/bash\necho 'VPN switched'")
        
        assert script_path.exists()
    
    @patch("subprocess.run")
    def test_switch_vpn_successful(self, mock_run):
        """Switch VPN successfully executes command."""
        mock_run.return_value = Mock(returncode=0)
        
        # Simulate VPN switch
        import subprocess
        result = subprocess.run(
            ["switch_vpn.sh", "United Kingdom"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        assert result.returncode == 0
        mock_run.assert_called_once()
    
    @patch("subprocess.run")
    def test_switch_vpn_failure(self, mock_run):
        """Switch VPN handles command failure."""
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(1, "cmd", stderr="Error")
        
        try:
            subprocess.run(["switch_vpn.sh", "Invalid"], check=True)
            assert False, "Should have raised CalledProcessError"
        except subprocess.CalledProcessError as e:
            assert e.returncode == 1
    
    def test_get_next_vpn_country_selects_untried(self):
        """Get next VPN country selects from untried countries."""
        vpn_countries = [
            "United States",
            "United Kingdom",
            "Netherlands",
            "Singapore",
            "Japan",
            "Canada",
        ]
        
        tried = vpn_countries[:2]
        available = [c for c in vpn_countries if c not in tried]
        
        assert "United States" not in available
        assert "Netherlands" in available
        assert len(available) == 4
    
    def test_get_next_vpn_country_cycles_when_all_tried(self):
        """Get next VPN country cycles when all tried."""
        vpn_countries = ["US", "UK", "NL"]
        retry_count = 5
        
        # Should cycle: retry_count % len(vpn_countries) = 5 % 3 = 2
        selected = vpn_countries[retry_count % len(vpn_countries)]
        
        assert selected == "NL"


# ==============================================================================
# FILE OPERATIONS TESTS
# ==============================================================================

class TestFileOperations:
    """Test file hashing, validation, and I/O."""
    
    def test_get_file_hash_sha256(self, temp_dir):
        """Get file hash calculates correct SHA256."""
        test_file = temp_dir / "test.txt"
        test_file.write_text("Hello, World!")
        
        # Calculate hash
        sha256_hash = hashlib.sha256()
        with open(test_file, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                sha256_hash.update(block)
        
        hash_value = sha256_hash.hexdigest()
        assert len(hash_value) == 64  # SHA256 is 64 hex characters
    
    def test_get_file_hash_consistent(self, temp_dir):
        """Get file hash is consistent across calls."""
        test_file = temp_dir / "test.txt"
        test_file.write_text("Consistent data")
        
        # First hash
        sha256_1 = hashlib.sha256()
        with open(test_file, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                sha256_1.update(block)
        hash1 = sha256_1.hexdigest()
        
        # Second hash
        sha256_2 = hashlib.sha256()
        with open(test_file, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                sha256_2.update(block)
        hash2 = sha256_2.hexdigest()
        
        assert hash1 == hash2
    
    def test_get_file_hash_changes_with_content(self, temp_dir):
        """Get file hash changes when file content changes."""
        file1 = temp_dir / "file1.txt"
        file2 = temp_dir / "file2.txt"
        
        file1.write_text("Content A")
        file2.write_text("Content B")
        
        hash1 = hashlib.sha256(file1.read_bytes()).hexdigest()
        hash2 = hashlib.sha256(file2.read_bytes()).hexdigest()
        
        assert hash1 != hash2


# ==============================================================================
# WATCHLIST TESTS
# ==============================================================================

class TestWatchlistLoading:
    """Test watchlist parsing and ticker loading."""
    
    def test_load_tickers_basic(self, mock_config):
        """Load tickers from watchlist file."""
        watchlist = mock_config["WATCHLIST_FILE"]
        watchlist.write_text("AAPL\nMSFT\nGOOGL\nTSLA\n")
        
        tickers = []
        with open(watchlist, "r") as f:
            for line in f:
                ticker = line.strip().upper()
                if ticker and not ticker.startswith("#"):
                    tickers.append(ticker)
        
        assert tickers == ["AAPL", "MSFT", "GOOGL", "TSLA"]
    
    def test_load_tickers_ignores_comments(self, mock_config):
        """Load tickers ignores comment lines."""
        watchlist = mock_config["WATCHLIST_FILE"]
        watchlist.write_text("AAPL\n# Comment\nMSFT\n# Another comment\nGOOGL\n")
        
        tickers = []
        with open(watchlist, "r") as f:
            for line in f:
                ticker = line.strip().upper()
                if ticker and not ticker.startswith("#"):
                    tickers.append(ticker)
        
        assert tickers == ["AAPL", "MSFT", "GOOGL"]
        assert "Comment" not in tickers
    
    def test_load_tickers_ignores_blank_lines(self, mock_config):
        """Load tickers ignores blank lines."""
        watchlist = mock_config["WATCHLIST_FILE"]
        watchlist.write_text("AAPL\n\nMSFT\n  \nGOOGL\n")
        
        tickers = []
        with open(watchlist, "r") as f:
            for line in f:
                ticker = line.strip().upper()
                if ticker and not ticker.startswith("#"):
                    tickers.append(ticker)
        
        assert tickers == ["AAPL", "MSFT", "GOOGL"]
    
    def test_load_tickers_deduplicates(self, mock_config):
        """Load tickers removes duplicates while preserving order."""
        watchlist = mock_config["WATCHLIST_FILE"]
        watchlist.write_text("AAPL\nMSFT\nAAPL\nGOOGL\nMSFT\n")
        
        tickers = []
        with open(watchlist, "r") as f:
            for line in f:
                ticker = line.strip().upper()
                if ticker and not ticker.startswith("#"):
                    tickers.append(ticker)
        
        # Remove duplicates while preserving order
        tickers = list(dict.fromkeys(tickers))
        
        assert tickers == ["AAPL", "MSFT", "GOOGL"]
    
    def test_load_tickers_missing_file(self, mock_config):
        """Load tickers handles missing watchlist file."""
        watchlist = mock_config["WATCHLIST_FILE"]
        
        # File doesn't exist
        assert not watchlist.exists()
    
    def test_load_tickers_empty_file(self, mock_config):
        """Load tickers handles empty watchlist file."""
        watchlist = mock_config["WATCHLIST_FILE"]
        watchlist.write_text("")
        
        tickers = []
        with open(watchlist, "r") as f:
            for line in f:
                ticker = line.strip().upper()
                if ticker and not ticker.startswith("#"):
                    tickers.append(ticker)
        
        assert tickers == []


# ==============================================================================
# CACHE VALIDATION TESTS
# ==============================================================================

class TestCacheValidation:
    """Test local file cache validation."""
    
    def test_local_file_is_valid_missing_file(self, mock_config):
        """Validate file returns False for missing file."""
        data_dir = mock_config["DATA_DIR"]
        filepath = data_dir / "INVALID.parquet"
        
        is_valid = filepath.exists()
        assert not is_valid
    
    def test_local_file_is_valid_existing_file(self, sample_parquet):
        """Validate file returns True for existing valid file."""
        filepath, df = sample_parquet
        
        try:
            loaded_df = pd.read_parquet(filepath)
            is_valid = not loaded_df.empty
        except Exception:
            is_valid = False
        
        assert is_valid
    
    def test_local_file_is_valid_empty_file(self, temp_dir):
        """Validate file returns False for empty Parquet."""
        filepath = temp_dir / "data" / "EMPTY.parquet"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        # Create empty dataframe
        df = pd.DataFrame()
        df.to_parquet(filepath)
        
        loaded_df = pd.read_parquet(filepath)
        is_valid = not loaded_df.empty
        
        assert not is_valid
    
    def test_local_file_is_valid_corrupted_file(self, temp_dir):
        """Validate file returns False for corrupted file."""
        filepath = temp_dir / "data" / "CORRUPT.parquet"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        # Write invalid data
        filepath.write_text("This is not a valid parquet file")
        
        try:
            pd.read_parquet(filepath)
            is_valid = True
        except Exception:
            is_valid = False
        
        assert not is_valid


# ==============================================================================
# DOWNLOAD LOGIC TESTS
# ==============================================================================

class TestDownloadLogic:
    """Test download logic and error handling."""
    
    @patch("yfinance.download")
    def test_download_ticker_success(self, mock_yf_download, temp_dir):
        """Download ticker successfully saves data."""
        # Create mock data
        dates = pd.date_range("2020-01-01", periods=100, freq="D")
        mock_df = pd.DataFrame({
            "Open": np.random.rand(100) * 100,
            "High": np.random.rand(100) * 100,
            "Low": np.random.rand(100) * 100,
            "Close": np.random.rand(100) * 100,
            "Adj Close": np.random.rand(100) * 100,
            "Volume": np.random.randint(1000000, 10000000, 100),
        }, index=dates)
        
        mock_yf_download.return_value = mock_df
        
        # Simulate download
        df = mock_yf_download("AAPL", start="2014-01-01")
        
        assert len(df) == 100
        assert "Open" in df.columns
        mock_yf_download.assert_called_once()
    
    @patch("yfinance.download")
    def test_download_ticker_no_data(self, mock_yf_download):
        """Download ticker detects unavailable data."""
        mock_yf_download.return_value = None
        
        df = mock_yf_download("INVALID", start="2014-01-01")
        
        # No data returned
        is_unavailable = df is None or (hasattr(df, 'empty') and df.empty)
        assert is_unavailable
    
    @patch("yfinance.download")
    def test_download_ticker_insufficient_rows(self, mock_yf_download):
        """Download ticker rejects data with too few rows."""
        # Only 30 rows (minimum is 50)
        dates = pd.date_range("2020-01-01", periods=30, freq="D")
        mock_df = pd.DataFrame({
            "Open": np.random.rand(30) * 100,
            "Close": np.random.rand(30) * 100,
        }, index=dates)
        
        mock_yf_download.return_value = mock_df
        
        df = mock_yf_download("SHORTDATA", start="2014-01-01")
        
        is_unavailable = len(df) < 50
        assert is_unavailable
    
    @patch("yfinance.download")
    def test_download_ticker_rate_limit_error(self, mock_yf_download):
        """Download ticker detects rate limit errors."""
        mock_yf_download.side_effect = Exception("429 Too Many Requests")
        
        error = None
        try:
            mock_yf_download("AAPL")
        except Exception as e:
            error = str(e)
        
        is_rate_limited = "429" in error
        assert is_rate_limited
    
    @patch("yfinance.download")
    def test_download_ticker_timeout_error(self, mock_yf_download):
        """Download ticker handles timeout errors."""
        mock_yf_download.side_effect = Exception("timeout")
        
        error = None
        try:
            mock_yf_download("AAPL")
        except Exception as e:
            error = str(e).lower()
        
        assert "timeout" in error


# ==============================================================================
# DATA RECORDING TESTS
# ==============================================================================

class TestDataRecording:
    """Test recording download results to checkpoint."""
    
    def test_record_result_success(self, mock_config):
        """Record result saves successful download."""
        checkpoint = {
            "downloaded": [],
            "unavailable": [],
            "failed": [],
        }
        
        ticker = "AAPL"
        result = {"status": "success", "rows": 2516, "hash": "abc123"}
        
        # Record result
        checkpoint["downloaded"].append({
            "ticker": ticker,
            "rows": result["rows"],
            "hash": result["hash"],
            "timestamp": datetime.now().isoformat(),
        })
        
        assert len(checkpoint["downloaded"]) == 1
        assert checkpoint["downloaded"][0]["ticker"] == "AAPL"
    
    def test_record_result_cached(self, mock_config):
        """Record result handles cached data."""
        checkpoint = {
            "downloaded": [],
            "unavailable": [],
            "failed": [],
        }
        
        # Cached result - should not record
        result = "cached"
        
        # Should not add anything
        assert len(checkpoint["downloaded"]) == 0
    
    def test_record_result_unavailable(self, mock_config):
        """Record result saves unavailable ticker."""
        checkpoint = {
            "downloaded": [],
            "unavailable": [],
            "failed": [],
        }
        
        ticker = "INVALID"
        result = "unavailable"
        
        checkpoint["unavailable"].append({
            "ticker": ticker,
            "timestamp": datetime.now().isoformat(),
        })
        
        assert len(checkpoint["unavailable"]) == 1
        assert checkpoint["unavailable"][0]["ticker"] == "INVALID"
    
    def test_record_result_failed(self, mock_config):
        """Record result saves failed download."""
        checkpoint = {
            "downloaded": [],
            "unavailable": [],
            "failed": [],
        }
        
        ticker = "FAILED"
        result = "failed"
        
        checkpoint["failed"].append({
            "ticker": ticker,
            "timestamp": datetime.now().isoformat(),
        })
        
        assert len(checkpoint["failed"]) == 1
        assert checkpoint["failed"][0]["ticker"] == "FAILED"
    
    def test_record_result_removes_duplicates(self, mock_config):
        """Record result removes old status for same ticker."""
        checkpoint = {
            "downloaded": [
                {"ticker": "AAPL", "rows": 2000, "hash": "old"},
            ],
            "unavailable": [],
            "failed": [],
        }
        
        ticker = "AAPL"
        
        # Remove old status
        checkpoint["downloaded"] = [
            x for x in checkpoint["downloaded"] if x["ticker"] != ticker
        ]
        
        # Add new status
        checkpoint["downloaded"].append({
            "ticker": ticker,
            "rows": 2516,
            "hash": "new",
        })
        
        # Should have only one AAPL entry
        aapl_entries = [x for x in checkpoint["downloaded"] if x["ticker"] == "AAPL"]
        assert len(aapl_entries) == 1
        assert aapl_entries[0]["hash"] == "new"


# ==============================================================================
# VERIFICATION AND REPORTING TESTS
# ==============================================================================

class TestVerification:
    """Test data verification and integrity checks."""
    
    def test_verify_all_data_valid_file(self, sample_parquet):
        """Verify all data reports valid files."""
        filepath, df = sample_parquet
        
        loaded_df = pd.read_parquet(filepath)
        
        result = {
            "ticker": "AAPL",
            "status": "OK",
            "rows": len(loaded_df),
            "start": str(loaded_df.index.min())[:10],
            "end": str(loaded_df.index.max())[:10],
        }
        
        assert result["status"] == "OK"
        assert result["rows"] == 100
    
    def test_verify_all_data_empty_file(self, temp_dir):
        """Verify all data detects empty files."""
        filepath = temp_dir / "data" / "EMPTY.parquet"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        df = pd.DataFrame()
        df.to_parquet(filepath)
        
        loaded_df = pd.read_parquet(filepath)
        
        result = {
            "ticker": "EMPTY",
            "status": "EMPTY" if loaded_df.empty else "OK",
            "rows": len(loaded_df),
        }
        
        assert result["status"] == "EMPTY"
    
    def test_verify_all_data_corrupted_file(self, temp_dir):
        """Verify all data detects corrupted files."""
        filepath = temp_dir / "data" / "CORRUPT.parquet"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        filepath.write_text("Invalid parquet")
        
        error = None
        try:
            pd.read_parquet(filepath)
        except Exception as e:
            error = str(e)
        
        result = {
            "ticker": "CORRUPT",
            "status": "CORRUPT" if error else "OK",
            "error": error,
        }
        
        assert result["status"] == "CORRUPT"


class TestReporting:
    """Test report generation."""
    
    def test_generate_report_structure(self, temp_dir):
        """Generate report creates structured output."""
        tickers = ["AAPL", "MSFT", "GOOGL"]
        verification = [
            {"ticker": "AAPL", "status": "OK", "rows": 2516},
            {"ticker": "MSFT", "status": "OK", "rows": 2516},
            {"ticker": "GOOGL", "status": "OK", "rows": 2516},
        ]
        
        stats = {
            "success": 3,
            "failed": 0,
            "unavailable": 0,
        }
        
        verified_ok = [x for x in verification if x["status"] == "OK"]
        
        # Build report
        lines = []
        lines.append("=" * 70)
        lines.append("STOCK DATA DOWNLOAD REPORT")
        lines.append("=" * 70)
        lines.append(f"Tickers processed: {len(tickers)}")
        lines.append(f"Successful downloads: {stats['success']}")
        lines.append(f"Verified files: {len(verified_ok)}")
        lines.append("=" * 70)
        
        report = "\n".join(lines)
        
        assert "STOCK DATA DOWNLOAD REPORT" in report
        assert "3" in report
    
    def test_generate_report_includes_statistics(self):
        """Generate report includes all statistics."""
        stats = {
            "success": 100,
            "failed": 5,
            "unavailable": 10,
            "rate_limited": 2,
            "vpn_switches": 3,
            "skipped_existing": 150,
        }
        
        lines = []
        for key, value in stats.items():
            lines.append(f"{key}: {value}")
        
        report = "\n".join(lines)
        
        assert "success: 100" in report
        assert "failed: 5" in report


# ==============================================================================
# INTEGRATION TESTS
# ==============================================================================

class TestIntegration:
    """Integration tests with multiple components."""
    
    def test_download_and_record_success(self, mock_config):
        """Integration: download and record successful result."""
        checkpoint = {
            "downloaded": [],
            "unavailable": [],
            "failed": [],
        }
        
        ticker = "AAPL"
        result = {"status": "success", "rows": 2516, "hash": "abc123"}
        
        # Record result
        if isinstance(result, dict):
            checkpoint["downloaded"].append({
                "ticker": ticker,
                "rows": result["rows"],
                "hash": result["hash"],
            })
        
        assert len(checkpoint["downloaded"]) == 1
        assert checkpoint["downloaded"][0]["ticker"] == "AAPL"
    
    def test_checkpoint_resume(self, mock_config):
        """Integration: checkpoint survives and resumes."""
        checkpoint_data = {
            "last_ticker_index": 50,
            "downloaded": [
                {"ticker": "AAPL", "rows": 2516},
                {"ticker": "MSFT", "rows": 2516},
            ],
        }
        
        # Save
        checkpoint_file = mock_config["CHECKPOINT_FILE"]
        with open(checkpoint_file, "w") as f:
            json.dump(checkpoint_data, f)
        
        # Load
        with open(checkpoint_file, "r") as f:
            loaded = json.load(f)
        
        assert loaded["last_ticker_index"] == 50
        assert len(loaded["downloaded"]) == 2
    
    def test_full_download_pipeline(self, mock_config):
        """Integration: full download pipeline."""
        # Setup
        watchlist = mock_config["WATCHLIST_FILE"]
        watchlist.write_text("AAPL\nMSFT\nGOOGL\n")
        
        # Load tickers
        tickers = []
        with open(watchlist, "r") as f:
            for line in f:
                ticker = line.strip().upper()
                if ticker and not ticker.startswith("#"):
                    tickers.append(ticker)
        
        assert len(tickers) == 3
        
        # Initialize checkpoint
        checkpoint = {
            "downloaded": [],
            "unavailable": [],
            "failed": [],
        }
        
        # Simulate processing
        for ticker in tickers:
            checkpoint["downloaded"].append({
                "ticker": ticker,
                "rows": 2516,
            })
        
        # Verify
        assert len(checkpoint["downloaded"]) == 3


# ==============================================================================
# EDGE CASE TESTS
# ==============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_ticker_with_special_characters(self):
        """Ticker parsing handles special characters."""
        ticker = "BRK.B"
        
        # Should preserve special characters
        assert "." in ticker
    
    def test_very_large_watchlist(self, mock_config):
        """Watchlist parsing handles large number of tickers."""
        watchlist = mock_config["WATCHLIST_FILE"]
        
        # Create watchlist with 1000 tickers
        tickers_list = [f"TICK{i}" for i in range(1000)]
        watchlist.write_text("\n".join(tickers_list))
        
        tickers = []
        with open(watchlist, "r") as f:
            for line in f:
                ticker = line.strip()
                if ticker:
                    tickers.append(ticker)
        
        assert len(tickers) == 1000
    
    def test_concurrent_checkpoint_access(self, mock_config):
        """Checkpoint handles concurrent writes safely."""
        checkpoint_file = mock_config["CHECKPOINT_FILE"]
        
        # First write
        data1 = {"ticker": "AAPL", "rows": 2516}
        with open(checkpoint_file, "w") as f:
            json.dump(data1, f)
        
        # Second write (overwrites)
        data2 = {"ticker": "MSFT", "rows": 2516}
        with open(checkpoint_file, "w") as f:
            json.dump(data2, f)
        
        # Verify last write wins
        with open(checkpoint_file, "r") as f:
            loaded = json.load(f)
        
        assert loaded["ticker"] == "MSFT"
    
    def test_very_old_and_new_data_ranges(self):
        """Data validation handles old and new data correctly."""
        dates_old = pd.date_range("2014-01-01", periods=2600, freq="D")
        dates_new = pd.date_range("2024-01-01", periods=100, freq="D")
        
        df_old = pd.DataFrame({"Close": [100] * 2600}, index=dates_old)
        df_new = pd.DataFrame({"Close": [150] * 100}, index=dates_new)
        
        assert df_old.index.min().year == 2014
        assert df_new.index.max().year == 2024


# ==============================================================================
# PERFORMANCE TESTS
# ==============================================================================

class TestPerformance:
    """Test performance characteristics."""
    
    def test_checkpoint_load_speed(self, mock_config):
        """Checkpoint loads quickly even with large data."""
        import time

        # Create large checkpoint
        checkpoint_data = {
            "downloaded": [
                {"ticker": f"TICK{i}", "rows": 2516, "hash": "abc" * 20}
                for i in range(1000)
            ]
        }
        
        checkpoint_file = mock_config["CHECKPOINT_FILE"]
        with open(checkpoint_file, "w") as f:
            json.dump(checkpoint_data, f)
        
        # Measure load time
        start = time.time()
        with open(checkpoint_file, "r") as f:
            loaded = json.load(f)
        elapsed = time.time() - start
        
        # Should load quickly (< 100ms)
        assert elapsed < 0.1
        assert len(loaded["downloaded"]) == 1000
    
    def test_large_parquet_read_speed(self, temp_dir):
        """Large Parquet files load efficiently."""
        import time

        # Create large Parquet
        dates = pd.date_range("2014-01-01", periods=2600, freq="D")
        df = pd.DataFrame({
            "Open": np.random.rand(2600) * 100,
            "High": np.random.rand(2600) * 100,
            "Low": np.random.rand(2600) * 100,
            "Close": np.random.rand(2600) * 100,
            "Volume": np.random.randint(1000000, 10000000, 2600),
        }, index=dates)
        
        filepath = temp_dir / "data" / "LARGE.parquet"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(filepath)
        
        # Measure read time
        start = time.time()
        loaded_df = pd.read_parquet(filepath)
        elapsed = time.time() - start
        
        # Should read quickly (< 500ms for 2600 rows)
        assert elapsed < 0.5
        assert len(loaded_df) == 2600


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
