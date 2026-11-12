import pytest
from pydantic import ValidationError

from deskmate.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    s = Settings(_env_file=None)
    assert s.llm_model == "claude-sonnet-5-5"
    assert s.bot_mode == "off"
    assert (s.context_window, s.context_ttl_s, s.dialog_idle_minutes) == (20, 3600, 30)


def test_api_key_required(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "mode,missing", [("polling", "TELEGRAM_BOT_TOKEN"), ("webhook", "WEBHOOK_SECRET")]
)
def test_bot_mode_requires_its_settings(monkeypatch, mode, missing):
    for k, v in {
        "API_KEY": "k",
        "BOT_MODE": mode,
        "TELEGRAM_BOT_TOKEN": "t",
        "WEBHOOK_BASE_URL": "https://x",
        "WEBHOOK_SECRET": "s",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(missing)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("value", ["", "   "])
def test_api_key_must_not_be_blank(monkeypatch, value):
    monkeypatch.setenv("API_KEY", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
