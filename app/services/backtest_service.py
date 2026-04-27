"""Phase 5 walk-forward backtest service.

Model: Elo-adjusted Poisson baseline for 1X2, Double Chance, OU2.5, and BTTS.

Walk-forward protocol:
  - For each test season S, the training window is the [train_seasons] seasons
    immediately preceding S that are present in fixtures_history.
  - Elo ratings are computed chronologically from the first available season
    and carry forward across train and test seasons (never reset mid-run).
  - Goal-rate parameters (attack/defence) are recomputed per training window
    using actual fixture results from fixtures_history — not team_stats_history.
  - No look-ahead: Elo and goal stats for fixture F only include data from
    fixtures with kickoff_at strictly before F's kickoff date.

Metrics (Phase 5 — no historical odds):
  - accuracy: fraction of predictions matching actual outcome
  - brier_score: mean (model_prob - actual_binary)^2 for the predicted selection
  - log_loss: mean -log(p_actual_outcome) across all samples
  - winrate: accuracy for picks within the daily cap (is_selected=True)
  - calibration: ECE and per-bucket mean_predicted vs actual_rate
  - fair_odds_profit: P/L at 1/model_prob synthetic odds (selected picks only)
  implied_probability, edge, ev_value are stored as NULL; Phase 5.5 will
  populate them once historical odds are backfilled via --with-odds.

Pool separation:
  - Clubs and national teams maintain separate Elo ratings.
  - Competition classification is read from competition_context (auto-seeded).
  - Club continuity holds across divisions via provider_team_id.

Neutral venues:
  - Competitions in _NEUTRAL_VENUES (World Cup, Euro, AFCON, Copa America) are
    treated as neutral: no home_advantage in Elo updates, blended team stats.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict

from app.data.local.duckdb_client import get_local_db
from app.data.local import history_repo
from app.model.elo import (
    update as elo_update,
    seasonal_regression,
    INITIAL_RATING,
    HOME_ADVANTAGE,
)
from app.model import poisson_model

logger = logging.getLogger(__name__)

MARKETS = ("1X2", "DC", "OU25", "BTTS")

# Competitions played at fully neutral venues — no home advantage applies.
_NEUTRAL_VENUES: frozenset[int] = frozenset({1, 4, 6, 9})

# Double Chance: which 1X2 outcomes each selection covers.
_DC_COVERS: dict[str, frozenset[str]] = {
    "1X": frozenset({"Home", "Draw"}),
    "X2": frozenset({"Draw", "Away"}),
    "12": frozenset({"Home", "Away"}),
}

# Known competition metadata: league_id → (name, entity_scope, competition_type, elo_weight)
# Unknown leagues default to club / domestic_league / weight=1.0.
_KNOWN: dict[int, tuple[str, str, str, float]] = {
    # International / National Teams
    1:   ("FIFA World Cup",                    "national_team", "international",    1.0),
    4:   ("UEFA Euro Championship",            "national_team", "international",    1.0),
    6:   ("Africa Cup of Nations",             "national_team", "international",    1.0),
    9:   ("Copa America",                      "national_team", "international",    1.0),
    10:  ("FIFA Friendlies",                   "national_team", "international",    0.3),
    # World Cup Qualifiers (national teams)
    29:  ("WC Qual Africa (CAF)",              "national_team", "international",    0.8),
    30:  ("WC Qual CONCACAF",                  "national_team", "international",    0.8),
    32:  ("WC Qual Europe (UEFA)",             "national_team", "international",    0.9),
    34:  ("WC Qual South America (CONMEBOL)",  "national_team", "international",    0.9),
    36:  ("WC Qual Asia (AFC)",                "national_team", "international",    0.8),
    # Continental (clubs) — UEFA
    2:   ("UEFA Champions League",             "club",          "continental",      0.8),
    3:   ("UEFA Europa League",                "club",          "continental",      0.6),
    848: ("UEFA Conference League",            "club",          "continental",      0.5),
    # Continental (clubs) — CONMEBOL
    11:  ("CONMEBOL Sudamericana",             "club",          "continental",      0.5),
    13:  ("CONMEBOL Libertadores",             "club",          "continental",      0.7),
    # CONCACAF national teams
    22:  ("CONCACAF Gold Cup",                 "national_team", "international",    0.9),
    # England
    39:  ("Premier League",                    "club",          "domestic_league",  1.0),
    40:  ("Championship",                      "club",          "domestic_league",  1.0),
    45:  ("FA Cup",                            "club",          "domestic_cup",     0.4),
    48:  ("EFL Cup",                           "club",          "domestic_cup",     0.3),
    # Spain
    140: ("La Liga",                           "club",          "domestic_league",  1.0),
    141: ("La Liga 2",                         "club",          "domestic_league",  1.0),
    143: ("Copa del Rey",                      "club",          "domestic_cup",     0.4),
    # Italy
    135: ("Serie A",                           "club",          "domestic_league",  1.0),
    136: ("Serie B",                           "club",          "domestic_league",  1.0),
    137: ("Coppa Italia",                      "club",          "domestic_cup",     0.4),
    # Germany
    78:  ("Bundesliga",                        "club",          "domestic_league",  1.0),
    79:  ("2. Bundesliga",                     "club",          "domestic_league",  1.0),
    81:  ("DFB Pokal",                         "club",          "domestic_cup",     0.4),
    # France
    61:  ("Ligue 1",                           "club",          "domestic_league",  1.0),
    62:  ("Ligue 2",                           "club",          "domestic_league",  1.0),
    66:  ("Coupe de France",                   "club",          "domestic_cup",     0.4),
    # Netherlands
    88:  ("Eredivisie",                        "club",          "domestic_league",  1.0),
    89:  ("Eerste Divisie",                    "club",          "domestic_league",  1.0),
    # Portugal
    94:  ("Primeira Liga",                     "club",          "domestic_league",  1.0),
    # Turkey
    203: ("Super Lig",                         "club",          "domestic_league",  1.0),
    # Brazil
    71:  ("Serie A Brazil",                    "club",          "domestic_league",  1.0),
    72:  ("Serie B Brazil",                    "club",          "domestic_league",  1.0),
    73:  ("Copa Do Brasil",                    "club",          "domestic_cup",     0.4),
    # Argentina
    128: ("Liga Profesional Argentina",        "club",          "domestic_league",  1.0),
    # North America
    253: ("Major League Soccer",               "club",          "domestic_league",  1.0),
    262: ("Liga MX",                           "club",          "domestic_league",  1.0),
}


# ── Correctness helpers ───────────────────────────────────────────────────────


def _is_correct(market: str, selection: str, actual: str) -> bool:
    """Return True if the selection is correct given the actual 1X2 result.

    For DC, actual is the 1X2 result string (e.g. "Home"); correctness is
    set membership in _DC_COVERS[selection]. For all other markets, simple
    string equality.
    """
    if market == "DC":
        return actual in _DC_COVERS[selection]
    return selection == actual


def _p_actual(
    market: str,
    selection: str,
    all_probs: dict[str, float],
    actual: str,
    correct: bool,
) -> float:
    """Return the model probability assigned to the actual outcome.

    For 1X2/OU25/BTTS: direct lookup in all_probs[actual].
    For DC: binary — p(selection) if correct, 1-p(selection) if not.
    """
    if market == "DC":
        p = all_probs.get(selection, 1e-7)
        return p if correct else max(1e-7, 1.0 - p)
    return all_probs.get(actual, 1e-7)


# ── Internal computation helpers ──────────────────────────────────────────────


def _ground_truth(goals_home: int, goals_away: int) -> dict[str, str]:
    """Return the actual outcome label per market for a completed fixture.

    DC stores the 1X2 result; _is_correct() handles the set-membership check.
    """
    total = goals_home + goals_away
    result_1x2 = (
        "Home" if goals_home > goals_away
        else "Draw" if goals_home == goals_away
        else "Away"
    )
    return {
        "1X2":  result_1x2,
        "DC":   result_1x2,
        "OU25": ("Over 2.5" if total > 2 else "Under 2.5"),
        "BTTS": ("Yes" if goals_home > 0 and goals_away > 0 else "No"),
    }


def _compute_lambdas(
    home_id: int,
    away_id: int,
    team_stats: dict,
    league_avgs: dict,
    elo_home: float,
    elo_away: float,
    *,
    is_neutral: bool = False,
) -> tuple[float, float]:
    """Compute Elo-adjusted Poisson lambda_home and lambda_away.

    Standard (non-neutral):
        lambda_h = (H.home_scored * A.away_conceded) / avg_home_goals
        lambda_a = (A.away_scored * H.home_conceded) / avg_away_goals
    Teams with fewer than 3 home or away games fall back to league averages.

    Neutral venue:
        Uses blended home+away stats (requires >= 6 total games per team).
        Both lambdas reference the same neutral average.

    Elo adjustment applies a conservative multiplicative factor:
        adj = exp((elo_home - elo_away) / 1000)
    """
    avg_h = league_avgs.get("avg_home") or 1.3
    avg_a = league_avgs.get("avg_away") or 1.1
    h = team_stats.get(home_id, {})
    a = team_stats.get(away_id, {})

    if is_neutral:
        avg_g = (avg_h + avg_a) / 2.0
        h_total = h.get("home_games", 0) + h.get("away_games", 0)
        a_total = a.get("home_games", 0) + a.get("away_games", 0)
        h_scored   = ((h.get("home_scored",   0.0) + h.get("away_scored",   0.0)) / 2.0
                      if h_total >= 6 else avg_g)
        a_conceded = ((a.get("home_conceded", 0.0) + a.get("away_conceded", 0.0)) / 2.0
                      if a_total >= 6 else avg_g)
        a_scored   = ((a.get("home_scored",   0.0) + a.get("away_scored",   0.0)) / 2.0
                      if a_total >= 6 else avg_g)
        h_conceded = ((h.get("home_conceded", 0.0) + h.get("away_conceded", 0.0)) / 2.0
                      if h_total >= 6 else avg_g)
        lam_h = (h_scored * a_conceded) / avg_g
        lam_a = (a_scored * h_conceded) / avg_g
    else:
        h_home_ok   = h.get("home_games", 0) >= 3
        a_away_ok   = a.get("away_games", 0) >= 3
        h_scored    = h.get("home_scored",   avg_h) if h_home_ok else avg_h
        a_conceded  = a.get("away_conceded", avg_h) if a_away_ok else avg_h
        a_scored    = a.get("away_scored",   avg_a) if a_away_ok else avg_a
        h_conceded  = h.get("home_conceded", avg_a) if h_home_ok else avg_a
        lam_h = (h_scored * a_conceded) / avg_h
        lam_a = (a_scored * h_conceded) / avg_a

    elo_diff = elo_home - elo_away
    adj = math.exp(elo_diff / 1000.0)
    lam_h = max(0.05, lam_h * adj)
    lam_a = max(0.05, lam_a / adj)
    return round(lam_h, 4), round(lam_a, 4)


def _probs_for_fixture(
    fix: dict,
    elo_state: dict[int, float],
    team_stats: dict,
    league_avgs: dict,
    rho: float = 0.0,
    is_neutral: bool = False,
) -> dict:
    """Return Poisson probabilities for all markets plus internal metadata."""
    home_elo = elo_state.get(fix["home_team_id"], INITIAL_RATING)
    away_elo = elo_state.get(fix["away_team_id"], INITIAL_RATING)
    lam_h, lam_a = _compute_lambdas(
        fix["home_team_id"], fix["away_team_id"],
        team_stats, league_avgs, home_elo, away_elo,
        is_neutral=is_neutral,
    )
    p_1x2 = poisson_model.prob_1x2(lam_h, lam_a, rho=rho)
    return {
        "1X2":       p_1x2,
        "DC":        poisson_model.prob_double_chance(p_1x2),
        "OU25":      poisson_model.prob_ou25(lam_h, lam_a),
        "BTTS":      poisson_model.prob_btts(lam_h, lam_a),
        "_lam_h":    lam_h,
        "_lam_a":    lam_a,
        "_home_elo": home_elo,
        "_away_elo": away_elo,
    }


def _update_elo(
    fix: dict,
    elo_state: dict[int, float],
    entity_scope: str,
    elo_weight: float,
    league_id: int,
    season: int,
    dry_run: bool,
    conn,
    is_neutral: bool = False,
) -> None:
    """Update elo_state in-place and persist to team_elo_history (unless dry_run)."""
    h_before = elo_state.get(fix["home_team_id"], INITIAL_RATING)
    a_before = elo_state.get(fix["away_team_id"], INITIAL_RATING)
    new_h, new_a = elo_update(
        h_before, a_before,
        fix["goals_home"], fix["goals_away"],
        weight=elo_weight,
        home_advantage=0.0 if is_neutral else HOME_ADVANTAGE,
    )
    elo_state[fix["home_team_id"]] = new_h
    elo_state[fix["away_team_id"]] = new_a

    if not dry_run:
        for team_id, elo_b, elo_a, was_home, gf, ga, opp_id in [
            (fix["home_team_id"], h_before, new_h, True,
             fix["goals_home"], fix["goals_away"], fix["away_team_id"]),
            (fix["away_team_id"], a_before, new_a, False,
             fix["goals_away"], fix["goals_home"], fix["home_team_id"]),
        ]:
            history_repo.insert_team_elo(conn, {
                "team_id":            team_id,
                "entity_scope":       entity_scope,
                "provider_league_id": league_id,
                "season":             season,
                "fixture_id":         fix["id"],
                "kickoff_at":         fix["kickoff_at"],
                "elo_before":         elo_b,
                "elo_after":          elo_a,
                "opponent_id":        opp_id,
                "was_home":           was_home,
                "goals_for":          gf,
                "goals_against":      ga,
            })


# ── Metrics helpers ───────────────────────────────────────────────────────────


def _metrics_for_group(samples: list[dict]) -> dict:
    """Compute accuracy, brier_score, log_loss for a list of samples."""
    n = len(samples)
    if not n:
        return {}
    n_correct = sum(1 for s in samples if s["correct"])
    brier = sum(
        (s["model_prob"] - (1.0 if s["correct"] else 0.0)) ** 2
        for s in samples
    ) / n
    ll = -sum(math.log(max(s["p_actual"], 1e-7)) for s in samples) / n
    return {
        "n":           n,
        "accuracy":    round(n_correct / n, 4),
        "brier_score": round(brier, 4),
        "log_loss":    round(ll, 4),
    }


def _calibration_buckets(samples: list[dict], n_buckets: int = 5) -> dict:
    """Group samples by predicted probability range; compute ECE."""
    width = 1.0 / n_buckets
    buckets = []
    ece_num = 0.0
    n_total = len(samples)
    for i in range(n_buckets):
        lo, hi = i * width, (i + 1) * width
        bucket = [s for s in samples if lo <= s["model_prob"] < hi]
        if not bucket:
            buckets.append({"range": f"[{lo:.1f},{hi:.1f})", "n": 0})
            continue
        mean_pred   = sum(s["model_prob"] for s in bucket) / len(bucket)
        actual_rate = sum(1 for s in bucket if s["correct"]) / len(bucket)
        err = abs(mean_pred - actual_rate)
        ece_num += len(bucket) * err
        buckets.append({
            "range":       f"[{lo:.1f},{hi:.1f})",
            "n":           len(bucket),
            "mean_pred":   round(mean_pred, 4),
            "actual_rate": round(actual_rate, 4),
            "error":       round(err, 4),
        })
    ece = round(ece_num / n_total, 4) if n_total else 0.0
    return {"buckets": buckets, "ece": ece}


def _fair_odds_profit(samples: list[dict]) -> dict:
    """P/L at synthetic fair odds (1/model_prob) for selected picks only."""
    sel = [s for s in samples if s.get("is_selected")]
    if not sel:
        return {"n": 0, "profit_units": 0.0, "roi_pct": 0.0}
    profit = sum(
        (1.0 / s["model_prob"] - 1.0) if s["correct"] else -1.0
        for s in sel
    )
    return {
        "n":            len(sel),
        "profit_units": round(profit, 4),
        "roi_pct":      round(profit / len(sel) * 100, 2),
    }


def _by_season(samples: list[dict]) -> dict[int, dict]:
    """Return accuracy/brier/log_loss grouped by season."""
    groups: dict[int, list[dict]] = defaultdict(list)
    for s in samples:
        groups[s["season"]].append(s)
    return {season: _metrics_for_group(grp) for season, grp in sorted(groups.items())}


# ── Public API ────────────────────────────────────────────────────────────────


def seed_competition_context(conn, league_ids: list[int]) -> int:
    """Populate competition_context for each league_id not yet present.

    Uses _KNOWN for recognized leagues; safe defaults (club / domestic_league /
    weight=1.0) for unknown ones. Skips existing rows (INSERT OR IGNORE).
    Returns the number of rows inserted.
    """
    inserted = 0
    for lid in league_ids:
        if history_repo.get_competition_context(conn, lid) is not None:
            continue
        known = _KNOWN.get(lid)
        if known:
            name, scope, ctype, weight = known
        else:
            name, scope, ctype, weight = (f"League {lid}", "club", "domestic_league", 1.0)
        history_repo.insert_competition_context(conn, {
            "provider_league_id": lid,
            "league_name":        name,
            "entity_scope":       scope,
            "competition_type":   ctype,
            "elo_weight":         weight,
        })
        inserted += 1
    if inserted:
        logger.info("competition_context: %d leagues sembrados", inserted)
    return inserted


def run_backtest(
    league_id: int,
    *,
    dry_run: bool = False,
    only_season: int | None = None,
    train_seasons: int = 3,
    min_model_prob: float = 0.0,
    max_daily_picks: int = 5,
    rho: float = 0.0,
    elo_season_regress: float = 0.0,
    recent_weight: float = 0.6,
) -> dict:
    """Run walk-forward Poisson + Elo backtest for a league.

    Args:
        league_id:          provider_league_id (API-Football).
        dry_run:            If True, compute metrics without writing to DuckDB.
        only_season:        Evaluate only this season as test. Elo still built
                            from all prior available seasons.
        train_seasons:      Number of prior seasons as training window.
        min_model_prob:     Minimum model probability to include a pick candidate.
        max_daily_picks:    Daily cap on selected picks.
        rho:                Dixon-Coles correction parameter (0.0 = disabled,
                            -0.13 = typical).
        elo_season_regress: Seasonal Elo regression factor (0.0 = disabled,
                            0.1 = typical). Applied between seasons.

    Returns:
        Summary dict with status, metrics_by_market (with calibration and
        fair_odds_profit), metrics_by_season, selected_picks, run_id.
    """
    conn = get_local_db()

    seed_competition_context(conn, [league_id])
    ctx = history_repo.get_competition_context(conn, league_id)
    entity_scope = ctx["entity_scope"] if ctx else "club"
    elo_weight   = ctx["elo_weight"]   if ctx else 1.0
    league_name  = ctx["league_name"]  if ctx else str(league_id)
    is_neutral   = league_id in _NEUTRAL_VENUES

    all_seasons = sorted(history_repo.get_seasons_in_history(conn, league_id))
    if not all_seasons:
        return {"status": "no_data", "league_id": league_id}

    if only_season is not None:
        if only_season not in all_seasons:
            return {"status": "season_not_found", "league_id": league_id, "season": only_season}
        seasons_to_process = [s for s in all_seasons if s <= only_season]
        test_seasons_set = {only_season}
    else:
        seasons_to_process = all_seasons
        test_seasons_set = set(all_seasons[train_seasons:])

    if not test_seasons_set:
        return {
            "status":    "insufficient_seasons",
            "league_id": league_id,
            "available": all_seasons,
            "needed":    train_seasons + 1,
        }

    logger.info(
        "Backtest liga=%d (%s) scope=%s neutral=%s | seasons=%s | test=%s | train_window=%d",
        league_id, league_name, entity_scope, is_neutral,
        seasons_to_process, sorted(test_seasons_set), train_seasons,
    )

    test_list = sorted(test_seasons_set)
    run_name = f"bt_L{league_id}_T{','.join(str(s) for s in test_list)}"
    run_id: int | None = None
    if not dry_run:
        run_id = history_repo.create_backtest_run(
            conn, run_name,
            description=f"Poisson+Elo walk-forward — {league_name}",
            leagues=[league_id],
            seasons=test_list,
            min_confidence=min_model_prob,
            max_daily_picks=max_daily_picks,
        )

    elo_state: dict[int, float] = {}
    all_samples: list[dict] = []

    for season in seasons_to_process:
        is_test  = season in test_seasons_set
        fixtures = history_repo.get_fixtures_for_season(conn, league_id, season)

        if not fixtures:
            logger.debug("Liga=%d season=%d: sin fixtures completados, omitida", league_id, season)
        else:
            team_stats:  dict = {}
            league_avgs: dict = {"avg_home": 1.3, "avg_away": 1.1}

            if is_test:
                prior = [s for s in all_seasons if s < season]
                train_window = prior[-train_seasons:]
                if not train_window:
                    logger.warning(
                        "Liga=%d season=%d: sin temporadas de entrenamiento previas, omitida",
                        league_id, season,
                    )
                else:
                    team_stats  = history_repo.get_team_goals_stats_weighted(
                        conn, league_id, train_window, recent_weight=recent_weight
                    )
                    league_avgs = history_repo.get_league_goal_averages(conn, league_id, train_window)
                    logger.info(
                        "  Test %d | train=%s | avg_goles h=%.2f a=%.2f | equipos=%d",
                        season, train_window,
                        league_avgs["avg_home"], league_avgs["avg_away"],
                        len(team_stats),
                    )

            by_date: dict[str, list[dict]] = defaultdict(list)
            for fix in fixtures:
                date_key = (fix["kickoff_at"] or "0000-00-00")[:10]
                by_date[date_key].append(fix)

            for date_key in sorted(by_date):
                day_fixes = by_date[date_key]

                if is_test and team_stats is not None:
                    day_candidates: list[dict] = []
                    for fix in day_fixes:
                        probs_all = _probs_for_fixture(
                            fix, elo_state, team_stats, league_avgs,
                            rho=rho, is_neutral=is_neutral,
                        )
                        home_elo = probs_all.pop("_home_elo")
                        away_elo = probs_all.pop("_away_elo")
                        lam_h    = probs_all.pop("_lam_h")
                        lam_a    = probs_all.pop("_lam_a")
                        truth    = _ground_truth(fix["goals_home"], fix["goals_away"])

                        for market in MARKETS:
                            sel_probs = probs_all[market]
                            best_sel  = max(sel_probs, key=sel_probs.get)
                            best_prob = sel_probs[best_sel]
                            if best_prob < min_model_prob:
                                continue
                            day_candidates.append({
                                "fixture":    fix,
                                "market":     market,
                                "selection":  best_sel,
                                "model_prob": best_prob,
                                "all_probs":  sel_probs,
                                "actual":     truth[market],
                                "home_elo":   home_elo,
                                "away_elo":   away_elo,
                                "lam_h":      lam_h,
                                "lam_a":      lam_a,
                                "season":     season,
                            })

                    day_candidates.sort(key=lambda c: c["model_prob"], reverse=True)
                    selected: set[tuple] = set()
                    cnt = 0
                    for c in day_candidates:
                        if cnt >= max_daily_picks:
                            break
                        key = (c["fixture"]["id"], c["market"])
                        if key not in selected:
                            selected.add(key)
                            cnt += 1

                    for c in day_candidates:
                        key     = (c["fixture"]["id"], c["market"])
                        is_sel  = key in selected
                        correct = _is_correct(c["market"], c["selection"], c["actual"])
                        p_act   = _p_actual(
                            c["market"], c["selection"], c["all_probs"], c["actual"], correct
                        )

                        if not dry_run and run_id is not None:
                            history_repo.insert_training_sample(conn, {
                                "backtest_run_id":     run_id,
                                "fixture_history_id":  c["fixture"]["id"],
                                "provider_league_id":  league_id,
                                "season":              season,
                                "kickoff_at":          c["fixture"]["kickoff_at"],
                                "home_team_id":        c["fixture"]["home_team_id"],
                                "away_team_id":        c["fixture"]["away_team_id"],
                                "market_key":          c["market"],
                                "selection":           c["selection"],
                                "home_elo":            c["home_elo"],
                                "away_elo":            c["away_elo"],
                                "elo_diff":            c["home_elo"] - c["away_elo"],
                                "lambda_home":         c["lam_h"],
                                "lambda_away":         c["lam_a"],
                                "model_probability":   c["model_prob"],
                                "implied_probability": None,
                                "edge":                None,
                                "ev_value":            None,
                                "goals_home":          c["fixture"]["goals_home"],
                                "goals_away":          c["fixture"]["goals_away"],
                                "actual_outcome":      c["actual"],
                                "model_correct":       correct,
                                "split":               "test",
                            })
                            history_repo.insert_backtest_metric(conn, run_id, {
                                "fixture_history_id":  c["fixture"]["id"],
                                "market_key":          c["market"],
                                "selection":           c["selection"],
                                "model_probability":   c["model_prob"],
                                "implied_probability": None,
                                "edge":                None,
                                "confidence_score":    c["model_prob"],
                                "odd_taken":           None,
                                "result_status":       "win" if correct else "loss",
                                "profit_units":        None,
                                "kickoff_at":          c["fixture"]["kickoff_at"],
                                "is_selected":         is_sel,
                            })

                        all_samples.append({
                            "market":      c["market"],
                            "model_prob":  c["model_prob"],
                            "p_actual":    p_act,
                            "correct":     correct,
                            "is_selected": is_sel,
                            "season":      season,
                        })

                for fix in day_fixes:
                    _update_elo(
                        fix, elo_state, entity_scope, elo_weight,
                        league_id, season, dry_run, conn,
                        is_neutral=is_neutral,
                    )

        # Apply seasonal Elo regression between seasons
        if elo_season_regress > 0.0:
            for tid in list(elo_state.keys()):
                elo_state[tid] = seasonal_regression(elo_state[tid], factor=elo_season_regress)

    # ── Aggregate metrics ────────────────────────────────────────────────────

    metrics_by_market: dict[str, dict] = {}
    selected_by_market: dict[str, dict] = {}

    for market in MARKETS:
        mkt_samples = [s for s in all_samples if s["market"] == market]
        if not mkt_samples:
            continue

        base = _metrics_for_group(mkt_samples)
        calib = _calibration_buckets(mkt_samples)
        fair  = _fair_odds_profit(mkt_samples)

        metrics_by_market[market] = {**base, "calibration": calib, "fair_odds": fair}

        sel = [s for s in mkt_samples if s["is_selected"]]
        if sel:
            n_sel = len(sel)
            wins  = sum(1 for s in sel if s["correct"])
            selected_by_market[market] = {
                "n":       n_sel,
                "wins":    wins,
                "winrate": round(wins / n_sel, 4),
            }

    metrics_by_season = _by_season(all_samples)

    total_selected = sum(d["n"] for d in selected_by_market.values())
    overall_acc = (
        sum(s["correct"] for s in all_samples) / len(all_samples)
        if all_samples else 0.0
    )

    if not dry_run and run_id is not None:
        history_repo.finish_backtest_run(
            conn, run_id,
            status="completed",
            total_picks=total_selected,
            total_profit_units=0.0,
            roi_pct=round(overall_acc * 100, 2),
        )

    return {
        "status":             "ok",
        "league_id":          league_id,
        "league_name":        league_name,
        "entity_scope":       entity_scope,
        "test_seasons":       sorted(test_seasons_set),
        "train_seasons":      train_seasons,
        "total_samples":      len(all_samples),
        "metrics_by_market":  metrics_by_market,
        "metrics_by_season":  metrics_by_season,
        "selected_picks":     selected_by_market,
        "run_id":             run_id,
        "dry_run":            dry_run,
        "rho":                rho,
        "elo_season_regress": elo_season_regress,
        "max_daily_picks":    max_daily_picks,
        "recent_weight":      recent_weight,
    }
