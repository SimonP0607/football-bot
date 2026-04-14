"""Tests for app.model.filters.pick_filter.PickFilter."""

import pytest
from app.model.filters.pick_filter import PickFilter
from app.model.predictors.odds_predictor import PredictionCandidate


def _candidate(
    selection: str = "Home",
    edge: float = 0.10,
    confidence: float = 0.65,
    fixture_id: int = 1,
    market: str = "1X2",
) -> PredictionCandidate:
    return PredictionCandidate(
        fixture_id=fixture_id,
        market=market,
        selection=selection,
        model_probability=confidence,
        implied_probability=confidence - edge,
        edge=edge,
        confidence_score=confidence,
        best_odd=1 / (confidence - edge) if (confidence - edge) > 0 else 99,
        best_bookmaker="TestBK",
        argument_json={},
    )


def test_empty_candidates_returns_empty():
    result = PickFilter().apply([], min_edge=0.05, min_confidence=0.60, max_picks=3)
    assert result == []


def test_all_below_edge_returns_empty():
    candidates = [_candidate(edge=0.02), _candidate(edge=0.01)]
    result = PickFilter().apply(candidates, min_edge=0.05, min_confidence=0.60, max_picks=5)
    assert result == []


def test_all_below_confidence_returns_empty():
    candidates = [_candidate(confidence=0.50, edge=0.10)]
    result = PickFilter().apply(candidates, min_edge=0.05, min_confidence=0.60, max_picks=5)
    assert result == []


def test_passing_candidates_returned():
    candidates = [_candidate(edge=0.08, confidence=0.65)]
    result = PickFilter().apply(candidates, min_edge=0.05, min_confidence=0.60, max_picks=5)
    assert len(result) == 1
    assert result[0].selection == "Home"


def test_sorted_by_edge_descending():
    candidates = [
        _candidate("Home", edge=0.06, confidence=0.65),
        _candidate("Away", edge=0.12, confidence=0.65),
        _candidate("Draw", edge=0.09, confidence=0.65),
    ]
    result = PickFilter().apply(candidates, min_edge=0.05, min_confidence=0.60, max_picks=10)
    edges = [c.edge for c in result]
    assert edges == sorted(edges, reverse=True)


def test_max_picks_limits_output():
    candidates = [_candidate(f"sel_{i}", edge=0.10 + i * 0.01, confidence=0.65) for i in range(6)]
    result = PickFilter().apply(candidates, min_edge=0.05, min_confidence=0.60, max_picks=3)
    assert len(result) == 3


def test_exact_threshold_values_pass():
    candidate = _candidate(edge=0.05, confidence=0.60)
    result = PickFilter().apply([candidate], min_edge=0.05, min_confidence=0.60, max_picks=5)
    assert len(result) == 1


def test_mixed_passing_and_failing():
    candidates = [
        _candidate("Home", edge=0.10, confidence=0.70),   # passes
        _candidate("Draw", edge=0.02, confidence=0.70),   # fails edge
        _candidate("Away", edge=0.10, confidence=0.50),   # fails confidence
    ]
    result = PickFilter().apply(candidates, min_edge=0.05, min_confidence=0.60, max_picks=5)
    assert len(result) == 1
    assert result[0].selection == "Home"
