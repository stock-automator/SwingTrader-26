"""
Tests for the Meshnet/LAN CORS allowance (`Settings.cors_origin_regex`,
`backend.app.config.DEFAULT_CORS_ORIGIN_REGEX`) added for mobile/NordVPN
Meshnet access to the dev server.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from backend.app.config import DEFAULT_CORS_ORIGIN_REGEX
from backend.app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestCorsOriginRegexPattern:
    @pytest.mark.parametrize(
        "origin",
        [
            "http://192.168.1.42:5173",
            "http://10.0.0.5:5173",
            "http://100.64.12.9:5173",
            "https://192.168.0.1:5173",
        ],
    )
    def test_matches_private_network_origins(self, origin):
        assert re.fullmatch(DEFAULT_CORS_ORIGIN_REGEX, origin)

    @pytest.mark.parametrize(
        "origin",
        [
            "http://evil.com:5173",
            "http://8.8.8.8:5173",
            "http://192.168.1.42",  # missing port
            "ftp://192.168.1.42:5173",
        ],
    )
    def test_rejects_non_private_or_malformed_origins(self, origin):
        assert re.fullmatch(DEFAULT_CORS_ORIGIN_REGEX, origin) is None


class TestCorsMiddlewareIntegration:
    def test_meshnet_origin_gets_cors_headers(self, client):
        response = client.get(
            "/api/v1/health", headers={"Origin": "http://100.64.1.2:5173"}
        )
        assert response.status_code == 200
        assert (
            response.headers.get("access-control-allow-origin")
            == "http://100.64.1.2:5173"
        )

    def test_public_origin_gets_no_cors_header(self, client):
        response = client.get("/api/v1/health", headers={"Origin": "http://evil.com"})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
