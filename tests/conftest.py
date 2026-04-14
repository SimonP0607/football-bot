"""Pytest configuration: set required env vars before any app module is imported.

These are dummy values — no real API calls are made in unit tests.
load_dotenv() uses override=False by default, so a real .env file won't be
overwritten; the setdefault calls here ensure the values are present when
running without a .env (e.g. CI environments).
"""
import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0000000000:test_token_for_pytest")
os.environ.setdefault("TELEGRAM_ALLOWED_USER_ID", "12345")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-service-role-key")
os.environ.setdefault("API_FOOTBALL_KEY", "test-api-football-key")
