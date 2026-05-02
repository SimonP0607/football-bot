"""Phase 4: Player Availability & Lineups Intelligence service.

Responsibilities:
  - Fetch injuries and lineups from API-Football per fixture.
  - Store raw data in DuckDB (fixture_injuries_history, fixture_lineups_history).
  - Derive player signals (player_availability_signals).
  - Compute team-level impact summary (team_availability_summary).
  - Provide read-only access for /partido and /equipo handlers.

Impact scoring — simple rules, no fake precision:
  Severity score per team:
    +2.0  goalkeeper baja
    +1.5  defender baja
    +1.0  midfielder baja
    +1.0  attacker baja
    +0.5  position unknown (player not in squad catalog)
    +0.5  each suspension (on top of position weight)
  Labels: none(0) · low(0.5–1.5) · medium(2–3.5) · high(4+)

Coverage status:
  'data'      — API returned ≥1 injury rows
  'no_data'   — API returned empty list (no injuries OR no coverage)
  'api_error' — fetch raised an exception
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_POSITION_SCORES: dict[str, float] = {
    "Goalkeeper": 2.0,
    "Defender":   1.5,
    "Midfielder": 1.0,
    "Attacker":   1.0,
}
_SUSPENSION_BONUS = 0.5
_UNKNOWN_SCORE    = 0.5

# Impact thresholds
_HIGH_THRESH   = 4.0
_MEDIUM_THRESH = 2.0
_LOW_THRESH    = 0.5


class PlayerAvailabilityService:
    """Sync and query player availability for fixtures."""

    # ── Public sync interface ─────────────────────────────────────────────────

    async def sync_fixture(
        self,
        conn,                   # duckdb.DuckDBPyConnection
        provider_fixture_id: int,
        home_team_id: int,
        away_team_id: int,
        *,
        fixture_id: int | None = None,
        league_id: int | None = None,
        season: int | None = None,
        sync_injuries: bool = True,
        sync_lineups: bool = True,
        dry_run: bool = True,
        verbose: bool = False,
    ) -> dict:
        """Sync injuries and/or lineups for one fixture.

        Returns:
            {injuries_found, lineups_found, api_calls, skipped, errors,
             home_impact, away_impact, coverage_status}
        """
        from app.data.api_football.endpoints import fetch_injuries, fetch_lineups
        from app.data.local.availability_repo import (
            upsert_injury, upsert_lineup, upsert_signal, upsert_team_summary,
        )

        now = datetime.now(timezone.utc).isoformat()
        stats: dict[str, Any] = {
            "injuries_found": 0,
            "lineups_found": 0,
            "api_calls": 0,
            "skipped": 0,
            "errors": 0,
            "home_impact": "unknown",
            "away_impact": "unknown",
            "coverage_status": "unknown",
        }

        # Dry-run: report estimated cost without any API calls or writes
        if dry_run:
            stats["api_calls"] = (1 if sync_injuries else 0) + (1 if sync_lineups else 0)
            return stats

        team_ids = {home_team_id, away_team_id}

        # ── Injuries ──────────────────────────────────────────────────────────
        if sync_injuries:
            injuries_raw: list[dict] = []
            coverage_status = "no_data"
            try:
                injuries_raw = await fetch_injuries(provider_fixture_id)
                stats["api_calls"] += 1
                coverage_status = "data" if injuries_raw else "no_data"
                stats["injuries_found"] = len(injuries_raw)
                if verbose:
                    print(
                        f"    fixture={provider_fixture_id} injuries={len(injuries_raw)} "
                        f"coverage={coverage_status}"
                    )
            except Exception as exc:
                logger.warning(
                    "fetch_injuries fixture=%s failed: %s", provider_fixture_id, exc
                )
                stats["errors"] += 1
                coverage_status = "api_error"

            stats["coverage_status"] = coverage_status

            # Build squad position lookup from DuckDB catalog (best-effort)
            squad_position: dict[int, dict[str, str]] = {}   # team_id → {player_name: position}
            for tid in team_ids:
                squad_position[tid] = _build_squad_map(conn, tid)

            # Group injuries by team
            injuries_by_team: dict[int, list[dict]] = {tid: [] for tid in team_ids}
            for inj in injuries_raw:
                tid = inj.get("team_id")
                if tid in injuries_by_team:
                    injuries_by_team[tid].append(inj)

            if not dry_run:
                for inj in injuries_raw:
                    upsert_injury(conn, {
                        **inj,
                        "provider_fixture_id": provider_fixture_id,
                        "fixture_id": fixture_id,
                        "league_id": league_id,
                        "season": season,
                        "fetched_at": now,
                        "raw_json": json.dumps(inj),
                    })

                # Derive signals
                for tid, team_injuries in injuries_by_team.items():
                    pos_map = squad_position.get(tid, {})
                    for inj in team_injuries:
                        pname = inj.get("player_name") or ""
                        inj_type = inj.get("type") or "injured"
                        position = pos_map.get(pname)
                        severity = _injury_severity(inj_type, position)
                        confidence = "high" if inj.get("player_id") and position else (
                            "medium" if inj.get("player_id") else "low"
                        )
                        signal_type = "suspension" if "suspend" in inj_type.lower() else "injury"
                        upsert_signal(conn, {
                            "provider_fixture_id": provider_fixture_id,
                            "fixture_id": fixture_id,
                            "team_id": tid,
                            "player_id": inj.get("player_id"),
                            "player_name": pname,
                            "signal_type": signal_type,
                            "severity": severity,
                            "confidence": confidence,
                            "reason": inj.get("reason"),
                            "position": position,
                        })

            # Compute team summaries
            for tid in (home_team_id, away_team_id):
                team_injuries = injuries_by_team.get(tid, [])
                pos_map = squad_position.get(tid, {})
                score, label = _compute_impact(team_injuries, pos_map)

                if tid == home_team_id:
                    stats["home_impact"] = label
                else:
                    stats["away_impact"] = label

                if not dry_run:
                    suspended = sum(
                        1 for i in team_injuries
                        if "suspend" in (i.get("type") or "").lower()
                    )
                    upsert_team_summary(conn, {
                        "provider_fixture_id": provider_fixture_id,
                        "fixture_id": fixture_id,
                        "team_id": tid,
                        "missing_count": len(team_injuries),
                        "suspended_count": suspended,
                        "injury_count": len(team_injuries) - suspended,
                        "severity_score": score,
                        "impact_label": label,
                        "coverage_status": coverage_status,
                    })

            # For teams with no injuries returned and coverage was "no_data",
            # still write a summary so the formatter knows we checked.
            if not dry_run and coverage_status in ("no_data", "api_error"):
                for tid in (home_team_id, away_team_id):
                    upsert_team_summary(conn, {
                        "provider_fixture_id": provider_fixture_id,
                        "fixture_id": fixture_id,
                        "team_id": tid,
                        "missing_count": 0,
                        "suspended_count": 0,
                        "injury_count": 0,
                        "severity_score": 0.0,
                        "impact_label": "unknown",
                        "coverage_status": coverage_status,
                    })

        # ── Lineups ───────────────────────────────────────────────────────────
        if sync_lineups:
            try:
                lineups_raw = await fetch_lineups(provider_fixture_id)
                stats["api_calls"] += 1
                stats["lineups_found"] = len(lineups_raw)
                if verbose:
                    print(
                        f"    fixture={provider_fixture_id} lineups={len(lineups_raw)}"
                    )
                if not dry_run:
                    for side, lu in lineups_raw.items():
                        tid = lu.get("team_id")
                        if not tid:
                            continue
                        upsert_lineup(conn, {
                            "provider_fixture_id": provider_fixture_id,
                            "fixture_id": fixture_id,
                            "league_id": league_id,
                            "season": season,
                            "team_id": tid,
                            "formation": lu.get("formation"),
                            "coach_name": lu.get("coach"),
                            "fetched_at": now,
                            "raw_json": json.dumps(lu.get("start_xi", [])),
                        })
            except Exception as exc:
                logger.warning(
                    "fetch_lineups fixture=%s failed: %s", provider_fixture_id, exc
                )
                stats["errors"] += 1

        return stats

    # ── Public read interface ─────────────────────────────────────────────────

    def get_availability_summary(
        self,
        conn,
        provider_fixture_id: int,
    ) -> dict | None:
        """Return combined availability dict for /partido display, or None."""
        from app.data.local.availability_repo import get_availability_for_fixture
        try:
            return get_availability_for_fixture(conn, provider_fixture_id)
        except Exception as exc:
            logger.debug(
                "get_availability_summary fixture=%s error: %s", provider_fixture_id, exc
            )
            return None


# ── Module-level helpers ──────────────────────────────────────────────────────


def _build_squad_map(conn, team_id: int) -> dict[str, str]:
    """Return {player_name: position} from the DuckDB squad catalog."""
    try:
        rows = conn.execute(
            """
            SELECT p.name, s.position
            FROM squad_membership s
            JOIN player_identity p ON p.provider_player_id = s.provider_player_id
            WHERE s.provider_team_id = ? AND s.position IS NOT NULL
            """,
            [team_id],
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def _injury_severity(injury_type: str, position: str | None) -> str:
    """Map injury type + position to a severity label."""
    if not position:
        return "low"
    if position == "Goalkeeper":
        return "high"
    if position == "Defender":
        return "medium"
    return "low"


def _compute_impact(
    injuries: list[dict],
    pos_map: dict[str, str],
) -> tuple[float, str]:
    """Compute (severity_score, impact_label) for a team's injury list.

    Uses simple additive scoring — no false precision.
    """
    if not injuries:
        return 0.0, "none"

    score = 0.0
    for inj in injuries:
        pname = inj.get("player_name") or ""
        position = pos_map.get(pname)
        inj_type = (inj.get("type") or "").lower()

        base = _POSITION_SCORES.get(position, _UNKNOWN_SCORE) if position else _UNKNOWN_SCORE
        score += base
        if "suspend" in inj_type:
            score += _SUSPENSION_BONUS

    if score >= _HIGH_THRESH:
        label = "high"
    elif score >= _MEDIUM_THRESH:
        label = "medium"
    elif score >= _LOW_THRESH:
        label = "low"
    else:
        label = "none"

    return round(score, 2), label


player_availability_service = PlayerAvailabilityService()
