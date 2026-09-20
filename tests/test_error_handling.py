"""
Tests for the global unhandled-exception handler
(`backend.app.main.unhandled_exception_handler`) - confirms a route that
raises something other than `HTTPException` comes back as a well-formed 500
JSON response instead of whatever ASGI's bare default error response would
be. Exercised against a throwaway `FastAPI` app (not the real `app`) so this
doesn't mutate the shared application instance every other test module
imports; every route's own deliberate 422/503/etc. `HTTPException` handling
is untouched by this handler and stays covered by each route's own tests.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.main import unhandled_exception_handler


def _boom() -> None:
    raise RuntimeError("boom")


def _make_client() -> TestClient:
    test_app = FastAPI()
    test_app.add_exception_handler(Exception, unhandled_exception_handler)
    test_app.add_api_route("/boom", _boom, methods=["GET"])
    return TestClient(test_app, raise_server_exceptions=False)


def test_unhandled_exception_returns_formatted_500():
    response = _make_client().get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Internal server error. Check server logs for details."
    }
