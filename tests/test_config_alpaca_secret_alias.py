"""The Alpaca secret is read from ALPACA_API_SECRET, or from Alpaca's own
name ALPACA_SECRET_KEY (what the Hermes bot's .env uses)."""

from backend.app.config import Settings, get_settings


def _load(monkeypatch, **env):
    for name in ("ALPACA_API_SECRET", "ALPACA_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()
    return Settings.from_env() if hasattr(Settings, "from_env") else get_settings()


def test_api_secret_name(monkeypatch):
    assert _load(monkeypatch, ALPACA_API_SECRET="a").alpaca_api_secret == "a"


def test_secret_key_alias(monkeypatch):
    assert _load(monkeypatch, ALPACA_SECRET_KEY="b").alpaca_api_secret == "b"


def test_api_secret_wins_over_alias(monkeypatch):
    s = _load(monkeypatch, ALPACA_API_SECRET="a", ALPACA_SECRET_KEY="b")
    assert s.alpaca_api_secret == "a"


def test_neither_set_is_none(monkeypatch):
    assert _load(monkeypatch).alpaca_api_secret is None
