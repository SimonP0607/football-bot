"""Handler for /equipo — search team in the local entity catalog."""

from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.utils import send_html

logger = logging.getLogger(__name__)


def _esc(text: object) -> str:
    import html
    return html.escape(str(text)) if text is not None else ""


@require_auth
async def equipo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/equipo <nombre o id> — buscar equipo en el catálogo local."""
    query = " ".join(context.args or []).strip()
    if not query:
        await update.message.reply_text(
            "Uso: /equipo &lt;nombre o id&gt;\n"
            "Ejemplos:\n"
            "  /equipo Manchester United\n"
            "  /equipo 33",
            parse_mode="HTML",
        )
        return

    logger.info("/equipo query=%r", query)

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        from app.data.local.entity_repo import find_team, get_team_memberships, get_squad_for_team
        conn = get_local_db()
        init_schema(conn)
    except Exception as exc:
        logger.error("/equipo: error abriendo DuckDB — %s", exc)
        await update.message.reply_text("Error interno al acceder al catálogo local.")
        return

    # Try numeric ID first
    teams = []
    if query.isdigit():
        rows = conn.execute(
            "SELECT provider_team_id, name, code, country, is_national, "
            "founded, venue_name, venue_city, venue_capacity "
            "FROM team_identity WHERE provider_team_id = ?",
            [int(query)]
        ).fetchall()
        cols = [
            "provider_team_id", "name", "code", "country", "is_national",
            "founded", "venue_name", "venue_city", "venue_capacity",
        ]
        teams = [dict(zip(cols, r)) for r in rows]
    else:
        teams = find_team(conn, query, limit=5)

    if not teams:
        msg = (
            f"<b>Sin resultados para: {_esc(query)}</b>\n\n"
            "El catálogo de entidades puede estar vacío.\n"
            "Para cargarlo:\n"
            "<code>python scripts/sync_entity_catalog.py --execute --limit 10</code>"
        )
        await send_html(update.message, msg)
        return

    lines: list[str] = []
    for team in teams[:3]:
        tid = team["provider_team_id"]
        tipo = "Selección nacional" if team.get("is_national") else "Club"
        memberships = get_team_memberships(conn, tid)
        squad = get_squad_for_team(conn, tid)
        has_squad = "Sí" if squad else "No"

        lines.append(f"<b>{_esc(team['name'])}</b>")
        if team.get("code"):
            lines.append(f"  Código: {_esc(team['code'])}")
        lines.append(f"  País:   {_esc(team.get('country', '?'))}")
        lines.append(f"  Tipo:   {tipo}")
        if team.get("founded"):
            lines.append(f"  Fundado: {team['founded']}")
        if team.get("venue_name"):
            city = f" ({_esc(team['venue_city'])})" if team.get("venue_city") else ""
            lines.append(f"  Estadio: {_esc(team['venue_name'])}{city}")

        if memberships:
            comps = ", ".join(
                f"{m['provider_league_id']}/{m['season']}" for m in memberships[:3]
            )
            lines.append(f"  Competiciones: {comps}")

        lines.append(f"  Plantilla en catálogo: {has_squad}")

        # Phase 4: recent injuries from availability tables (best-effort)
        try:
            from app.data.local.availability_repo import get_injuries_for_team
            recent_injuries = get_injuries_for_team(conn, tid, limit=5)
            if recent_injuries:
                lines.append(f"  Bajas recientes ({len(recent_injuries)}):")
                for inj in recent_injuries[:3]:
                    itype = inj.get("type") or "injured"
                    lines.append(
                        f"    · {_esc(inj['player_name'])} [{itype}]"
                    )
                if len(recent_injuries) > 3:
                    lines.append(f"    · ... y {len(recent_injuries) - 3} más")
        except Exception:
            pass

        # Phase 11: top players from player_intelligence (best-effort)
        try:
            from app.data.local.player_intelligence_repo import get_team_player_profiles
            top_players = get_team_player_profiles(conn, tid, limit=5)
            if top_players:
                lines.append("  <b>Top jugadores (estadísticas):</b>")
                for p in top_players[:4]:
                    pname = _esc(p.get("player_name") or "")
                    pos = _esc(p.get("position") or "")
                    g = p.get("goals", 0) or 0
                    a = p.get("assists", 0) or 0
                    lines.append(f"    · {pname} ({pos}) — {g}G / {a}A")
                lines.append("  <i>Usa /jugadorstats &lt;nombre&gt; para más detalle</i>")
            else:
                lines.append("  <i>Sin estadísticas profundas cargadas aún</i>")
        except Exception:
            lines.append("  <i>Sin estadísticas profundas cargadas aún</i>")

        lines.append("")

    if len(teams) > 3:
        lines.append(f"<i>... y {len(teams) - 3} resultado(s) más</i>")

    await send_html(update.message, "\n".join(lines))


@require_auth
async def jugador_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/jugador <nombre> — buscar jugador en el catálogo local."""
    query = " ".join(context.args or []).strip()
    if not query:
        await update.message.reply_text(
            "Uso: /jugador &lt;nombre&gt;\n"
            "Ejemplo: /jugador Salah",
            parse_mode="HTML",
        )
        return

    logger.info("/jugador query=%r", query)

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        from app.data.local.entity_repo import find_player
        conn = get_local_db()
        init_schema(conn)
    except Exception as exc:
        logger.error("/jugador: error abriendo DuckDB — %s", exc)
        await update.message.reply_text("Error interno al acceder al catálogo local.")
        return

    players = find_player(conn, query, limit=5)

    if not players:
        msg = (
            f"<b>Sin resultados para: {_esc(query)}</b>\n\n"
            "Los jugadores se cargan con:\n"
            "<code>python scripts/sync_entity_catalog.py --execute --include-squads</code>"
        )
        await send_html(update.message, msg)
        return

    lines: list[str] = []
    for p in players[:5]:
        pid = p["provider_player_id"]
        injured_tag = " 🚑" if p.get("injured") else ""
        lines.append(f"<b>{_esc(p['name'])}</b>{injured_tag}")
        if p.get("nationality"):
            lines.append(f"  Nacionalidad: {_esc(p['nationality'])}")
        if p.get("age"):
            lines.append(f"  Edad: {p['age']}")

        # Teams
        try:
            team_rows = conn.execute(
                """
                SELECT t.name, s.position, s.season
                FROM squad_membership s
                JOIN team_identity t ON t.provider_team_id = s.provider_team_id
                WHERE s.provider_player_id = ?
                ORDER BY s.season DESC NULLS LAST
                LIMIT 3
                """,
                [pid]
            ).fetchall()
            if team_rows:
                teams_str = "; ".join(
                    f"{r[0]} ({r[1] or '?'}, {r[2] or '?'})" for r in team_rows
                )
                lines.append(f"  Equipo(s): {_esc(teams_str)}")
        except Exception:
            pass

        if p.get("injured"):
            lines.append("  <i>Estado: lesionado según último sync</i>")
        else:
            lines.append("  <i>Sin estadísticas profundas en esta fase</i>")

        lines.append("")

    await send_html(update.message, "\n".join(lines))
