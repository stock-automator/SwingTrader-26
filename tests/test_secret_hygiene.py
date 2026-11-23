"""F1 (batch 02) - secret hygiene: no live credentials in tracked files, and
`Settings` never prints its secrets in logs or reprs."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

import pytest

from backend.app.config import Settings

REPO = Path(__file__).resolve().parent.parent

_BINARY_SUFFIXES = {
    ".parquet",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".duckdb",
    ".woff",
    ".woff2",
    ".zip",
}

_PATTERNS = {
    "telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "alpaca key id": re.compile(r"\b[PA]K[A-Z0-9]{18}\b"),
    "alpaca secret": re.compile(
        r"ALPACA_API_SECRET\s*[=:]\s*['\"]?[A-Za-z0-9/+]{40}", re.IGNORECASE
    ),
}

# Obviously fake placeholder values tests may use; never real-shaped.
_FAKE_KEY = "pk-test-fake-key"
_FAKE_SECRET = "fake-secret-value-for-tests-only"
_FAKE_TOKEN = "123:fake-telegram-token"


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
    ).stdout.decode()
    files = []
    for rel in filter(None, out.split("\0")):
        path = REPO / rel
        if path.suffix.lower() in _BINARY_SUFFIXES or not path.is_file():
            continue
        files.append(path)
    return files


def test_f1_no_credentials_in_tracked_files():
    hits = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in _PATTERNS.items():
            if pattern.search(text):
                hits.append(f"{path.relative_to(REPO)}: {label}")
    assert hits == [], "possible secrets committed:\n" + "\n".join(hits)


def test_f1_no_guards_switch_left_in_tracked_files():
    """`execution_guards_enabled` / `EXECUTION_GUARDS_ENABLED` is deleted
    everywhere outside tests and team notes (docs, .env.example, code)."""
    hits = []
    for path in _tracked_text_files():
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(("tests/", ".claude/")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if re.search(r"execution_guards_enabled", text, re.IGNORECASE):
            hits.append(rel)
    assert hits == []


def _secret_settings() -> Settings:
    return Settings(
        alpaca_api_key=_FAKE_KEY,
        alpaca_api_secret=_FAKE_SECRET,
        telegram_bot_token=_FAKE_TOKEN,
    )


@pytest.mark.parametrize(
    "render", [repr, str, lambda s: f"{s}"], ids=["repr", "str", "fstr"]
)
def test_f1_settings_rendering_masks_secrets(render):
    text = render(_secret_settings())
    for secret in (_FAKE_KEY, _FAKE_SECRET, _FAKE_TOKEN):
        assert secret not in text


def test_f1_logging_settings_masks_secrets(caplog):
    with caplog.at_level(logging.INFO):
        logging.getLogger("f1.secret").info(
            "settings: %s / %r", *[_secret_settings()] * 2
        )
    for secret in (_FAKE_KEY, _FAKE_SECRET, _FAKE_TOKEN):
        assert secret not in caplog.text


def test_f1_masking_keeps_secret_values_usable():
    settings = _secret_settings()
    assert settings.alpaca_api_key == _FAKE_KEY
    assert settings.alpaca_api_secret == _FAKE_SECRET
    assert settings.telegram_bot_token == _FAKE_TOKEN
    assert settings.has_alpaca is True
