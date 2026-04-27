"""Shadow mode prediction service.

Generates model probability predictions for upcoming fixtures without
touching any production system. No Supabase calls, no Telegram messages,
no modification of the live pick pipeline.

All predictions are derived from:
  - Current Elo state: latest elo_after per team from team_elo_history
  - Recent goal statistics: last train_seasons of fixtures_history
  - Competition context: competition_context table (auto-seeded if missing)

Limitations (by design — Phase 5 shadow):
  - Elo state reflects the last backtest run, NOT live match results.
    For accurate shadow picks, run the backtest on the most recent season
    before calling predict_fixture.
  - No historical odds: all fields relating to implied_probability, edge,
    and ev_value are None. Phase 5.5 will add odds backfill.
  - No fixture lookup: caller must supply provider team IDs directly.

Next step (shadow pipeline integration):
  1. Add a shadow_picks JSONL file or DuckDB table to persist predictions.
  2. Add settle_shadow_picks() that grades past predictions by joining
     completed fixtures_history rows.
  3. Wire to the daily sync pipeline via a shadow_mode=True flag in config.
"""

from __future__ import annotations

import logging
from datetime import date

from app.data.local import history_repo
from app.data.local.duckdb_client import get_local_db
from app.model.elo import INITIAL_RATING
from app.services.backtest_service import (
    _NEUTRAL_VENUES,
    _probs_for_fixture,
    seed_competition_context,
    MARKETS,
)

logger = logging.getLogger(__name__)


def predict_fixture(
    conn=None,
    *,
    league_id: int,
    home_team_id: int,
    away_team_id: int,
    fixture_date: str | None = None,
    rho: float = 0.0,
    train_seasons: int = 2,
    recent_weight: float = 0.6,
) -> dict:
    """Return model probabilities and top picks for an upcoming fixture.

    Args:
        conn:           DuckDB connection (uses get_local_db() if None).
        league_id:      provider_league_id (API-Football).
        home_team_id:   provider_team_id for the home team.
        away_team_id:   provider_team_id for the away team.
        fixture_date:   ISO date string "YYYY-MM-DD" (informational only).
        rho:            Dixon-Coles correction (0.0=disabled, -0.13=typical).
        train_seasons:  Number of most-recent seasons to use for goal stats.
        recent_weight:  Weight for the most recent training season (0.6=default).

    Returns:
        Dict with league metadata, Elo ratings, lambdas, per-market probs,
        and recommended pick per market.  implied_probability/edge/ev_value
        are None (Phase 5 — no odds data).
    """
    if conn is None:
        conn = get_local_db()

    seed_competition_context(conn, [league_id])
    ctx = history_repo.get_competition_context(conn, league_id)
    entity_scope = ctx["entity_scope"] if ctx else "club"
    league_name  = ctx["league_name"]  if ctx else str(league_id)
    is_neutral   = league_id in _NEUTRAL_VENUES

    # Load current Elo state from team_elo_history
    elo_state = history_repo.get_current_elo_state(conn, entity_scope)
    n_elo = len(elo_state)
    if n_elo == 0:
        logger.warning(
            "shadow: no Elo data for scope=%s liga=%d — "
            "run backtest first to populate team_elo_history",
            entity_scope, league_id,
        )

    # Load recent goal stats for goal-rate lambda computation
    all_seasons = sorted(history_repo.get_seasons_in_history(conn, league_id))
    if not all_seasons:
        return {
            "error":     "no_data",
            "league_id": league_id,
            "message":   "No hay fixtures en fixtures_history para esta liga.",
        }

    train_window = all_seasons[-train_seasons:]
    team_stats  = history_repo.get_team_goals_stats_weighted(
        conn, league_id, train_window, recent_weight=recent_weight
    )
    league_avgs = history_repo.get_league_goal_averages(conn, league_id, train_window)

    # Build a fixture-like dict (no goals — upcoming match)
    fix = {
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "goals_home":   None,
        "goals_away":   None,
    }

    probs_raw = _probs_for_fixture(
        fix, elo_state, team_stats, league_avgs,
        rho=rho, is_neutral=is_neutral,
    )

    lam_h    = probs_raw.pop("_lam_h")
    lam_a    = probs_raw.pop("_lam_a")
    home_elo = probs_raw.pop("_home_elo")
    away_elo = probs_raw.pop("_away_elo")

    # Build per-market picks
    picks: dict[str, dict] = {}
    for market in MARKETS:
        sel_probs = probs_raw.get(market, {})
        if not sel_probs:
            continue
        best_sel  = max(sel_probs, key=sel_probs.get)
        best_prob = sel_probs[best_sel]
        picks[market] = {
            "selection":           best_sel,
            "probability":         best_prob,
            "all_probs":           sel_probs,
            "implied_probability": None,
            "edge":                None,
            "ev_value":            None,
        }

    logger.info(
        "shadow predict: liga=%d %s vs %s | elo_diff=%.0f | lam=%.2f/%.2f",
        league_id, home_team_id, away_team_id,
        home_elo - away_elo, lam_h, lam_a,
    )

    return {
        "league_id":     league_id,
        "league_name":   league_name,
        "entity_scope":  entity_scope,
        "is_neutral":    is_neutral,
        "home_team_id":  home_team_id,
        "away_team_id":  away_team_id,
        "fixture_date":  fixture_date or str(date.today()),
        "home_elo":      home_elo,
        "away_elo":      away_elo,
        "elo_diff":      round(home_elo - away_elo, 2),
        "lambda_home":   lam_h,
        "lambda_away":   lam_a,
        "train_window":  train_window,
        "picks":         picks,
        "rho":           rho,
        "elo_teams_loaded": n_elo,
    }
