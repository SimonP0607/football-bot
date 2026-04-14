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


def test_missing_user_id_raises():
    with pytest.raises(ValueError, match="TELEGRAM_ALLOWED_USER_ID"):
        Settings(**{**VALID, "telegram_allowed_user_id": 0})


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
