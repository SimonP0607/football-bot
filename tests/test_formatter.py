"""Tests for app.bot.formatters.pick_formatter."""

import pytest
from app.bot.formatters.pick_formatter import format_picks, format_estado


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
    "market": "1X2",
    "recommended_pick": "Home",
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
    pred2 = {**PREDICTION, "id": 2, "recommended_pick": "Away"}
    result = format_picks([PREDICTION, pred2], [FIXTURE], TEAM_NAMES, LEAGUE_NAMES)
    assert result.count("Real Madrid") == 2


def test_format_estado_contains_counts():
    estado = {"fixtures_today": 8, "odds_rows": 320, "picks_today": 3}
    result = format_estado(estado)
    assert "8" in result
    assert "320" in result
    assert "3" in result


def test_format_estado_contains_labels():
    estado = {"fixtures_today": 0, "odds_rows": 0, "picks_today": 0}
    result = format_estado(estado)
    assert "Fixtures" in result or "fixtures" in result
    assert "Picks" in result or "picks" in result
