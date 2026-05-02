#!/usr/bin/env python
"""Entity catalog audit: counts, league coverage, potential duplicates.

Usage:
    python scripts/audit_entities.py
    python scripts/audit_entities.py --league 39 --season 2025
    python scripts/audit_entities.py --players
    python scripts/audit_entities.py --duplicates
    python scripts/audit_entities.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401 -- loads .env


def _open_conn():
    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)
    return conn


def _safe(conn, sql: str, params: list | None = None) -> list:
    try:
        return conn.execute(sql, params or []).fetchall()
    except Exception:
        return []


def main() -> None:
    parser = argparse.ArgumentParser(description="Auditoria del catalogo de entidades")
    parser.add_argument("--league", type=int, metavar="ID",
                        help="Filtrar por provider_league_id")
    parser.add_argument("--season", type=int, metavar="YEAR",
                        help="Filtrar por temporada")
    parser.add_argument("--players", action="store_true",
                        help="Incluir analisis de jugadores y plantillas")
    parser.add_argument("--duplicates", action="store_true",
                        help="Mostrar posibles duplicados por nombre")
    parser.add_argument("--json", action="store_true",
                        help="Salida JSON")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)

    try:
        conn = _open_conn()
    except Exception as exc:
        print(f"ERROR: No se pudo abrir DuckDB: {exc}")
        print("Ejecuta: python scripts/init_local_db.py")
        sys.exit(1)

    # -- Check tables exist
    tables = _safe(
        conn,
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    )
    table_names = {r[0] for r in tables}
    if "team_identity" not in table_names:
        print()
        print("Tablas de entidades no encontradas.")
        print("Ejecuta: python scripts/init_local_db.py")
        print()
        sys.exit(0)

    # -- Counts
    counts: dict = {}
    for tbl, col in [
        ("team_identity", "provider_team_id"),
        ("player_identity", "provider_player_id"),
        ("team_season_membership", "id"),
        ("squad_membership", "id"),
        ("entity_sync_runs", "id"),
    ]:
        rows = _safe(conn, f"SELECT COUNT(*) FROM {tbl}")
        counts[tbl] = rows[0][0] if rows else 0

    clubs = _safe(conn, "SELECT COUNT(*) FROM team_identity WHERE is_national = FALSE")
    nationals = _safe(conn, "SELECT COUNT(*) FROM team_identity WHERE is_national = TRUE")
    counts["clubs"] = clubs[0][0] if clubs else 0
    counts["national_teams"] = nationals[0][0] if nationals else 0

    # -- Leagues with/without teams
    leagues_with = _safe(
        conn,
        "SELECT DISTINCT provider_league_id FROM team_season_membership"
        + (" WHERE provider_league_id = ?" if args.league else ""),
        [args.league] if args.league else [],
    )
    leagues_with_ids = {r[0] for r in leagues_with}

    # Tracked leagues from Supabase
    try:
        from app.data.repositories.fixture_repo import get_tracked_league_seasons
        tracked = get_tracked_league_seasons()
        tracked_ids = set(tracked.keys())
    except Exception:
        tracked = {}
        tracked_ids = set()

    leagues_without = tracked_ids - leagues_with_ids

    # -- Teams in multiple competitions
    multi_comp = _safe(
        conn,
        """
        SELECT provider_team_id, COUNT(DISTINCT provider_league_id) AS n_leagues
        FROM team_season_membership
        GROUP BY provider_team_id
        HAVING COUNT(DISTINCT provider_league_id) > 1
        ORDER BY n_leagues DESC
        LIMIT 10
        """
    )

    # -- Country breakdown
    country_counts = _safe(
        conn,
        "SELECT country, COUNT(*) FROM team_identity GROUP BY country ORDER BY 2 DESC LIMIT 15"
    )

    # -- League summary (optional filter)
    league_summary_sql = """
        SELECT m.provider_league_id, m.season,
               COUNT(DISTINCT m.provider_team_id) AS teams,
               COUNT(DISTINCT m.scope) AS scopes,
               MAX(t.is_national::INTEGER) AS has_national
        FROM team_season_membership m
        JOIN team_identity t ON t.provider_team_id = m.provider_team_id
    """
    league_params: list = []
    if args.league:
        league_summary_sql += " WHERE m.provider_league_id = ?"
        league_params.append(args.league)
    if args.season:
        league_summary_sql += (" AND" if args.league else " WHERE") + " m.season = ?"
        league_params.append(args.season)
    league_summary_sql += " GROUP BY m.provider_league_id, m.season ORDER BY teams DESC LIMIT 20"
    league_summary = _safe(conn, league_summary_sql, league_params)

    # -- Players / squads (optional)
    players_without_team: list = []
    players_multiple_teams: list = []
    if args.players:
        players_without_team = _safe(
            conn,
            """
            SELECT p.provider_player_id, p.name
            FROM player_identity p
            WHERE NOT EXISTS (
                SELECT 1 FROM squad_membership s WHERE s.provider_player_id = p.provider_player_id
            )
            LIMIT 10
            """
        )
        players_multiple_teams = _safe(
            conn,
            """
            SELECT provider_player_id, COUNT(DISTINCT provider_team_id) AS n_teams
            FROM squad_membership
            GROUP BY provider_player_id
            HAVING COUNT(DISTINCT provider_team_id) > 1
            ORDER BY n_teams DESC
            LIMIT 10
            """
        )

    # -- Potential duplicates
    name_dups: list = []
    if args.duplicates:
        name_dups = _safe(
            conn,
            """
            SELECT name, COUNT(*) AS n, COUNT(DISTINCT country) AS countries
            FROM team_identity
            GROUP BY name
            HAVING COUNT(*) > 1
            ORDER BY n DESC
            LIMIT 20
            """
        )

    # -- Last sync run
    last_run = _safe(
        conn,
        """
        SELECT id, started_at, status, teams_synced, memberships_synced, api_calls
        FROM entity_sync_runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    )

    # ── Output ────────────────────────────────────────────────────────────────

    if args.json:
        output = {
            "generated_at": now.isoformat(),
            "counts": counts,
            "leagues_with_teams": sorted(leagues_with_ids),
            "leagues_without_teams": sorted(leagues_without),
            "multi_competition_teams": [
                {"provider_team_id": r[0], "n_leagues": r[1]} for r in multi_comp
            ],
            "country_breakdown": [
                {"country": r[0], "teams": r[1]} for r in country_counts
            ],
            "league_summary": [
                {
                    "provider_league_id": r[0], "season": r[1],
                    "teams": r[2], "has_national": bool(r[4])
                }
                for r in league_summary
            ],
        }
        print(json.dumps(output, indent=2, default=str))
        return

    print()
    print("=" * 60)
    print("  AUDITORIA DE ENTIDADES")
    print(f"  {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 60)

    print()
    print("  RESUMEN GLOBAL")
    print(f"    Equipos totales:    {counts.get('team_identity', 0)}")
    print(f"      Clubes:           {counts.get('clubs', 0)}")
    print(f"      Selecciones:      {counts.get('national_teams', 0)}")
    print(f"    Memberships:        {counts.get('team_season_membership', 0)}")
    print(f"    Jugadores:          {counts.get('player_identity', 0)}")
    print(f"    Squad entries:      {counts.get('squad_membership', 0)}")
    print(f"    Ligas con equipos:  {len(leagues_with_ids)}")
    print(f"    Ligas rastreadas:   {len(tracked_ids)}")

    if leagues_without:
        print(f"    Ligas sin equipos:  {len(leagues_without)}")
        sample = sorted(leagues_without)[:8]
        print(f"      Muestra: {sample}")

    if not counts.get("team_identity"):
        print()
        print("  Sin datos de entidades.")
        print("  Ejecuta: python scripts/sync_entity_catalog.py --execute --limit 5")
        print()
        print("=" * 60)
        print()
        return

    # League summary
    if league_summary:
        print()
        label = "LIGAS CON EQUIPOS"
        if args.league:
            label += f" (filtro: {args.league})"
        print(f"  {label}")
        print(f"  {'Liga':>6}  {'Season':>6}  {'Equipos':>7}  {'Nacional':>8}")
        print("  " + "-" * 32)
        for r in league_summary[:15]:
            nacional = "SI" if r[4] else "no"
            print(f"  {r[0]:>6}  {r[1]:>6}  {r[2]:>7}  {nacional:>8}")

    # Multi-competition teams
    if multi_comp:
        print()
        print("  EQUIPOS EN MULTIPLES COMPETICIONES (top 10)")
        for r in multi_comp[:10]:
            team_row = _safe(
                conn,
                "SELECT name, country FROM team_identity WHERE provider_team_id = ?",
                [r[0]]
            )
            name = team_row[0][0] if team_row else "?"
            country = team_row[0][1] if team_row else "?"
            print(f"    id={r[0]} {name} ({country}) — {r[1]} ligas")

    # Country breakdown
    if country_counts:
        print()
        print("  EQUIPOS POR PAIS (top 15)")
        for r in country_counts:
            print(f"    {(r[0] or 'desconocido'):<25} {r[1]:>4}")

    # Players / squads
    if args.players:
        print()
        print("  JUGADORES")
        print(f"    Total:              {counts.get('player_identity', 0)}")
        print(f"    Squad entries:      {counts.get('squad_membership', 0)}")

        if players_without_team:
            print(f"    Sin equipo asignado: {len(players_without_team)}")
            for r in players_without_team[:5]:
                print(f"      id={r[0]} {r[1]}")

        if players_multiple_teams:
            print(f"    Con multiples equipos:")
            for r in players_multiple_teams[:5]:
                print(f"      player_id={r[0]} — {r[1]} equipos")

    # Duplicates
    if args.duplicates and name_dups:
        print()
        print("  POSIBLES DUPLICADOS POR NOMBRE")
        for r in name_dups:
            print(f"    '{r[0]}' — {r[1]} registros, {r[2]} paises distintos")

    # Last sync run
    if last_run:
        r = last_run[0]
        print()
        print("  ULTIMO SYNC")
        print(f"    ID:       {r[0]}")
        print(f"    Inicio:   {str(r[1])[:16]}")
        print(f"    Estado:   {r[2]}")
        print(f"    Equipos:  {r[3]}")
        print(f"    API:      {r[5]} llamadas")
    else:
        print()
        print("  Sin historial de sync.")
        print("  Ejecuta: python scripts/sync_entity_catalog.py --execute --limit 5")

    print()
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
