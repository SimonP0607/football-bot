"""Phase 11: Player Intelligence & Props Signals service.

Orchestrates:
  A) Sync of per-fixture player statistics from API-Football.
  B) Building player season profiles from raw fixture stats.
  C) Computing recent form windows.
  D) Generating informational prop-style signals.

IMPORTANT: Prop signals are NOT official picks and are never presented
as betting recommendations. They are analytical signals only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

from app.core.config import settings

logger = logging.getLogger(__name__)

# Market keys for prop signals
MARKET_KEYS = [
    "player_goal_signal",
    "player_assist_signal",
    "player_shot_signal",
    "player_shot_on_target_signal",
    "player_card_signal",
    "player_foul_signal",
    "player_minutes_signal",
]

MARKET_KEY_LABELS = {
    "player_goal_signal": "Gol",
    "player_assist_signal": "Asistencia",
    "player_shot_signal": "Tiro",
    "player_shot_on_target_signal": "Tiro al arco",
    "player_card_signal": "Tarjeta",
    "player_foul_signal": "Falta",
    "player_minutes_signal": "Minutos jugados",
}

MIN_SAMPLE_SIZE = 5  # min fixtures needed for signals


# ── A: Sync fixture player stats ──────────────────────────────────────────────

async def sync_fixture_player_stats(
    conn: "duckdb.DuckDBPyConnection",
    provider_fixture_id: int,
    fixture_id: int | None = None,
    league_id: int | None = None,
    season: int | None = None,
    dry_run: bool = True,
) -> dict:
    """Fetch and store player stats for one fixture. Returns summary dict."""
    from app.data.api_football.endpoints import (
        fetch_fixture_player_stats,
        parse_fixture_player_stats,
    )
    from app.services.api_budget_service import consume_budget

    logger.info("sync_fixture_player_stats: fixture=%s dry_run=%s", provider_fixture_id, dry_run)

    if not dry_run:
        consume_budget(1, priority="medium", source="player_stats")

    try:
        raw_blocks = await fetch_fixture_player_stats(provider_fixture_id)
        rows = parse_fixture_player_stats(
            raw_blocks,
            provider_fixture_id=provider_fixture_id,
            fixture_id=fixture_id,
            league_id=league_id,
            season=season,
        )
    except Exception as exc:
        logger.error("sync_fixture_player_stats: API error — %s", exc)
        return {"status": "api_error", "error": str(exc), "rows": 0}

    if dry_run:
        return {"status": "dry_run", "rows": len(rows), "fixture_id": provider_fixture_id}

    from app.data.local.player_intelligence_repo import upsert_player_fixture_stats
    written = upsert_player_fixture_stats(conn, rows)
    logger.info("sync_fixture_player_stats: fixture=%s written=%d", provider_fixture_id, written)
    return {"status": "ok", "rows": written, "fixture_id": provider_fixture_id}


async def sync_player_stats_bulk(
    conn: "duckdb.DuckDBPyConnection",
    date: str | None = None,
    league_id: int | None = None,
    fixture_ids: list[int] | None = None,
    limit: int = 20,
    max_requests: int = 30,
    dry_run: bool = True,
) -> dict:
    """Sync player stats for multiple recent/finished fixtures.

    Returns summary with total fixtures processed and rows written.
    """
    from app.services.api_budget_service import get_budget_status

    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Resolve fixture IDs from Supabase if not provided
    if not fixture_ids:
        fixture_ids = _get_finished_fixture_ids(date, league_id, limit)

    if not fixture_ids:
        return {"status": "no_fixtures", "total": 0, "written": 0, "api_calls": 0}

    # Budget check
    budget = get_budget_status()
    remaining = budget.get("remaining_today", max_requests)
    max_requests = min(max_requests, remaining, len(fixture_ids))

    logger.info(
        "sync_player_stats_bulk: %d fixtures, limit=%d dry_run=%s",
        len(fixture_ids), max_requests, dry_run,
    )

    total_rows = 0
    api_calls = 0
    errors = 0

    for i, pfix_id in enumerate(fixture_ids[:max_requests]):
        if api_calls >= max_requests:
            break
        result = await sync_fixture_player_stats(
            conn, pfix_id, dry_run=dry_run,
        )
        api_calls += 1
        if result.get("status") == "ok":
            total_rows += result.get("rows", 0)
        elif result.get("status") == "api_error":
            errors += 1

    return {
        "status": "ok",
        "date": date,
        "fixtures_processed": api_calls,
        "rows_written": total_rows,
        "api_calls": api_calls,
        "errors": errors,
        "dry_run": dry_run,
    }


def _get_finished_fixture_ids(
    date: str,
    league_id: int | None,
    limit: int,
) -> list[int]:
    """Get provider_fixture_ids for recently finished fixtures from Supabase."""
    try:
        from app.data.repositories.fixture_repo import get_finished_fixtures_for_date
        fixtures = get_finished_fixtures_for_date(date, league_id=league_id, limit=limit)
        return [f["provider_fixture_id"] for f in fixtures if f.get("provider_fixture_id")]
    except Exception as exc:
        logger.debug("_get_finished_fixture_ids: %s", exc)
    # Fallback: try DuckDB local
    try:
        from app.data.local.duckdb_client import get_local_db
        dconn = get_local_db()
        rows = dconn.execute(
            """
            SELECT DISTINCT provider_fixture_id FROM player_fixture_stats
            WHERE synced_at::DATE = ? ORDER BY synced_at DESC LIMIT ?
            """,
            [date, limit],
        ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


# ── B: Build player season profiles ──────────────────────────────────────────

def build_player_profiles(
    conn: "duckdb.DuckDBPyConnection",
    league_id: int | None = None,
    season: int | None = None,
    team_id: int | None = None,
    dry_run: bool = True,
) -> dict:
    """Aggregate player_fixture_stats into player_season_profiles.

    Returns summary with count of profiles built.
    """
    from app.data.local.player_intelligence_repo import upsert_player_season_profile

    # Build WHERE clause
    conditions = ["minutes > 0"]
    params: list[Any] = []
    if league_id:
        conditions.append("league_id = ?")
        params.append(league_id)
    if season:
        conditions.append("season = ?")
        params.append(season)
    if team_id:
        conditions.append("team_id = ?")
        params.append(team_id)

    where = " AND ".join(conditions)

    try:
        rows = conn.execute(
            f"""
            SELECT
                player_id,
                MAX(player_name)  AS player_name,
                team_id,
                MAX(team_name)    AS team_name,
                league_id,
                season,
                MAX(position)     AS position,
                COUNT(*)          AS appearances,
                SUM(CASE WHEN NOT substitute THEN 1 ELSE 0 END) AS starts,
                SUM(minutes)      AS total_minutes,
                AVG(minutes)      AS avg_minutes,
                SUM(goals_total)  AS goals,
                SUM(assists)      AS assists,
                SUM(shots_total)  AS shots_total,
                SUM(shots_on)     AS shots_on,
                CASE WHEN SUM(shots_total) > 0
                     THEN CAST(SUM(shots_on) AS FLOAT) / SUM(shots_total)
                     ELSE 0 END   AS shots_on_rate,
                SUM(passes_key)   AS key_passes,
                SUM(fouls_drawn)  AS fouls_drawn,
                SUM(fouls_committed) AS fouls_committed,
                SUM(cards_yellow) AS yellow_cards,
                SUM(cards_red)    AS red_cards,
                AVG(rating)       AS avg_rating
            FROM player_fixture_stats
            WHERE {where}
            GROUP BY player_id, team_id, league_id, season
            """,
            params,
        ).fetchall()
    except Exception as exc:
        logger.error("build_player_profiles: query failed — %s", exc)
        return {"status": "error", "error": str(exc), "profiles": 0}

    if dry_run:
        return {"status": "dry_run", "profiles": len(rows)}

    built = 0
    for row in rows:
        profile = {
            "player_id": row[0], "player_name": row[1],
            "team_id": row[2], "team_name": row[3],
            "league_id": row[4], "season": row[5],
            "position": row[6], "appearances": row[7],
            "starts": row[8], "total_minutes": row[9],
            "avg_minutes": row[10], "goals": row[11],
            "assists": row[12], "shots_total": row[13],
            "shots_on": row[14], "shots_on_rate": row[15],
            "key_passes": row[16], "fouls_drawn": row[17],
            "fouls_committed": row[18], "yellow_cards": row[19],
            "red_cards": row[20], "avg_rating": row[21],
        }
        try:
            upsert_player_season_profile(conn, profile)
            built += 1
        except Exception as exc:
            logger.debug("build_player_profiles: upsert failed player_id=%s — %s", row[0], exc)

    logger.info("build_player_profiles: %d profiles built", built)
    return {"status": "ok", "profiles": built, "dry_run": False}


# ── C: Recent form ────────────────────────────────────────────────────────────

def compute_player_recent_form(
    conn: "duckdb.DuckDBPyConnection",
    player_id: int,
    window: int = 5,
    team_id: int | None = None,
    league_id: int | None = None,
    season: int | None = None,
) -> dict | None:
    """Compute and store rolling-window form for one player.

    Returns the form dict (with trend_label) or None on insufficient data.
    """
    from app.data.local.player_intelligence_repo import upsert_player_recent_form

    conditions = ["player_id = ?", "minutes > 0"]
    params: list[Any] = [player_id]
    if team_id:
        conditions.append("team_id = ?")
        params.append(team_id)
    if league_id:
        conditions.append("league_id = ?")
        params.append(league_id)
    if season:
        conditions.append("season = ?")
        params.append(season)

    where = " AND ".join(conditions)

    try:
        rows = conn.execute(
            f"""
            SELECT minutes, goals_total, assists, shots_total, shots_on,
                   passes_key, fouls_drawn, fouls_committed,
                   cards_yellow + cards_red AS cards, rating,
                   team_id, league_id, season
            FROM player_fixture_stats
            WHERE {where}
            ORDER BY synced_at DESC
            LIMIT ?
            """,
            params + [window],
        ).fetchall()
    except Exception as exc:
        logger.debug("compute_player_recent_form: %s", exc)
        return None

    if not rows:
        return None

    n = len(rows)
    tid = rows[0][10]
    lid = rows[0][11]
    sea = rows[0][12]

    def _avg(idx):
        vals = [r[idx] for r in rows if r[idx] is not None]
        return sum(vals) / len(vals) if vals else None

    avg_mins = _avg(0)
    goals = sum(r[1] or 0 for r in rows)
    assists = sum(r[2] or 0 for r in rows)
    shots = sum(r[3] or 0 for r in rows)
    shots_on = sum(r[4] or 0 for r in rows)
    key_passes = sum(r[5] or 0 for r in rows)
    fouls_drawn = sum(r[6] or 0 for r in rows)
    fouls_committed = sum(r[7] or 0 for r in rows)
    cards = sum(r[8] or 0 for r in rows)
    avg_rating = _avg(9)

    # Compare vs season profile for trend detection
    trend_label = _compute_trend_label(
        conn, player_id, tid, lid, sea,
        recent_goals=goals, recent_minutes=avg_mins or 0, n_matches=n,
    )

    form = {
        "player_id": player_id,
        "team_id": team_id or tid,
        "league_id": league_id or lid,
        "season": season or sea,
        "window_size": window,
        "matches_count": n,
        "avg_minutes": avg_mins,
        "goals": goals,
        "assists": assists,
        "shots_total": shots,
        "shots_on": shots_on,
        "key_passes": key_passes,
        "fouls_drawn": fouls_drawn,
        "fouls_committed": fouls_committed,
        "cards_total": cards,
        "avg_rating": avg_rating,
        "trend_label": trend_label,
    }

    try:
        upsert_player_recent_form(conn, form)
    except Exception as exc:
        logger.debug("compute_player_recent_form: upsert failed — %s", exc)

    return form


def _compute_trend_label(
    conn: "duckdb.DuckDBPyConnection",
    player_id: int,
    team_id: int | None,
    league_id: int | None,
    season: int | None,
    recent_goals: int,
    recent_minutes: float,
    n_matches: int,
) -> str:
    if n_matches < MIN_SAMPLE_SIZE:
        return "insufficient_data"
    if recent_minutes < 30:
        return "low_minutes"

    # Compare recent vs full-season goals/game rate
    try:
        row = conn.execute(
            """
            SELECT AVG(goals_total), COUNT(*) FROM player_fixture_stats
            WHERE player_id = ? AND minutes > 0
            """,
            [player_id],
        ).fetchone()
        season_avg_goals = (row[0] or 0.0)
        total_matches = row[1] or 0
    except Exception:
        return "stable"

    recent_avg = recent_goals / n_matches if n_matches else 0
    if season_avg_goals > 0:
        ratio = recent_avg / season_avg_goals
        if ratio >= 1.5:
            return "hot"
        if ratio <= 0.5:
            return "declining"
    elif recent_avg > 0:
        return "hot"

    return "stable"


def build_all_recent_forms(
    conn: "duckdb.DuckDBPyConnection",
    windows: list[int] | None = None,
    league_id: int | None = None,
    dry_run: bool = True,
) -> dict:
    """Build recent form for all players with enough fixture data."""
    if windows is None:
        windows = [3, 5, 10]

    try:
        conditions = ["minutes > 0"]
        params: list[Any] = []
        if league_id:
            conditions.append("league_id = ?")
            params.append(league_id)
        where = " AND ".join(conditions)
        player_rows = conn.execute(
            f"""
            SELECT DISTINCT player_id, team_id, league_id, season
            FROM player_fixture_stats WHERE {where}
            """,
            params,
        ).fetchall()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    if dry_run:
        return {"status": "dry_run", "players": len(player_rows), "windows": windows}

    built = 0
    for pid, tid, lid, sea in player_rows:
        for w in windows:
            form = compute_player_recent_form(conn, pid, window=w, team_id=tid, league_id=lid, season=sea)
            if form:
                built += 1

    return {"status": "ok", "form_records": built}


# ── D: Prop signals ───────────────────────────────────────────────────────────

def generate_player_signals(
    conn: "duckdb.DuckDBPyConnection",
    provider_fixture_id: int | None = None,
    league_id: int | None = None,
    days: int = 1,
    limit: int = 50,
    dry_run: bool = True,
) -> dict:
    """Generate informational prop signals for players.

    Signals are stored as player_prop_signals, NOT as official picks.
    They are informational/analytical only.
    """
    from app.data.local.player_intelligence_repo import (
        upsert_player_prop_signal,
        get_player_recent_form,
        get_player_profile,
    )

    # Determine which fixtures to process
    if provider_fixture_id:
        fixture_ids = [provider_fixture_id]
    else:
        fixture_ids = _get_recent_fixture_ids_with_stats(conn, league_id, days, limit)

    if not fixture_ids:
        return {"status": "no_fixtures", "signals": 0}

    signals_generated = 0
    for pfix_id in fixture_ids:
        try:
            n = _generate_signals_for_fixture(conn, pfix_id, dry_run)
            signals_generated += n
        except Exception as exc:
            logger.debug("generate_player_signals: fixture=%s — %s", pfix_id, exc)

    return {
        "status": "dry_run" if dry_run else "ok",
        "fixtures": len(fixture_ids),
        "signals": signals_generated,
    }


def _get_recent_fixture_ids_with_stats(
    conn: "duckdb.DuckDBPyConnection",
    league_id: int | None,
    days: int,
    limit: int,
) -> list[int]:
    try:
        if league_id:
            rows = conn.execute(
                """
                SELECT DISTINCT provider_fixture_id
                FROM player_fixture_stats
                WHERE league_id = ?
                ORDER BY synced_at DESC
                LIMIT ?
                """,
                [league_id, limit],
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT provider_fixture_id
                FROM player_fixture_stats
                ORDER BY synced_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _generate_signals_for_fixture(
    conn: "duckdb.DuckDBPyConnection",
    provider_fixture_id: int,
    dry_run: bool,
) -> int:
    """Generate signals for all players in a fixture. Returns count written."""
    from app.data.local.player_intelligence_repo import (
        upsert_player_prop_signal,
        get_player_recent_form,
        get_player_profile,
    )

    try:
        rows = conn.execute(
            """
            SELECT player_id, player_name, team_id, league_id, season,
                   fixture_id, position
            FROM player_fixture_stats
            WHERE provider_fixture_id = ? AND minutes > 0
            """,
            [provider_fixture_id],
        ).fetchall()
    except Exception:
        return 0

    written = 0
    for row in rows:
        pid, pname, tid, lid, sea, fid, pos = row
        if not pid:
            continue

        # Load profile and recent form
        profile = get_player_profile(conn, pid)
        form = get_player_recent_form(conn, pid, window=5)
        availability_score = _get_availability_score(conn, pid, provider_fixture_id)

        if not profile or not form:
            # Not enough data for signals
            if not dry_run:
                _write_no_data_signal(conn, pid, pname, tid, lid, sea, fid, provider_fixture_id)
            continue

        n_matches = form.get("matches_count", 0)
        if n_matches < MIN_SAMPLE_SIZE:
            if not dry_run:
                _write_no_data_signal(conn, pid, pname, tid, lid, sea, fid, provider_fixture_id)
            continue

        for market_key in MARKET_KEYS:
            signal = _compute_signal(
                market_key=market_key,
                player_id=pid,
                player_name=pname,
                team_id=tid,
                league_id=lid,
                season=sea,
                fixture_id=fid,
                provider_fixture_id=provider_fixture_id,
                profile=profile,
                form=form,
                position=pos,
                availability_score=availability_score,
            )
            if not dry_run:
                try:
                    upsert_player_prop_signal(conn, signal)
                    written += 1
                except Exception as exc:
                    logger.debug("_generate_signals_for_fixture: %s", exc)
            else:
                written += 1

    return written


def _compute_signal(
    market_key: str,
    player_id: int,
    player_name: str,
    team_id: int | None,
    league_id: int | None,
    season: int | None,
    fixture_id: int | None,
    provider_fixture_id: int,
    profile: dict,
    form: dict,
    position: str | None,
    availability_score: float,
) -> dict:
    """Compute a single prop signal for a market."""
    mins = form.get("avg_minutes") or profile.get("avg_minutes") or 0
    minutes_projection = min(float(mins), 90.0)
    minutes_score = minutes_projection / 90.0

    trend_label = form.get("trend_label", "insufficient_data")
    trend_score = _trend_score_for_market(market_key, form, profile, trend_label)
    role_score = _role_score(market_key, position)
    team_context_score = 0.5  # neutral without opponent data

    confidence_score = (
        0.35 * trend_score
        + 0.25 * minutes_score
        + 0.20 * role_score
        + 0.10 * availability_score
        + 0.10 * team_context_score
    )
    confidence_score = min(max(confidence_score, 0.0), 1.0)

    # Determine status
    is_high_risk = (
        minutes_projection < 45
        or availability_score < 0.3
        or trend_label in ("low_minutes",)
    )
    is_no_data = trend_label == "insufficient_data"

    if is_no_data:
        status = "no_data"
    elif is_high_risk:
        status = "high_risk"
    elif confidence_score >= 0.65:
        status = "recommended"
    else:
        status = "observed"

    reason = _build_reason(market_key, form, profile, trend_label, minutes_projection)
    risk_text = _build_risk(is_high_risk, minutes_projection, availability_score, trend_label)

    return {
        "player_id": player_id,
        "player_name": player_name,
        "team_id": team_id,
        "league_id": league_id,
        "season": season,
        "fixture_id": fixture_id,
        "provider_fixture_id": provider_fixture_id,
        "market_key": market_key,
        "signal_type": "prop_signal",
        "confidence_score": round(confidence_score, 4),
        "trend_score": round(trend_score, 4),
        "availability_score": round(availability_score, 4),
        "minutes_projection": round(minutes_projection, 1),
        "reason_text": reason,
        "risk_text": risk_text,
        "status": status,
    }


def _trend_score_for_market(
    market_key: str, form: dict, profile: dict, trend_label: str
) -> float:
    if trend_label == "insufficient_data":
        return 0.0
    if trend_label == "hot":
        base = 0.85
    elif trend_label == "stable":
        base = 0.60
    elif trend_label == "declining":
        base = 0.25
    elif trend_label == "low_minutes":
        return 0.10
    else:
        base = 0.50

    n = form.get("matches_count", 0)
    if n < 3:
        return base * 0.5

    # Market-specific adjustments based on recent numbers
    if market_key == "player_goal_signal":
        goals = form.get("goals", 0) / max(n, 1)
        return min(base + goals * 0.5, 1.0)
    if market_key == "player_assist_signal":
        assists = form.get("assists", 0) / max(n, 1)
        return min(base + assists * 0.4, 1.0)
    if market_key in ("player_shot_signal", "player_shot_on_target_signal"):
        shots = form.get("shots_total", 0) / max(n, 1)
        return min(base + shots * 0.1, 1.0)
    if market_key == "player_card_signal":
        cards = form.get("cards_total", 0) / max(n, 1)
        return min(cards * 2.0, 1.0)
    if market_key == "player_foul_signal":
        fouls = form.get("fouls_committed", 0) / max(n, 1)
        return min(fouls * 0.3, 1.0)
    if market_key == "player_minutes_signal":
        avg_mins = form.get("avg_minutes") or 0
        return min(avg_mins / 90.0, 1.0)

    return base


def _role_score(market_key: str, position: str | None) -> float:
    pos = (position or "").lower()
    if market_key == "player_goal_signal":
        if "forward" in pos or "attacker" in pos:
            return 1.0
        if "midfielder" in pos or "midfield" in pos:
            return 0.55
        if "defender" in pos or "back" in pos:
            return 0.15
        return 0.30  # GK or unknown
    if market_key == "player_assist_signal":
        if "midfielder" in pos or "midfield" in pos:
            return 0.90
        if "forward" in pos or "attacker" in pos:
            return 0.70
        if "defender" in pos or "back" in pos:
            return 0.25
        return 0.25
    if market_key in ("player_shot_signal", "player_shot_on_target_signal"):
        if "forward" in pos or "attacker" in pos:
            return 0.95
        if "midfielder" in pos:
            return 0.60
        if "defender" in pos:
            return 0.20
        return 0.20
    if market_key == "player_card_signal":
        if "defender" in pos or "midfielder" in pos:
            return 0.80
        if "forward" in pos:
            return 0.50
        return 0.40
    if market_key == "player_foul_signal":
        if "defender" in pos or "midfielder" in pos:
            return 0.80
        return 0.50
    if market_key == "player_minutes_signal":
        return 0.70  # applies to most outfield players
    return 0.50


def _get_availability_score(
    conn: "duckdb.DuckDBPyConnection",
    player_id: int,
    provider_fixture_id: int,
) -> float:
    """Check player availability from Phase 4 DuckDB tables. Returns 0-1 score."""
    try:
        row = conn.execute(
            """
            SELECT type FROM fixture_injuries_history
            WHERE provider_fixture_id = ? AND player_id = ?
            LIMIT 1
            """,
            [provider_fixture_id, player_id],
        ).fetchone()
        if row:
            injury_type = (row[0] or "").lower()
            if "injured" in injury_type or "suspended" in injury_type:
                return 0.0
            return 0.50
        return 1.0
    except Exception:
        return 0.80  # DuckDB table may not exist yet


def _write_no_data_signal(
    conn: "duckdb.DuckDBPyConnection",
    player_id: int,
    player_name: str,
    team_id: int | None,
    league_id: int | None,
    season: int | None,
    fixture_id: int | None,
    provider_fixture_id: int,
) -> None:
    from app.data.local.player_intelligence_repo import upsert_player_prop_signal
    for mkey in MARKET_KEYS:
        try:
            upsert_player_prop_signal(conn, {
                "player_id": player_id,
                "player_name": player_name,
                "team_id": team_id,
                "league_id": league_id,
                "season": season,
                "fixture_id": fixture_id,
                "provider_fixture_id": provider_fixture_id,
                "market_key": mkey,
                "status": "no_data",
                "confidence_score": 0.0,
                "reason_text": "Datos insuficientes (menos de 5 partidos)",
            })
        except Exception:
            pass


def _build_reason(
    market_key: str, form: dict, profile: dict, trend_label: str, minutes_projection: float
) -> str:
    n = form.get("matches_count", 0)
    trend_es = {
        "hot": "en racha", "stable": "estable", "declining": "en baja",
        "low_minutes": "pocos minutos", "insufficient_data": "pocos datos",
    }.get(trend_label, trend_label)

    if market_key == "player_goal_signal":
        goals = form.get("goals", 0)
        return f"{goals}G en {n} partidos ({trend_es}), ~{minutes_projection:.0f}' proyectados"
    if market_key == "player_assist_signal":
        asst = form.get("assists", 0)
        return f"{asst}A en {n} partidos ({trend_es})"
    if market_key in ("player_shot_signal", "player_shot_on_target_signal"):
        shots = form.get("shots_total", 0)
        on = form.get("shots_on", 0)
        return f"{shots} tiros ({on} al arco) en {n} partidos ({trend_es})"
    if market_key == "player_card_signal":
        cards = form.get("cards_total", 0)
        return f"{cards} tarjetas en {n} partidos ({trend_es})"
    if market_key == "player_foul_signal":
        fouls = form.get("fouls_committed", 0)
        return f"{fouls} faltas cometidas en {n} partidos"
    if market_key == "player_minutes_signal":
        avg = form.get("avg_minutes") or 0
        return f"Prom {avg:.0f}' en {n} partidos"
    return f"{trend_es}, {n} partidos"


def _build_risk(
    is_high_risk: bool,
    minutes_projection: float,
    availability_score: float,
    trend_label: str,
) -> str:
    parts = []
    if minutes_projection < 45:
        parts.append(f"Bajo minutos (~{minutes_projection:.0f}')")
    if availability_score < 0.3:
        parts.append("Posible lesión/suspensión")
    if trend_label == "declining":
        parts.append("Tendencia a la baja")
    if not parts:
        return "Riesgo normal"
    return " · ".join(parts)


# ── Public helpers for bot handlers ──────────────────────────────────────────

def get_player_analysis(
    conn: "duckdb.DuckDBPyConnection",
    query: str,
) -> dict | None:
    """Find a player by name/ID and return full analysis dict."""
    from app.data.local.player_intelligence_repo import (
        get_player_profile, search_players, get_player_recent_form,
        get_player_fixture_history,
    )

    # Try numeric ID
    if query.strip().isdigit():
        pid = int(query.strip())
        profile = get_player_profile(conn, pid)
        if not profile:
            return None
    else:
        matches = search_players(conn, query, limit=1)
        if not matches:
            return None
        pid = matches[0]["player_id"]
        profile = get_player_profile(conn, pid)

    form_5 = get_player_recent_form(conn, pid, window=5)
    form_3 = get_player_recent_form(conn, pid, window=3)
    history = get_player_fixture_history(conn, pid, limit=5)

    return {
        "profile": profile,
        "form_5": form_5,
        "form_3": form_3,
        "history": history,
    }


def get_fixture_player_overview(
    conn: "duckdb.DuckDBPyConnection",
    provider_fixture_id: int,
    top_n: int = 3,
) -> dict:
    """Return top players per team for a fixture (for /partido 'Jugadores clave')."""
    from app.data.local.player_intelligence_repo import get_player_recent_form

    try:
        rows = conn.execute(
            """
            SELECT player_id, player_name, team_id, team_name, position,
                   goals_total, assists, shots_total, rating
            FROM player_fixture_stats
            WHERE provider_fixture_id = ? AND minutes > 30
            ORDER BY (COALESCE(rating, 0) + goals_total * 0.5) DESC
            LIMIT ?
            """,
            [provider_fixture_id, top_n * 2],
        ).fetchall()
    except Exception:
        return {}

    if not rows:
        return {}

    cols = ["player_id", "player_name", "team_id", "team_name", "position",
            "goals_total", "assists", "shots_total", "rating"]
    by_team: dict[int, list[dict]] = {}
    for r in rows:
        p = dict(zip(cols, r))
        tid = p["team_id"]
        if tid not in by_team:
            by_team[tid] = []
        if len(by_team[tid]) < top_n:
            form = get_player_recent_form(conn, p["player_id"], window=5)
            p["trend"] = form.get("trend_label") if form else None
            by_team[tid].append(p)

    return by_team


def get_hot_players_formatted(
    conn: "duckdb.DuckDBPyConnection",
    league_id: int | None = None,
    market_key: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Return hot players with formatted data for Telegram."""
    from app.data.local.player_intelligence_repo import get_hot_players
    return get_hot_players(conn, league_id=league_id, market_key=market_key, limit=limit)
