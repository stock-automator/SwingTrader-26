"""Tests for `POST /api/v1/position-sizer/preview`."""

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestPositionSizerPreview:
    def test_returns_shares_and_risk_amount(self, client):
        response = client.post(
            "/api/v1/position-sizer/preview",
            json={
                "account_capital": 100_000,
                "risk_pct": 0.01,
                "atr": 2.0,
                "atr_multiplier": 2.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["shares"] == 250
        assert body["risk_amount"] == pytest.approx(1000.0)

    def test_default_multiplier_is_two(self, client):
        response = client.post(
            "/api/v1/position-sizer/preview",
            json={"account_capital": 10_000, "risk_pct": 0.02, "atr": 1.0},
        )
        assert response.status_code == 200
        assert response.json()["shares"] == 100

    def test_rejects_non_positive_atr(self, client):
        response = client.post(
            "/api/v1/position-sizer/preview",
            json={"account_capital": 10_000, "risk_pct": 0.01, "atr": 0},
        )
        assert response.status_code == 422

    def test_rejects_risk_pct_above_one(self, client):
        response = client.post(
            "/api/v1/position-sizer/preview",
            json={"account_capital": 10_000, "risk_pct": 1.5, "atr": 1.0},
        )
        assert response.status_code == 422
