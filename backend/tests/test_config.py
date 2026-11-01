import pytest
from pydantic import ValidationError

from ipo_intel.config import ConfigError, Settings, require
from tests.fakes import make_settings


def test_defaults_match_the_budget_plan():
    settings = make_settings()
    assert (settings.ipoalerts_daily_cap, settings.ipoalerts_monthly_cap) == (12, 400)
    assert settings.ipoalerts_backfill_daily_cap == 8
    assert (settings.indianapi_pause_backfill_below, settings.indianapi_essential_only_below) == (1000, 300)
    assert settings.http_max_retries == 3
    assert not any(settings.secret_status().values())


def test_secrets_come_from_env_and_are_masked(monkeypatch):
    monkeypatch.setenv("INDIANAPI_KEY", "  ia-live-SECRET-0001 \n")
    settings = make_settings()
    assert settings.indianapi_key.get_secret_value() == "ia-live-SECRET-0001"
    assert "ia-live-SECRET-0001" not in repr(settings)
    assert "ia-live-SECRET-0001" not in str(settings.model_dump())
    assert settings.secret_status()["INDIANAPI_KEY"] is True


def test_blank_secret_counts_as_unset(monkeypatch):
    monkeypatch.setenv("IPOALERTS_KEY", "   ")
    assert make_settings().ipoalerts_key is None


def test_env_file_is_read_as_utf8(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_CHAT_ID=12345678\nLOG_LEVEL=debug  # ₹ comment\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)
    assert settings.telegram_chat_id.get_secret_value() == "12345678"
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    "overrides",
    [
        {"ipoalerts_daily_cap": 25},
        {"ipoalerts_monthly_cap": 750},
        {"ipoalerts_daily_cap": 10, "ipoalerts_backfill_daily_cap": 11},
        {"indianapi_essential_only_below": 2000},
        {"http_backoff_base_seconds": 10, "http_backoff_max_seconds": 5},
        {"http_max_retries": 9},
    ],
)
def test_unsafe_limits_are_rejected(overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_require_names_the_variable_not_the_value():
    with pytest.raises(ConfigError, match="^INDIANAPI_KEY is not set$"):
        require(None, "INDIANAPI_KEY")
    settings = make_settings(indianapi_key="ia-SECRET-42")
    assert require(settings.indianapi_key, "INDIANAPI_KEY") == "ia-SECRET-42"


def test_secret_values_include_the_database_password():
    settings = make_settings(
        ipoalerts_key="alerts-SECRET-77",
        database_url="postgresql://ipo:pw-SECRET-99@db.example.internal:5432/ipo_intel",
    )
    values = settings.secret_values()
    assert "alerts-SECRET-77" in values
    assert "pw-SECRET-99" in values
