"""Tests for `/api/v1/journal/*` - always against a tmp_path journal file,
never `data/trades_live.csv`."""

from datetime import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.data.loader import DataUnavailableError
from backend.app.journal.executor import TradeJournal
from backend.app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def journal_settings(tmp_path):
    settings = Settings(journal_path=tmp_path / "journal.csv")
    app.dependency_overrides[get_settings] = lambda: settings
    return settings


def _seed_closed_trade(settings, exit_price=110.0):
    journal = TradeJournal(journal_file=str(settings.journal_path))
    trade_id = journal.log_signal(
        ticker="AAPL",
        entry_date=datetime(2024, 1, 1),
        entry_price=100.0,
        thesis="Test",
        signal_strength=50.0,
        stop_loss=95.0,
        target_1=110.0,
        target_2=120.0,
    )
    journal.log_entry(
        trade_id=trade_id,
        actual_entry_price=100.0,
        actual_entry_date=datetime(2024, 1, 2),
    )
    journal.log_exit(
        trade_id=trade_id,
        exit_date=datetime(2024, 1, 5),
        exit_price=exit_price,
        exit_reason="TP1",
    )
    return trade_id


class TestJournalSummary:
    def test_no_trades_yet_returns_error_field(self, client, journal_settings):
        response = client.get("/api/v1/journal/summary")
        assert response.status_code == 200
        assert response.json()["error"] == "No completed trades yet"

    def test_summary_after_a_closed_trade(self, client, journal_settings):
        _seed_closed_trade(journal_settings)
        response = client.get("/api/v1/journal/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["total_trades"] == 1
        assert body["win_rate"] == pytest.approx(1.0)


class TestJournalDecay:
    def test_default_windows(self, client, journal_settings):
        response = client.get("/api/v1/journal/decay")
        assert response.status_code == 200
        body = response.json()
        assert set(body["windows"].keys()) == {"30", "60", "90"}
        assert body["windows"]["30"]["trade_count"] == 0

    def test_custom_windows(self, client, journal_settings):
        response = client.get("/api/v1/journal/decay", params={"windows": "7,14"})
        assert response.status_code == 200
        assert set(response.json()["windows"].keys()) == {"7", "14"}

    def test_invalid_windows_is_422(self, client, journal_settings):
        response = client.get("/api/v1/journal/decay", params={"windows": "abc"})
        assert response.status_code == 422


class TestJournalMaeMfe:
    def test_computes_mae_mfe_for_a_closed_trade(
        self, client, journal_settings, monkeypatch
    ):
        trade_id = _seed_closed_trade(journal_settings)

        index = pd.date_range("2024-01-02", "2024-01-05", freq="D")
        price_df = pd.DataFrame(
            {
                "Open": [100.0, 97.0, 103.0, 109.0],
                "High": [101.0, 98.0, 112.0, 110.5],
                "Low": [96.0, 95.0, 102.0, 108.0],
                "Close": [97.0, 97.5, 111.0, 110.0],
            },
            index=index,
        )

        import backend.app.api.journal as journal_module

        monkeypatch.setattr(journal_module, "load_prices", lambda *a, **k: price_df)

        response = client.post(
            "/api/v1/journal/mae-mfe", json={"trade_id": trade_id, "ticker": "AAPL"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["mae_dollars"] == pytest.approx(5.0)
        assert body["mfe_dollars"] == pytest.approx(12.0)

    def test_unknown_trade_id_is_422(self, client, journal_settings, monkeypatch):
        import backend.app.api.journal as journal_module

        monkeypatch.setattr(
            journal_module, "load_prices", lambda *a, **k: pd.DataFrame()
        )
        response = client.post(
            "/api/v1/journal/mae-mfe", json={"trade_id": 999, "ticker": "AAPL"}
        )
        assert response.status_code == 422

    def test_unavailable_ticker_is_503(self, client, journal_settings, monkeypatch):
        import backend.app.api.journal as journal_module

        def _raise(*a, **k):
            raise DataUnavailableError("AAPL not cached")

        monkeypatch.setattr(journal_module, "load_prices", _raise)
        response = client.post(
            "/api/v1/journal/mae-mfe", json={"trade_id": 1, "ticker": "AAPL"}
        )
        assert response.status_code == 503
