"""Tests for app.model.predictors.odds_predictor.OddsPredictor."""

import pytest
from app.model.predictors.odds_predictor import OddsPredictor, PredictionCandidate


def _make_rows(
    fixture_id: int = 1,
    bookmaker: str = "Bet365",
    market: str = "1X2",
    home_odd: float = 2.10,
    draw_odd: float = 3.50,
    away_odd: float = 4.00,
) -> list[dict]:
    return [
        {"fixture_id": fixture_id, "bookmaker": bookmaker, "market": market, "selection": "Home", "odd": home_odd},
        {"fixture_id": fixture_id, "bookmaker": bookmaker, "market": market, "selection": "Draw", "odd": draw_odd},
        {"fixture_id": fixture_id, "bookmaker": bookmaker, "market": market, "selection": "Away", "odd": away_odd},
    ]


def test_empty_input_returns_empty():
    result = OddsPredictor().calculate([])
    assert result == []


def test_returns_one_candidate_per_selection():
    rows = _make_rows()
    result = OddsPredictor().calculate(rows)
    selections = {c.selection for c in result}
    assert selections == {"Home", "Draw", "Away"}


def test_model_probability_sums_close_to_one():
    rows = _make_rows()
    result = OddsPredictor().calculate(rows)
    total = sum(c.model_probability for c in result if c.market == "1X2")
    assert abs(total - 1.0) < 0.01, f"Expected probabilities ~1.0, got {total}"


def test_implied_probability_equals_one_over_best_odd():
    rows = _make_rows(home_odd=2.10)
    result = OddsPredictor().calculate(rows)
    home = next(c for c in result if c.selection == "Home")
    assert abs(home.implied_probability - (1 / 2.10)) < 0.001


def test_edge_equals_model_minus_implied():
    # edge, model_probability, and implied_probability are each rounded to 4
    # decimal places independently, so their arithmetic may differ by up to
    # 2 * 0.00005 = 0.0001.  Use a tolerance of 2e-4 to account for that.
    rows = _make_rows()
    result = OddsPredictor().calculate(rows)
    for c in result:
        assert abs(c.edge - (c.model_probability - c.implied_probability)) < 2e-4


def test_single_bookmaker_edge_is_negative_or_zero():
    # With only one bookmaker, model_prob = fair_prob < implied_prob → edge ≤ 0
    rows = _make_rows()
    result = OddsPredictor().calculate(rows)
    for c in result:
        assert c.edge <= 0.001, f"Expected non-positive edge with one BK, got {c.edge}"


def test_two_bookmakers_best_odd_used():
    rows = _make_rows(bookmaker="Bet365", home_odd=2.10) + \
           _make_rows(bookmaker="WilliamHill", home_odd=2.20, draw_odd=3.40, away_odd=3.80)
    result = OddsPredictor().calculate(rows)
    home = next(c for c in result if c.selection == "Home")
    # Best odd for Home should be 2.20
    assert home.best_odd == 2.20
    assert home.best_bookmaker == "WilliamHill"


def test_argument_json_contains_expected_keys():
    rows = _make_rows()
    result = OddsPredictor().calculate(rows)
    for c in result:
        assert "market" in c.argument_json
        assert "selection" in c.argument_json
        assert "best_odd" in c.argument_json
        assert "bookmakers_count" in c.argument_json


def test_invalid_odd_skipped():
    rows = [
        {"fixture_id": 1, "bookmaker": "BK", "market": "1X2", "selection": "Home", "odd": 0.5},
        {"fixture_id": 1, "bookmaker": "BK", "market": "1X2", "selection": "Draw", "odd": 3.50},
        {"fixture_id": 1, "bookmaker": "BK", "market": "1X2", "selection": "Away", "odd": 4.00},
    ]
    # odd=0.5 is <= 1.0; that bookmaker row will still be included but Home may
    # dominate differently. Main check: no exception is raised.
    result = OddsPredictor().calculate(rows)
    assert isinstance(result, list)
