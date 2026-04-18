"""Tests for app.bot.formatters.pick_formatter."""

import pytest
from app.bot.formatters.pick_formatter import format_picks, format_estado
from app.data.db_health import SchemaStatus


FIXTURE = {
    "id": 1,
    "home_team_id": 10,
    "away_team_id": 20,
    "league_id": 5,
    "kickoff_at": "2024-01-15T20:00:00+00:00",
    "status": "NS",
}

PREDICTION = {
    "id": 1,
    "fixture_id": 1,
    "market_key": "1X2",
    "selection": "Home",
    "model_probability": 0.65,
    "implied_probability": 0.476,
    "edge": 0.174,
    "confidence_score": 0.65,
    "is_publishable": True,
    "argument_json": {
        "best_odd": 2.10,
        "best_bookmaker": "Bet365",
        "bookmakers_count": 2,
    },
}

TEAM_NAMES = {10: "Real Madrid", 20: "Barcelona"}
LEAGUE_NAMES = {5: "La Liga"}


# ── format_picks ───────────────────────────────────────────────────────────────

def test_empty_predictions_returns_no_picks_message():
    result = format_picks([], [], {}, {})
    assert "No hay picks" in result


def test_single_pick_contains_team_names():
    result = format_picks([PREDICTION], [FIXTURE], TEAM_NAMES, LEAGUE_NAMES)
    assert "Real Madrid" in result
    assert "Barcelona" in result


def test_single_pick_contains_market_info():
    result = format_picks([PREDICTION], [FIXTURE], TEAM_NAMES, LEAGUE_NAMES)
    assert "1X2" in result or "Resultado" in result
    assert "Local" in result  # selection label for "Home"


def test_single_pick_contains_edge():
    result = format_picks([PREDICTION], [FIXTURE], TEAM_NAMES, LEAGUE_NAMES)
    assert "17.4%" in result or "Edge" in result


def test_single_pick_contains_best_odd():
    result = format_picks([PREDICTION], [FIXTURE], TEAM_NAMES, LEAGUE_NAMES)
    assert "2.1" in result  # 2.10


def test_single_pick_contains_bookmaker():
    result = format_picks([PREDICTION], [FIXTURE], TEAM_NAMES, LEAGUE_NAMES)
    assert "Bet365" in result


def test_multiple_picks_separated():
    pred2 = {**PREDICTION, "id": 2, "selection": "Away"}
    result = format_picks([PREDICTION, pred2], [FIXTURE], TEAM_NAMES, LEAGUE_NAMES)
    assert result.count("Real Madrid") == 2


# ── format_estado ─────────────────────────────────────────────────────────────

def _ready_schema() -> SchemaStatus:
    return SchemaStatus(connection_ok=True, tables_ok=True)


def test_format_estado_db_ready_shows_telegram_ok():
    result = format_estado(_ready_schema(), {"fixtures_today": 0, "odds_rows": 0, "picks_today": 0})
    assert "Telegram" in result
    assert "OK" in result


def test_format_estado_db_ready_shows_counts():
    counts = {"fixtures_today": 8, "odds_rows": 320, "picks_today": 3}
    result = format_estado(_ready_schema(), counts)
    assert "8" in result
    assert "320" in result
    assert "3" in result


def test_format_estado_db_ready_shows_fixtures_label():
    counts = {"fixtures_today": 4, "odds_rows": 100, "picks_today": 2}
    result = format_estado(_ready_schema(), counts)
    assert "Fixtures" in result or "fixtures" in result
    assert "Picks" in result or "picks" in result


def test_format_estado_conn_fail_shows_fail():
    schema = SchemaStatus(connection_ok=False, error="timeout")
    result = format_estado(schema, None)
    assert "FAIL" in result
    # Should NOT include the data section
    assert "Fixtures" not in result


def test_format_estado_schema_not_initialized():
    schema = SchemaStatus(
        connection_ok=True,
        tables_ok=False,
        missing_tables=["fixtures", "predictions"],
    )
    result = format_estado(schema, None)
    assert "NO APLICADO" in result
    assert "fixtures" in result
    assert "predictions" in result
    # Migration hint should appear
    assert "010_production_schema.sql" in result


def test_format_estado_counts_none_shows_error_row():
    result = format_estado(_ready_schema(), None)
    assert "Error" in result


def test_format_estado_zero_fixtures_shows_sync_hint():
    counts = {"fixtures_today": 0, "odds_rows": 0, "picks_today": 0}
    result = format_estado(_ready_schema(), counts)
    assert "sync_today.py" in result
