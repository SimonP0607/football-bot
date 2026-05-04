"""Phase 11: Telegram handlers for player intelligence.

Commands:
  /jugadorstats <nombre o id> — player profile + form + analysis
  /props <fixture_id>         — prop signals for a fixture
  /playerhot [liga|mercado]   — players with best recent trend
"""

from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.utils import send_html

logger = logging.getLogger(__name__)

_DISCLAIMER = "\n\n<i>Analisis informativo. No es una apuesta. Solo datos del modelo.</i>"

_MARKET_LABELS = {
    "player_goal_signal": "Gol",
    "player_assist_signal": "Asistencia",
    "player_shot_signal": "Tiro",
    "player_shot_on_target_signal": "Tiro al arco",
    "player_card_signal": "Tarjeta",
    "player_foul_signal": "Falta",
    "player_minutes_signal": "Minutos",
}

_TREND_ICON = {
    "hot": "🔥", "stable": "📊", "declining": "📉",
    "low_minutes": "⏱", "insufficient_data": "❓",
}

_STATUS_ICON = {
    "recommended": "✅", "observed": "👁", "no_data": "—", "high_risk": "⚠️",
}


def _esc(text: object) -> str:
    import html
    return html.escape(str(text)) if text is not None else ""


@require_auth
async def jugadorstats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/jugadorstats <nombre o id> — player profile + form analysis."""
    query = " ".join(context.args or []).strip()
    if not query:
        await send_html(
            update.message,
            "Uso: <code>/jugadorstats &lt;nombre o id&gt;</code>\n"
            "Ejemplo: <code>/jugadorstats Salah</code>",
        )
        return

    logger.info("/jugadorstats query=%r user_id=%s", query, update.effective_user.id)

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        conn = get_local_db()
        init_schema(conn)
    except Exception as exc:
        await send_html(update.message, f"Error al abrir la base de datos local: {exc}")
        return

    from app.services.player_intelligence_service import get_player_analysis
    analysis = get_player_analysis(conn, query)

    if not analysis:
        await send_html(
            update.message,
            f"No se encontraron estadísticas para <b>{_esc(query)}</b>.\n\n"
            "Asegúrate de haber sincronizado datos:\n"
            "<code>python scripts/sync_player_stats_today.py --days 7 --execute</code>\n"
            "<code>python scripts/build_player_profiles.py --all --execute</code>",
        )
        return

    await send_html(update.message, _format_player_analysis(analysis) + _DISCLAIMER)


def _format_player_analysis(analysis: dict) -> str:
    profile = analysis.get("profile") or {}
    form_5 = analysis.get("form_5")
    form_3 = analysis.get("form_3")
    history = analysis.get("history") or []

    name = profile.get("player_name") or "Jugador desconocido"
    pos = profile.get("position") or "—"
    team = profile.get("team_name") or "—"
    season = profile.get("season") or "—"
    apps = profile.get("appearances", 0)
    avg_mins = profile.get("avg_minutes")
    goals = profile.get("goals", 0)
    assists = profile.get("assists", 0)
    shots = profile.get("shots_total", 0)
    shots_on = profile.get("shots_on", 0)
    yellows = profile.get("yellow_cards", 0)
    reds = profile.get("red_cards", 0)
    avg_rating = profile.get("avg_rating")

    trend_label = (form_5 or {}).get("trend_label", "insufficient_data")
    trend_icon = _TREND_ICON.get(trend_label, "📊")

    lines: list[str] = [
        f"<b>{_esc(name)}</b> {trend_icon}",
        f"Equipo: {_esc(team)} · Temporada {season}",
        f"Posición: {_esc(pos)}",
        "",
        f"<b>Perfil de temporada</b>",
        f"Partidos: {apps} · Prom: {avg_mins:.0f}' " if avg_mins else f"Partidos: {apps}",
        f"Goles: {goals} · Asistencias: {assists}",
        f"Tiros: {shots} ({shots_on} al arco)",
        f"Tarjetas: {yellows}🟡 {reds}🔴",
    ]
    if avg_rating:
        lines.append(f"Rating promedio: {avg_rating:.2f}")

    if form_5:
        lines.append("")
        lines.append("<b>Forma reciente (5 partidos)</b>")
        n5 = form_5.get("matches_count", 0)
        lines.append(f"Partidos: {n5} · Tendencia: {_esc(trend_label)}")
        lines.append(
            f"Goles: {form_5.get('goals', 0)} · Asistencias: {form_5.get('assists', 0)}"
        )
        lines.append(
            f"Tiros: {form_5.get('shots_total', 0)} · Faltas: {form_5.get('fouls_committed', 0)}"
        )
        if form_5.get("avg_rating"):
            lines.append(f"Rating: {form_5['avg_rating']:.2f}")

    if form_3:
        n3 = form_3.get("matches_count", 0)
        if n3 >= 2:
            lines.append(f"Últimos 3: {form_3.get('goals',0)}G / {form_3.get('assists',0)}A")

    if history:
        lines.append("")
        lines.append("<b>Últimos partidos</b>")
        for h in history[:4]:
            mins = h.get("minutes", 0)
            g = h.get("goals_total", 0)
            a = h.get("assists", 0)
            rating = h.get("rating")
            r_str = f" [{rating:.1f}]" if rating else ""
            lines.append(
                f"  {_esc(h.get('team_name',''))} — {mins}' — {g}G {a}A{r_str}"
            )

    return "\n".join(lines)


@require_auth
async def props_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/props <fixture_id> — prop signals for all players in a fixture."""
    args = context.args or []
    if not args:
        await send_html(
            update.message,
            "Uso: <code>/props &lt;fixture_id&gt;</code>\n"
            "Ejemplo: <code>/props 1060362</code>",
        )
        return

    if not args[0].isdigit():
        await send_html(update.message, "El fixture_id debe ser un número.")
        return

    provider_fixture_id = int(args[0])
    logger.info("/props fixture=%s user_id=%s", provider_fixture_id, update.effective_user.id)

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        conn = get_local_db()
        init_schema(conn)
    except Exception as exc:
        await send_html(update.message, f"Error al abrir la base de datos local: {exc}")
        return

    from app.data.local.player_intelligence_repo import get_fixture_player_signals
    signals = get_fixture_player_signals(conn, provider_fixture_id)

    if not signals:
        await send_html(
            update.message,
            f"No hay señales para el fixture <code>{provider_fixture_id}</code>.\n\n"
            "Genera señales con:\n"
            f"<code>python scripts/generate_player_signals.py --fixture {provider_fixture_id} --execute</code>",
        )
        return

    await send_html(
        update.message,
        _format_prop_signals(provider_fixture_id, signals) + _DISCLAIMER,
    )


def _format_prop_signals(fixture_id: int, signals: list[dict]) -> str:
    # Group by market_key, show top 8 total
    shown = [s for s in signals if s.get("status") not in ("no_data",)][:8]

    if not shown:
        return f"<b>Señales del fixture {fixture_id}</b>\n\nTodos los jugadores sin datos suficientes."

    lines = [f"<b>Señales — Fixture {fixture_id}</b>", ""]

    by_market: dict[str, list[dict]] = {}
    for s in shown:
        mk = s.get("market_key", "other")
        by_market.setdefault(mk, []).append(s)

    for mk, mkt_signals in list(by_market.items())[:4]:
        label = _MARKET_LABELS.get(mk, mk)
        lines.append(f"<b>{_esc(label)}</b>")
        for s in mkt_signals[:3]:
            icon = _STATUS_ICON.get(s.get("status", "observed"), "👁")
            conf = s.get("confidence_score") or 0
            name = s.get("player_name") or "?"
            reason = s.get("reason_text") or ""
            lines.append(f"  {icon} {_esc(name)} — {conf*100:.0f}%")
            if reason:
                lines.append(f"     <i>{_esc(reason)}</i>")
        lines.append("")

    return "\n".join(lines).rstrip()


@require_auth
async def playerhot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/playerhot [liga_id|mercado] — players with best recent trend."""
    args = context.args or []
    league_id: int | None = None
    market_key: str | None = None

    if args:
        arg = args[0]
        if arg.isdigit():
            league_id = int(arg)
        elif arg in ("goals", "goles"):
            market_key = "player_goal_signal"
        elif arg in ("shots", "tiros"):
            market_key = "player_shot_signal"
        elif arg in ("cards", "tarjetas"):
            market_key = "player_card_signal"
        elif arg in ("assists", "asistencias"):
            market_key = "player_assist_signal"

    logger.info("/playerhot league=%s market=%s user_id=%s", league_id, market_key, update.effective_user.id)

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        conn = get_local_db()
        init_schema(conn)
    except Exception as exc:
        await send_html(update.message, f"Error al abrir la base de datos local: {exc}")
        return

    from app.services.player_intelligence_service import get_hot_players_formatted
    players = get_hot_players_formatted(conn, league_id=league_id, market_key=market_key, limit=10)

    if not players:
        await send_html(
            update.message,
            "No hay jugadores en racha para el filtro seleccionado.\n\n"
            "Genera perfiles primero:\n"
            "<code>python scripts/build_player_profiles.py --all --execute</code>",
        )
        return

    await send_html(update.message, _format_hot_players(players, league_id, market_key) + _DISCLAIMER)


def _format_hot_players(players: list[dict], league_id: int | None, market_key: str | None) -> str:
    title = "Jugadores en racha"
    if league_id:
        title += f" — Liga {league_id}"
    if market_key:
        label = _MARKET_LABELS.get(market_key, market_key)
        title += f" — {label}"

    lines = [f"<b>{title}</b>", ""]
    for p in players[:8]:
        trend = p.get("trend_label", "stable")
        icon = _TREND_ICON.get(trend, "📊")
        name = p.get("player_name") or "?"
        team = p.get("team_name") or "?"
        goals = p.get("goals", 0)
        assists = p.get("assists", 0)
        n = p.get("matches_count", 0)
        rating = p.get("avg_rating")
        r_str = f" [{rating:.1f}]" if rating else ""
        lines.append(
            f"{icon} <b>{_esc(name)}</b> ({_esc(team)})"
        )
        lines.append(f"   {goals}G / {assists}A en {n} partidos{r_str}")

    return "\n".join(lines)
