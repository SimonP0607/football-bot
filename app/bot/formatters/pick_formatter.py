"""Telegram message formatters for picks and status summaries.

All output uses HTML parse mode to avoid MarkdownV2 escaping issues.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.core.config import settings

if TYPE_CHECKING:
    from app.data.db_health import SchemaStatus


def _kickoff_str(kickoff_at: str) -> str:
    """Convert ISO kickoff string to a readable local time."""
    try:
        tz = ZoneInfo(settings.default_timezone)
        dt = datetime.fromisoformat(kickoff_at).astimezone(tz)
        return dt.strftime("%H:%M")
    except Exception:
        return kickoff_at


def _pct(value: float) -> str:
    """Format a probability as a percentage string."""
    return f"{value * 100:.1f}%"


def _market_label(market: str) -> str:
    labels = {"1X2": "Resultado 1X2", "OU25": "Más/Menos 2.5 goles", "BTTS": "Ambos anotan"}
    return labels.get(market, market)


def _selection_label(market: str, selection: str) -> str:
    labels: dict[tuple[str, str], str] = {
        ("1X2", "Home"): "Local",
        ("1X2", "Draw"): "Empate",
        ("1X2", "Away"): "Visitante",
        ("OU25", "Over 2.5"): "Más de 2.5",
        ("OU25", "Under 2.5"): "Menos de 2.5",
        ("BTTS", "Yes"): "Sí anotan ambos",
        ("BTTS", "No"): "No anotan ambos",
    }
    return labels.get((market, selection), selection)


def format_picks(
    predictions: list[dict],
    fixtures: list[dict],
    team_names: dict[int, str],
    league_names: dict[int, str],
) -> str:
    """Build the HTML message shown by /hoy and /top.

    Args:
        predictions: Rows from ``prediction_repo``.
        fixtures: Rows from ``fixture_repo.get_fixtures_today``.
        team_names: Mapping of internal team_id → team name.
        league_names: Mapping of internal league_id → league name.

    Returns:
        HTML-formatted string ready for ``reply_text(..., parse_mode='HTML')``.
    """
    if not predictions:
        return (
            "No hay picks publicables para hoy.\n\n"
            "Si ya aplicaste las migraciones, sincroniza con:\n"
            "<code>python scripts/sync_today.py</code>"
        )

    fixture_by_id = {f["id"]: f for f in fixtures}
    lines: list[str] = []

    for pred in predictions:
        fix = fixture_by_id.get(pred["fixture_id"])
        if not fix:
            continue

        home = team_names.get(fix["home_team_id"], "Local")
        away = team_names.get(fix["away_team_id"], "Visitante")
        league = league_names.get(fix["league_id"], "")
        kickoff = _kickoff_str(fix["kickoff_at"])

        market_lbl = _market_label(pred["market"])
        pick_lbl = _selection_label(pred["market"], pred["recommended_pick"])
        edge_pct = _pct(pred["edge"])
        conf_pct = _pct(pred["confidence_score"])
        impl_pct = _pct(pred["implied_probability"])
        best_odd = pred["argument_json"].get("best_odd", "?")
        best_bk = pred["argument_json"].get("best_bookmaker", "")

        block = (
            f"<b>{home} vs {away}</b>\n"
            f"⚽ {league} · {kickoff}\n"
            f"\n"
            f"📊 <b>{market_lbl} — {pick_lbl}</b>\n"
            f"📈 Edge: <b>+{edge_pct}</b>  |  Confianza: <b>{conf_pct}</b>\n"
            f"💰 Mejor cuota: <b>{best_odd}</b>"
            + (f" ({best_bk})" if best_bk else "")
            + f"\n"
            f"📋 Prob. justa: {conf_pct} → Implícita: {impl_pct}"
        )
        lines.append(block)

    header = f"<b>Picks del día</b> ({len(predictions)})\n"
    separator = "\n\n" + "─" * 30 + "\n\n"
    return header + separator.join(lines)


def format_estado(schema: SchemaStatus, counts: dict | None) -> str:
    """Build the HTML /estado message showing full system health.

    Args:
        schema: Result of ``db_health.check_schema()`` — describes connection
                and schema readiness.
        counts: Dict with keys ``fixtures_today``, ``odds_rows``,
                ``picks_today``; or None if the query failed.

    Returns:
        HTML-formatted string for ``reply_text(..., parse_mode='HTML')``.
    """
    lines: list[str] = ["<b>Estado del sistema</b>", ""]

    # ── Telegram (always OK if the handler is running) ─────────────────────────
    lines.append("📡 Telegram:              <b>OK</b>")

    # ── Supabase connection ────────────────────────────────────────────────────
    if schema.connection_ok:
        lines.append("🔌 Supabase:              <b>OK</b>")
    else:
        lines.append("🔌 Supabase:              <b>FAIL ✗</b>")
        if schema.error:
            lines.append(f"   └ {schema.error}")
        lines.append("")
        lines.append("Verifica <code>SUPABASE_URL</code> y <code>SUPABASE_KEY</code> en <code>.env</code>")
        return "\n".join(lines)

    # ── Schema ────────────────────────────────────────────────────────────────
    if schema.tables_ok:
        lines.append("🗄️  Schema:                <b>aplicado ✓</b>")
    else:
        lines.append("🗄️  Schema:                <b>NO APLICADO ✗</b>")
        missing = "  ".join(schema.missing_tables)
        lines.append(f"   └ Faltantes: <code>{missing}</code>")
        lines.append("")
        lines.append("<b>Aplica las migraciones en Supabase → SQL Editor:</b>")
        lines.append("  <code>sql/migrations/001_init.sql</code>")
        lines.append("  <code>sql/migrations/002_constraints_and_indexes.sql</code>")
        lines.append("")
        lines.append("Después reinicia el bot y ejecuta:")
        lines.append("  <code>python scripts/sync_today.py</code>")
        return "\n".join(lines)

    # ── Data counts (only when schema is OK) ───────────────────────────────────
    lines.append("")
    if counts is None:
        lines.append("❌ Error al obtener conteos — revisa los logs")
    else:
        fx = counts.get("fixtures_today", 0)
        odds = counts.get("odds_rows", 0)
        picks = counts.get("picks_today", 0)
        lines.append(f"📅 Fixtures hoy:          <b>{fx}</b>")
        lines.append(f"💹 Cuotas almacenadas:    <b>{odds}</b>")
        lines.append(f"🎯 Picks publicables:     <b>{picks}</b>")

        if fx == 0:
            lines.append("")
            lines.append("Sin datos para hoy. Ejecuta:")
            lines.append("  <code>python scripts/sync_today.py</code>")

    return "\n".join(lines)
