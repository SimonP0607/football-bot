"""Tests for API-Football response parsing in app.data.api_football.endpoints.

These tests mock the HTTP client and verify that ``fetch_odds`` correctly:
- maps API-Football market names to internal codes
- filters Only the 2.5 line for OU25
- skips invalid or unsupported markets
- handles empty responses gracefully
"""

import pytest
from unittest.mock import AsyncMock, patch

# Import the function under test directly (no real HTTP calls)
from app.data.api_football.endpoints import fetch_odds, fetch_fixtures


# ── Sample API-Football responses ─────────────────────────────────────────────

def _odds_response(fixture_id: int = 1) -> dict:
    """Minimal realistic /odds response for fixture_id."""
    return {
        "results": 1,
        "response": [
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
                                    {"value": "Over 1.5",  "odd": "1.30"},  # should be skipped
                                    {"value": "Over 2.5",  "odd": "2.20"},
                                    {"value": "Under 2.5", "odd": "1.70"},
                                    {"value": "Over 3.5",  "odd": "3.10"},  # should be skipped
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
        ],
    }


def _fixtures_response() -> dict:
    return {
        "results": 1,
        "response": [
            {
                "fixture": {
                    "id": 12345,
                    "date": "2025-01-15T20:00:00+00:00",
                    "status": {"short": "NS"},
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
    with patch("app.data.api_football.endpoints.api_client") as mock_client:
        mock_client.get = AsyncMock(return_value={"results": 0, "response": []})
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])
    assert rows == []


@pytest.mark.asyncio
async def test_fetch_odds_maps_1x2_market():
    with patch("app.data.api_football.endpoints.api_client") as mock_client:
        mock_client.get = AsyncMock(return_value=_odds_response())
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])

    markets_found = {r["market"] for r in rows}
    assert markets_found == {"1X2"}


@pytest.mark.asyncio
async def test_fetch_odds_1x2_has_three_selections():
    with patch("app.data.api_football.endpoints.api_client") as mock_client:
        mock_client.get = AsyncMock(return_value=_odds_response())
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])

    selections = {r["selection"] for r in rows}
    assert selections == {"Home", "Draw", "Away"}


@pytest.mark.asyncio
async def test_fetch_odds_ou25_keeps_only_2_5_line():
    with patch("app.data.api_football.endpoints.api_client") as mock_client:
        mock_client.get = AsyncMock(return_value=_odds_response())
        rows = await fetch_odds(fixture_id=1, markets=["OU25"])

    ou25_rows = [r for r in rows if r["market"] == "OU25"]
    selections = {r["selection"] for r in ou25_rows}
    # Must include Over/Under 2.5 but NOT other lines (1.5, 3.5, etc.)
    assert selections == {"Over 2.5", "Under 2.5"}


@pytest.mark.asyncio
async def test_fetch_odds_btts_selections():
    with patch("app.data.api_football.endpoints.api_client") as mock_client:
        mock_client.get = AsyncMock(return_value=_odds_response())
        rows = await fetch_odds(fixture_id=1, markets=["BTTS"])

    btts_rows = [r for r in rows if r["market"] == "BTTS"]
    selections = {r["selection"] for r in btts_rows}
    assert selections == {"Yes", "No"}


@pytest.mark.asyncio
async def test_fetch_odds_skips_unsupported_market():
    with patch("app.data.api_football.endpoints.api_client") as mock_client:
        mock_client.get = AsyncMock(return_value=_odds_response())
        rows = await fetch_odds(fixture_id=1, markets=["1X2", "OU25", "BTTS"])

    markets_found = {r["market"] for r in rows}
    assert "Unsupported Market" not in markets_found
    assert all(m in {"1X2", "OU25", "BTTS"} for m in markets_found)


@pytest.mark.asyncio
async def test_fetch_odds_requested_market_subset():
    """Only request 1X2 — OU25 and BTTS rows must not appear."""
    with patch("app.data.api_football.endpoints.api_client") as mock_client:
        mock_client.get = AsyncMock(return_value=_odds_response())
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])

    markets_found = {r["market"] for r in rows}
    assert "OU25" not in markets_found
    assert "BTTS" not in markets_found


@pytest.mark.asyncio
async def test_fetch_odds_row_structure():
    """Each returned row must have the expected keys and types."""
    with patch("app.data.api_football.endpoints.api_client") as mock_client:
        mock_client.get = AsyncMock(return_value=_odds_response())
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])

    for row in rows:
        assert set(row.keys()) == {"bookmaker", "market", "selection", "odd"}
        assert isinstance(row["odd"], float)
        assert row["odd"] > 1.0


@pytest.mark.asyncio
async def test_fetch_odds_bookmaker_name():
    with patch("app.data.api_football.endpoints.api_client") as mock_client:
        mock_client.get = AsyncMock(return_value=_odds_response())
        rows = await fetch_odds(fixture_id=1, markets=["1X2"])

    assert all(r["bookmaker"] == "Bet365" for r in rows)


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
