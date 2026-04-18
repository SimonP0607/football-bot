"""Tests for API-Football response parsing in app.data.api_football.endpoints.

These tests mock the HTTP client and verify that ``fetch_odds`` correctly:
- maps API-Football market names to internal codes
- filters only the 2.5 line for OU25
- skips invalid or unsupported markets
- handles empty responses gracefully
- returns enriched rows with bookmaker_id and bet_id
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.data.api_football.endpoints import fetch_odds, fetch_fixtures


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_paginated_mock(page_items: list) -> MagicMock:
    """Return an async generator mock that yields one page with the given items."""
    async def _gen(*args, **kwargs):
        yield page_items

    mock_client = MagicMock()
    mock_client.get_paginated = _gen
    mock_client.get = AsyncMock()
    return mock_client


def _odds_response_items(fixture_id: int = 1) -> list:
    """Minimal /odds response items (single page) with typical markets."""
    return [
        {
            "fixture": {"id": fixture_id},
            "bookmakers": [
                {
                    "id": 8,
                    "name": "Bet365",
                    "bets": [
                        {
                            "id": 1,
                            "name": "Match Winner",
                            "values": [
                                {"value": "Home", "odd": "2.10"},
                                {"value": "Draw", "odd": "3.50"},
                                {"value": "Away", "odd": "4.00"},
                            ],
                        },
                        {
                            "id": 5,
                            "name": "Goals Over/Under",
                            "values": [
                                {"value": "Over 1.5",  "odd": "1.30"},  # skipped
                                {"value": "Over 2.5",  "odd": "2.20"},
                                {"value": "Under 2.5", "odd": "1.70"},
                                {"value": "Over 3.5",  "odd": "3.10"},  # skipped
                            ],
                        },
                        {
                            "id": 8,
                            "name": "Both Teams Score",
                            "values": [
                                {"value": "Yes", "odd": "1.90"},
                                {"value": "No",  "odd": "1.90"},
                            ],
                        },
                        {
                            "id": 999,
                            "name": "Unsupported Market",
                            "values": [{"value": "Yes", "odd": "1.50"}],
                        },
                    ],
                }
            ],
        }
    ]


def _fixtures_response() -> dict:
    return {
        "results": 1,
        "response": [
            {
                "fixture": {
                    "id": 12345,
                    "date": "2025-01-15T20:00:00+00:00",
                    "status": {"short": "NS", "long": "Not Started", "elapsed": None},
                    "timezone": "UTC",
                    "venue": {"id": 556, "name": "Old Trafford"},
                },
                "league": {"id": 39, "name": "Premier League", "country": "England", "season": 2025},
                "teams": {
                    "home": {"id": 33, "name": "Manchester United", "country": "England"},
                    "away": {"id": 40, "name": "Liverpool", "country": "England"},
                },
            }
        ],
    }


# ── Tests: fetch_odds parsing ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_odds_empty_response():
    mock_client = _make_paginated_mock([])
    with patch("app.data.api_football.endpoints.api_client", mock_client):
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])
    assert rows == []


@pytest.mark.asyncio
async def test_fetch_odds_maps_1x2_market():
    mock_client = _make_paginated_mock(_odds_response_items())
    with patch("app.data.api_football.endpoints.api_client", mock_client):
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])

    markets_found = {r["market"] for r in rows}
    assert markets_found == {"1X2"}


@pytest.mark.asyncio
async def test_fetch_odds_1x2_has_three_selections():
    mock_client = _make_paginated_mock(_odds_response_items())
    with patch("app.data.api_football.endpoints.api_client", mock_client):
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])

    selections = {r["selection"] for r in rows}
    assert selections == {"Home", "Draw", "Away"}


@pytest.mark.asyncio
async def test_fetch_odds_ou25_keeps_only_2_5_line():
    mock_client = _make_paginated_mock(_odds_response_items())
    with patch("app.data.api_football.endpoints.api_client", mock_client):
        rows = await fetch_odds(fixture_id=1, markets=["OU25"])

    ou25_rows = [r for r in rows if r["market"] == "OU25"]
    selections = {r["selection"] for r in ou25_rows}
    assert selections == {"Over 2.5", "Under 2.5"}


@pytest.mark.asyncio
async def test_fetch_odds_btts_selections():
    mock_client = _make_paginated_mock(_odds_response_items())
    with patch("app.data.api_football.endpoints.api_client", mock_client):
        rows = await fetch_odds(fixture_id=1, markets=["BTTS"])

    btts_rows = [r for r in rows if r["market"] == "BTTS"]
    selections = {r["selection"] for r in btts_rows}
    assert selections == {"Yes", "No"}


@pytest.mark.asyncio
async def test_fetch_odds_skips_unsupported_market():
    mock_client = _make_paginated_mock(_odds_response_items())
    with patch("app.data.api_football.endpoints.api_client", mock_client):
        rows = await fetch_odds(fixture_id=1, markets=["1X2", "OU25", "BTTS"])

    markets_found = {r["market"] for r in rows}
    assert "Unsupported Market" not in markets_found
    assert all(m in {"1X2", "OU25", "BTTS"} for m in markets_found)


@pytest.mark.asyncio
async def test_fetch_odds_requested_market_subset():
    """Only request 1X2 — OU25 and BTTS rows must not appear."""
    mock_client = _make_paginated_mock(_odds_response_items())
    with patch("app.data.api_football.endpoints.api_client", mock_client):
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])

    markets_found = {r["market"] for r in rows}
    assert "OU25" not in markets_found
    assert "BTTS" not in markets_found


@pytest.mark.asyncio
async def test_fetch_odds_row_structure():
    """Each returned row must have the expected keys including new bookmaker_id/bet_id."""
    mock_client = _make_paginated_mock(_odds_response_items())
    with patch("app.data.api_football.endpoints.api_client", mock_client):
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])

    expected_keys = {"bookmaker_id", "bookmaker_name", "bet_id", "market", "selection", "odd"}
    for row in rows:
        assert set(row.keys()) == expected_keys
        assert isinstance(row["odd"], float)
        assert row["odd"] > 1.0
        assert isinstance(row["bookmaker_id"], int)
        assert isinstance(row["bet_id"], int)


@pytest.mark.asyncio
async def test_fetch_odds_bookmaker_name_and_id():
    mock_client = _make_paginated_mock(_odds_response_items())
    with patch("app.data.api_football.endpoints.api_client", mock_client):
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])

    assert all(r["bookmaker_name"] == "Bet365" for r in rows)
    assert all(r["bookmaker_id"] == 8 for r in rows)
    assert all(r["bet_id"] == 1 for r in rows)  # Match Winner bet_id


# ── Tests: fetch_fixtures parsing ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_fixtures_empty_response():
    with patch("app.data.api_football.endpoints.api_client") as mock_client:
        mock_client.get = AsyncMock(return_value={"results": 0, "response": []})
        items = await fetch_fixtures(date="2025-01-15", league_id=39, season=2025)
    assert items == []


@pytest.mark.asyncio
async def test_fetch_fixtures_returns_items():
    with patch("app.data.api_football.endpoints.api_client") as mock_client:
        mock_client.get = AsyncMock(return_value=_fixtures_response())
        items = await fetch_fixtures(date="2025-01-15", league_id=39, season=2025)

    assert len(items) == 1
    item = items[0]
    assert item["fixture"]["id"] == 12345
    assert item["teams"]["home"]["name"] == "Manchester United"
    assert item["teams"]["away"]["name"] == "Liverpool"
