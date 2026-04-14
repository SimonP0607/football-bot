"""Tests for app.core.config.Settings."""

import pytest
from app.core.config import Settings


VALID = dict(
    telegram_bot_token="fake:token",
    telegram_allowed_user_id=12345,
    supabase_url="https://x.supabase.co",
    supabase_key="service_key",
    api_football_key="api_key",
)


def test_valid_settings_does_not_raise():
    s = Settings(**VALID)
    assert s.telegram_allowed_user_id == 12345


def test_missing_token_raises():
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        Settings(**{**VALID, "telegram_bot_token": ""})


def test_missing_user_id_in_local_activates_bootstrap():
    """In local mode, user_id=0 is valid — activates bootstrap mode instead of crashing."""
    s = Settings(**{**VALID, "telegram_allowed_user_id": 0})
    assert s.is_bootstrap_mode is True


def test_missing_user_id_in_production_raises():
    """In production mode, user_id=0 must raise so the bot never starts unprotected."""
    with pytest.raises(ValueError, match="TELEGRAM_ALLOWED_USER_ID"):
        Settings(**{**VALID, "telegram_allowed_user_id": 0, "app_env": "production"})


def test_missing_supabase_url_raises():
    with pytest.raises(ValueError, match="SUPABASE_URL"):
        Settings(**{**VALID, "supabase_url": ""})


def test_missing_api_key_raises():
    with pytest.raises(ValueError, match="API_FOOTBALL_KEY"):
        Settings(**{**VALID, "api_football_key": ""})


def test_markets_list_parses_default():
    s = Settings(**VALID)
    assert s.markets_list == ["1X2", "OU25", "BTTS"]


def test_markets_list_parses_custom():
    s = Settings(**{**VALID, "default_markets": "1X2, BTTS"})
    assert s.markets_list == ["1X2", "BTTS"]


def test_markets_list_handles_single():
    s = Settings(**{**VALID, "default_markets": "OU25"})
    assert s.markets_list == ["OU25"]


def test_default_thresholds():
    s = Settings(**VALID)
    assert s.min_edge == 0.05
    assert s.min_confidence == 0.60
    assert s.max_daily_picks == 3


def test_is_bootstrap_mode_false_when_user_id_set():
    s = Settings(**VALID)  # VALID has telegram_allowed_user_id=12345
    assert s.is_bootstrap_mode is False


def test_is_local_for_non_production():
    s = Settings(**{**VALID, "app_env": "local"})
    assert s.is_local is True


def test_is_local_false_for_production():
    s = Settings(**{**VALID, "app_env": "production"})
    assert s.is_local is False


def test_masked_token_hides_secret():
    s = Settings(**{**VALID, "telegram_bot_token": "123456789:ABCDEFGHsecretpart"})
    masked = s.masked_token
    assert "ABCDEFGHsecretpart" not in masked
    assert "123456789:" in masked
    assert "*" in masked


def test_masked_token_empty():
    s = Settings.__new__(Settings)
    s.telegram_bot_token = ""
    assert s.masked_token == "(not set)"


def test_league_ids_list_empty_by_default():
    s = Settings(**VALID)
    assert s.league_ids_list == []


def test_league_ids_list_parses_correctly():
    s = Settings(**{**VALID, "default_league_ids": "39,140,253"})
    assert s.league_ids_list == [39, 140, 253]
