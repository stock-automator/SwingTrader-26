"""Tests for `scripts/update_watchlist.py`. Every network call
(`pandas.read_html`, `yfinance.download`) is mocked - no test here touches
the network."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import scripts.update_watchlist as uw  # noqa: E402


class TestFindTickerColumn:
    def test_finds_table_with_symbol_column(self):
        decoy = pd.DataFrame({"Company": ["Foo"], "Other": [1]})
        real = pd.DataFrame({"Symbol": ["AAPL", "MSFT"], "Company": ["Apple", "MS"]})
        table, col = uw._find_ticker_column([decoy, real], "test")
        assert col == "Symbol"
        assert list(table[col]) == ["AAPL", "MSFT"]

    def test_finds_table_with_ticker_column_case_insensitive(self):
        real = pd.DataFrame({"TICKER": ["AAPL"], "Company": ["Apple"]})
        table, col = uw._find_ticker_column([real], "test")
        assert col == "TICKER"

    def test_raises_when_no_table_has_a_ticker_column(self):
        decoy = pd.DataFrame({"Company": ["Foo"]})
        with pytest.raises(ValueError, match="no table"):
            uw._find_ticker_column([decoy], "test")


class TestFetchIndexConstituents:
    def test_normalizes_tickers_and_dot_dash(self, monkeypatch):
        table = pd.DataFrame({"Symbol": ["aapl", "BRK.B", " msft "]})
        monkeypatch.setattr(uw.pd, "read_html", lambda url: [table])
        result = uw.fetch_index_constituents("http://example.com", "test")
        assert result == ["AAPL", "BRK-B", "MSFT"]


class TestMergeAndDedupe:
    def test_merges_overlapping_lists_case_insensitively(self):
        result = uw.merge_and_dedupe(["AAPL", "MSFT"], ["MSFT", "GOOG"])
        assert result == ["AAPL", "GOOG", "MSFT"]

    def test_empty_inputs_produce_empty_output(self):
        assert uw.merge_and_dedupe([], []) == []

    def test_drops_falsy_entries(self):
        assert uw.merge_and_dedupe(["AAPL", "", None]) == ["AAPL"]


class TestFilterIlliquid:
    def test_drops_illiquid_keeps_liquid(self, monkeypatch):
        index = pd.date_range("2024-01-02", periods=25, freq="B")

        def _fake_download(tickers, **kwargs):
            frames = {}
            frames["LIQUID"] = pd.DataFrame({"Volume": [5_000_000] * 25}, index=index)
            frames["THIN"] = pd.DataFrame({"Volume": [10_000] * 25}, index=index)
            return pd.concat(frames, axis=1)

        import yfinance as yf

        monkeypatch.setattr(yf, "download", _fake_download)

        kept, dropped = uw.filter_illiquid(["LIQUID", "THIN"])
        assert kept == ["LIQUID"]
        assert dropped == ["THIN"]

    def test_missing_ticker_data_is_dropped_with_warning(self, monkeypatch, caplog):
        index = pd.date_range("2024-01-02", periods=25, freq="B")

        def _fake_download(tickers, **kwargs):
            return pd.concat(
                {"LIQUID": pd.DataFrame({"Volume": [5_000_000] * 25}, index=index)},
                axis=1,
            )

        import yfinance as yf

        monkeypatch.setattr(yf, "download", _fake_download)

        with caplog.at_level("WARNING"):
            kept, dropped = uw.filter_illiquid(["LIQUID", "MISSING"])

        assert kept == ["LIQUID"]
        assert dropped == ["MISSING"]

    def test_empty_ticker_list_short_circuits(self):
        assert uw.filter_illiquid([]) == ([], [])

    def test_single_ticker_uses_unflattened_columns(self, monkeypatch):
        index = pd.date_range("2024-01-02", periods=25, freq="B")

        def _fake_download(tickers, **kwargs):
            return pd.DataFrame({"Volume": [2_000_000] * 25}, index=index)

        import yfinance as yf

        monkeypatch.setattr(yf, "download", _fake_download)

        kept, dropped = uw.filter_illiquid(["SOLO"])
        assert kept == ["SOLO"]
        assert dropped == []


class TestReadExistingAndWriteAtomic:
    def test_preserves_leading_comment_header(self, tmp_path):
        path = tmp_path / "watchlist.txt"
        path.write_text("# header line 1\n# header line 2\n\nAAPL\nMSFT\n")

        header, tickers = uw.read_existing(path)
        assert header == ["# header line 1", "# header line 2", ""]
        assert tickers == ["AAPL", "MSFT"]

    def test_missing_file_returns_empty(self, tmp_path):
        header, tickers = uw.read_existing(tmp_path / "does_not_exist.txt")
        assert header == []
        assert tickers == []

    def test_write_atomic_roundtrips_and_leaves_no_tmp_file(self, tmp_path):
        path = tmp_path / "watchlist.txt"
        uw.write_atomic(path, ["# a comment"], ["AAPL", "MSFT"])

        header, tickers = uw.read_existing(path)
        assert header == ["# a comment"]
        assert tickers == ["AAPL", "MSFT"]
        assert not path.with_name(path.name + ".tmp").exists()


class TestSync:
    def _patch_sources(
        self,
        monkeypatch,
        sp500=None,
        nasdaq100=None,
        sp500_error=None,
        nasdaq_error=None,
    ):
        def _sp500():
            if sp500_error:
                raise sp500_error
            return sp500 or []

        def _nasdaq():
            if nasdaq_error:
                raise nasdaq_error
            return nasdaq100 or []

        monkeypatch.setattr(uw, "fetch_sp500", _sp500)
        monkeypatch.setattr(uw, "fetch_nasdaq100", _nasdaq)
        monkeypatch.setattr(
            uw, "filter_illiquid", lambda tickers, *a, **k: (tickers, [])
        )

    def test_both_sources_failing_returns_nonzero(self, monkeypatch, tmp_path):
        self._patch_sources(
            monkeypatch,
            sp500_error=RuntimeError("down"),
            nasdaq_error=RuntimeError("down"),
        )
        code = uw.sync(output=tmp_path / "watchlist.txt")
        assert code == 1

    def test_one_source_failing_still_succeeds(self, monkeypatch, tmp_path):
        self._patch_sources(
            monkeypatch, sp500=["AAPL", "MSFT"], nasdaq_error=RuntimeError("down")
        )
        code = uw.sync(output=tmp_path / "watchlist.txt")
        assert code == 0
        _, tickers = uw.read_existing(tmp_path / "watchlist.txt")
        assert tickers == ["AAPL", "MSFT"]

    def test_dry_run_does_not_write(self, monkeypatch, tmp_path):
        self._patch_sources(monkeypatch, sp500=["AAPL", "MSFT"])
        output = tmp_path / "watchlist.txt"
        code = uw.sync(output=output, dry_run=True)
        assert code == 0
        assert not output.exists()

    def test_no_changes_does_not_rewrite_file(self, monkeypatch, tmp_path):
        output = tmp_path / "watchlist.txt"
        output.write_text("AAPL\nMSFT\n")
        self._patch_sources(monkeypatch, sp500=["AAPL", "MSFT"])

        before = output.stat().st_mtime_ns
        code = uw.sync(output=output)
        assert code == 0
        assert output.stat().st_mtime_ns == before

    def test_writes_when_tickers_change(self, monkeypatch, tmp_path):
        output = tmp_path / "watchlist.txt"
        output.write_text("# keep me\nAAPL\n")
        self._patch_sources(monkeypatch, sp500=["AAPL", "MSFT"])

        code = uw.sync(output=output)
        assert code == 0
        header, tickers = uw.read_existing(output)
        assert header == ["# keep me"]
        assert tickers == ["AAPL", "MSFT"]
