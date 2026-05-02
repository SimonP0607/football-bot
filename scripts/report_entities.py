#!/usr/bin/env python
"""Entity catalog reports: search teams, players, squad and league views.

Usage:
    python scripts/report_entities.py --team "Manchester United"
    python scripts/report_entities.py --team-id 33 --squad
    python scripts/report_entities.py --player "Salah"
    python scripts/report_entities.py --league 39 --season 2025
    python scripts/report_entities.py --league 39 --season 2025 --squad
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# Force UTF-8 output so non-ASCII player/team names print correctly on Windows
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401 -- loads .env


def _open_conn():
    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)
    return conn


def _check_has_tables(conn) -> bool:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main' AND table_name='team_identity'"
    ).fetchall()
    return bool(rows)


def _print_team(team: dict, memberships: list[dict]) -> None:
    print()
    print(f"  Equipo: {team['name']}")
    if team.get("code"):
        print(f"  Codigo: {team['code']}")
    print(f"  Pais:   {team.get('country') or '?'}")
    tipo = "Seleccion nacional" if team.get("is_national") else "Club"
    print(f"  Tipo:   {tipo}")
    if team.get("founded"):
        print(f"  Fundado: {team['founded']}")
    if team.get("venue_name"):
        city = f" ({team['venue_city']})" if team.get("venue_city") else ""
        cap = f" cap. {team['venue_capacity']}" if team.get("venue_capacity") else ""
        print(f"  Estadio: {team['venue_name']}{city}{cap}")

    if memberships:
        print(f"\n  Competiciones ({len(memberships)}):")
        for m in memberships:
            ct = m.get("competition_type") or "?"
            print(
                f"    league={m['provider_league_id']} season={m['season']} "
                f"type={ct} scope={m.get('scope', '?')}"
            )
    else:
        print("\n  Sin historial de competicion en el catalogo.")


def _print_squad(squad: list[dict], team_name: str) -> None:
    if not squad:
        print(f"\n  Sin plantilla registrada para {team_name}.")
        print("  Ejecuta: python scripts/sync_entity_catalog.py --execute --include-squads ...")
        return
    print(f"\n  Plantilla {team_name} ({len(squad)} jugadores):")
    pos_order = {"Goalkeeper": 0, "Defender": 1, "Midfielder": 2, "Attacker": 3}
    squad_sorted = sorted(squad, key=lambda p: pos_order.get(p.get("position", ""), 9))
    cur_pos = None
    for p in squad_sorted:
        pos = p.get("position") or "?"
        if pos != cur_pos:
            print(f"\n    [{pos}]")
            cur_pos = pos
        num = f"#{p['number']}" if p.get("number") else "   "
        nat = p.get("nationality") or ""
        age = f"(edad {p['age']})" if p.get("age") else ""
        print(f"      {num:<5} {p['name']:<28} {nat:<15} {age}")


def cmd_team(args: argparse.Namespace) -> None:
    conn = _open_conn()
    if not _check_has_tables(conn):
        print("Tablas de entidades no encontradas. Ejecuta init_local_db.py")
        return

    from app.data.local.entity_repo import find_team, get_team_memberships, get_squad_for_team

    if args.team_id:
        rows = conn.execute(
            "SELECT provider_team_id, name, code, country, is_national, "
            "founded, venue_name, venue_city, venue_capacity "
            "FROM team_identity WHERE provider_team_id = ?",
            [args.team_id]
        ).fetchall()
        teams = [
            dict(zip([
                "provider_team_id", "name", "code", "country", "is_national",
                "founded", "venue_name", "venue_city", "venue_capacity"
            ], r)) for r in rows
        ]
    else:
        teams = find_team(conn, args.team)

    if not teams:
        q = args.team_id or args.team
        print(f"\n  Sin resultados para: {q}")
        print("  Ejecuta sync_entity_catalog.py para cargar equipos.")
        return

    for team in teams[:5]:
        memberships = get_team_memberships(conn, team["provider_team_id"])
        _print_team(team, memberships)

        if args.squad:
            season_hint = args.season
            squad = get_squad_for_team(conn, team["provider_team_id"], season=season_hint)
            _print_squad(squad, team["name"])

    if len(teams) > 5:
        print(f"\n  ... y {len(teams) - 5} resultado(s) mas. Usa --team-id para busqueda exacta.")


def cmd_player(args: argparse.Namespace) -> None:
    conn = _open_conn()
    if not _check_has_tables(conn):
        print("Tablas de entidades no encontradas. Ejecuta init_local_db.py")
        return

    from app.data.local.entity_repo import find_player, get_squad_for_team

    players = find_player(conn, args.player)
    if not players:
        print(f"\n  Sin resultados para: {args.player}")
        print("  Los jugadores se cargan con --include-squads en sync_entity_catalog.py")
        return

    for p in players[:10]:
        print()
        print(f"  Jugador: {p['name']}")
        print(f"  ID:      {p['provider_player_id']}")
        if p.get("nationality"):
            print(f"  Nac:     {p['nationality']}")
        if p.get("age"):
            print(f"  Edad:    {p['age']}")
        if p.get("injured"):
            print("  Estado:  LESIONADO")

        # Find teams
        teams_rows = conn.execute(
            """
            SELECT t.name, s.season, s.position, s.number, s.provider_league_id
            FROM squad_membership s
            JOIN team_identity t ON t.provider_team_id = s.provider_team_id
            WHERE s.provider_player_id = ?
            ORDER BY s.season DESC NULLS LAST
            """,
            [p["provider_player_id"]]
        ).fetchall()
        if teams_rows:
            print(f"  Equipos ({len(teams_rows)}):")
            for r in teams_rows:
                pos = r[2] or "?"
                num = f"#{r[3]}" if r[3] else ""
                season = r[1] or "?"
                print(f"    {r[0]} (season={season}, {pos} {num})")
        else:
            print("  Sin equipos registrados en plantilla.")


def cmd_league(args: argparse.Namespace) -> None:
    conn = _open_conn()
    if not _check_has_tables(conn):
        print("Tablas de entidades no encontradas. Ejecuta init_local_db.py")
        return

    from app.data.local.entity_repo import get_teams_for_league_season, get_squad_for_team

    season = args.season or 2025
    teams = get_teams_for_league_season(conn, args.league, season)
    if not teams:
        print(f"\n  Sin equipos para league={args.league} season={season}")
        print(f"  Ejecuta: python scripts/sync_entity_catalog.py --execute --league {args.league} --season {season}")
        return

    print()
    print(f"  Liga: {args.league}  Season: {season}")
    print(f"  Equipos: {len(teams)}")
    print()

    clubs = [t for t in teams if not t.get("is_national")]
    nationals = [t for t in teams if t.get("is_national")]

    if nationals:
        print(f"  Selecciones nacionales ({len(nationals)}):")
        for t in nationals:
            print(f"    {t['provider_team_id']:>6}  {t['name']:<30} {t.get('country', '')}")

    if clubs:
        print()
        print(f"  Clubes ({len(clubs)}):")
        print(f"  {'ID':>6}  {'Nombre':<30} {'Pais':<15} {'Fundado':>7}")
        print("  " + "-" * 60)
        for t in clubs:
            founded = t.get("founded") or ""
            print(
                f"  {t['provider_team_id']:>6}  {t['name']:<30} "
                f"{(t.get('country') or ''):<15} {founded!s:>7}"
            )

    if args.squad:
        print()
        print("  Plantillas:")
        for t in teams[:args.limit or len(teams)]:
            squad = get_squad_for_team(conn, t["provider_team_id"], season=season)
            if squad:
                print(f"\n  {t['name']} ({len(squad)} jugadores):")
                for p in squad[:5]:
                    pos = p.get("position") or "?"
                    print(f"    {pos:<12} {p['name']}")
                if len(squad) > 5:
                    print(f"    ... y {len(squad)-5} mas")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consulta el catalogo de entidades deportivas"
    )
    parser.add_argument("--team", metavar="NOMBRE",
                        help="Buscar equipo por nombre")
    parser.add_argument("--team-id", type=int, metavar="ID",
                        help="Buscar equipo por provider_team_id")
    parser.add_argument("--player", metavar="NOMBRE",
                        help="Buscar jugador por nombre")
    parser.add_argument("--league", type=int, metavar="ID",
                        help="Mostrar equipos de esta liga")
    parser.add_argument("--season", type=int, metavar="YEAR",
                        help="Temporada (default: 2025)")
    parser.add_argument("--squad", action="store_true",
                        help="Mostrar plantilla")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="Maximo de equipos a mostrar")
    args = parser.parse_args()

    if not any([args.team, args.team_id, args.player, args.league]):
        parser.print_help()
        sys.exit(0)

    if args.team or args.team_id:
        cmd_team(args)
    elif args.player:
        cmd_player(args)
    elif args.league:
        cmd_league(args)

    print()


if __name__ == "__main__":
    main()
