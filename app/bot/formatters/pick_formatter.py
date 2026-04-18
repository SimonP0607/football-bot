"""Telegram message formatters for picks, estado, and partido analysis.

All output uses HTML parse mode to avoid MarkdownV2 escaping issues.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.core.config import settings

if TYPE_CHECKING:
    from app.data.db_health import SchemaStatus


# ── Shared helpers ────────────────────────────────────────────────────────────


def _kickoff_str(kickoff_at: str) -> str:
    """Convert ISO kickoff string to a readable local time (HH:MM)."""
    try:
        tz = ZoneInfo(settings.default_timezone)
        dt = datetime.fromisoformat(kickoff_at).astimezone(tz)
        return dt.strftime("%H:%M")
    except Exception:
        return kickoff_at


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _edge_str(edge: float) -> str:
    sign = "+" if edge >= 0 else ""
    return f"{sign}{edge * 100:.1f}%"


def _market_label(market: str) -> str:
    labels = {"1X2": "Resultado 1X2", "OU25": "Más/Menos 2.5", "BTTS": "Ambos anotan"}
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


def _edge_icon(edge: float) -> str:
    if edge >= 0.08:
        return "🔥"
    if edge >= 0.05:
        return "✅"
    if edge >= 0.02:
        return "📊"
    return "⚠️"


# ── /hoy and /top formatter ───────────────────────────────────────────────────


def format_picks(
    predictions: list[dict],
    fixtures: list[dict],
    team_names: dict[int, str],
    league_names: dict[int, str],
) -> str:
    """Build the HTML message shown by /hoy and /top.

    Each pick block shows: match, time, market, odds, edge, confidence,
    implied probability, reasons, and main risk.
    """
    if not predictions:
        return (
            "No hay picks publicables para hoy.\n\n"
            "Si ya aplicaste las migraciones, sincroniza con:\n"
            "<code>python scripts/sync_today.py</code>"
        )

    fixture_by_id = {f["id"]: f for f in fixtures}
    blocks: list[str] = []

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
        edge = pred["edge"]
        icon = _edge_icon(edge)
        arg = pred.get("argument_json") or {}

        best_odd = arg.get("best_odd", "?")
        best_bk = arg.get("best_bookmaker", "")
        reasons: list[str] = arg.get("reasons", [])
        main_risk: str = arg.get("main_risk", "")

        bk_str = f" ({best_bk})" if best_bk else ""

        lines = [
            f"<b>{home} vs {away}</b>",
            f"⚽ {league} · {kickoff}",
            "",
            f"{icon} <b>{market_lbl} — {pick_lbl}</b>",
            f"💰 Cuota: <b>{best_odd}</b>{bk_str}",
            f"📈 Prob. modelo: <b>{_pct(pred['model_probability'])}</b> · "
            f"Implícita: {_pct(pred['implied_probability'])}",
            f"⚡ Edge: <b>{_edge_str(edge)}</b> · Confianza: <b>{_pct(pred['confidence_score'])}</b>",
        ]

        if reasons:
            lines.append("")
            lines.append("📋 <b>Razones:</b>")
            for r in reasons[:3]:
                lines.append(f"  · {r}")

        if main_risk:
            lines.append(f"⚠️ <b>Riesgo:</b> {main_risk}")

        blocks.append("\n".join(lines))

    if not blocks:
        return "No hay picks publicables para hoy."

    header = f"<b>Picks del día</b> ({len(blocks)})\n"
    separator = "\n\n" + "─" * 30 + "\n\n"
    return header + separator.join(blocks)


# ── /partido formatter ────────────────────────────────────────────────────────


def format_partido(
    fixture: dict,
    candidates: list[dict],
    team_names: dict[int, str],
    league_names: dict[int, str],
) -> str:
    """Build the full pre-match analysis message for /partido.

    Shows all evaluated markets with edge, reasons, and a best pick summary.
    """
    home = team_names.get(fixture["home_team_id"], "Local")
    away = team_names.get(fixture["away_team_id"], "Visitante")
    league = league_names.get(fixture["league_id"], "")
    kickoff = _kickoff_str(fixture["kickoff_at"])
    pid = fixture.get("provider_fixture_id", "?")

    lines = [
        f"<b>{home} vs {away}</b>",
        f"⚽ {league}",
        f"🕐 {kickoff} · ID: <code>{pid}</code>",
        "",
    ]

    if not candidates:
        lines.append("Sin cuotas disponibles para analizar este partido.")
        lines.append("")
        lines.append("Ejecuta el sync diario para cargar odds:")
        lines.append("  <code>python scripts/sync_today.py</code>")
        return "\n".join(lines)

    # Group candidates by market, pick best selection per market by edge
    markets_order = ["1X2", "OU25", "BTTS"]
    by_market: dict[str, list[dict]] = {}
    for c in candidates:
        by_market.setdefault(c["market"], []).append(c)

    best_picks: list[dict] = []  # selections with positive edge

    lines.append("📊 <b>ANÁLISIS DE MERCADOS</b>")
    lines.append("")

    for market in markets_order:
        mkt_candidates = by_market.get(market, [])
        if not mkt_candidates:
            continue

        # Sort by edge descending; best (highest-edge) candidate first
        mkt_candidates.sort(key=lambda x: x["edge"], reverse=True)
        top = mkt_candidates[0]

        mkt_lbl = _market_label(market)
        pick_lbl = _selection_label(market, top["selection"])
        edge = top["edge"]
        icon = _edge_icon(edge)
        arg = top.get("argument_json") or {}

        lines.append(f"<b>{mkt_lbl}</b>")
        lines.append(
            f"  Pick: <b>{pick_lbl}</b> {icon} Edge: <b>{_edge_str(edge)}</b>"
        )
        lines.append(
            f"  Cuota: <b>{top['best_odd']}</b>"
            + (f" ({top['best_bookmaker']})" if top.get("best_bookmaker") else "")
        )
        lines.append(
            f"  Prob. modelo: <b>{_pct(top['model_probability'])}</b>"
            f" · Implícita: {_pct(top['implied_probability'])}"
            f" · Confianza: {_pct(top['confidence_score'])}"
        )

        # Show all selections as a sub-table
        if len(mkt_candidates) > 1:
            sub = []
            for c in mkt_candidates:
                sub.append(
                    f"    {_selection_label(market, c['selection'])}: "
                    f"{_pct(c['model_probability'])} (cuota {c['best_odd']})"
                )
            lines.extend(sub)

        reasons = arg.get("reasons", [])
        if reasons:
            for r in reasons[:2]:
                lines.append(f"  ✓ {r}")

        main_risk = arg.get("main_risk", "")
        if main_risk:
            lines.append(f"  ⚠️ {main_risk}")

        lines.append("")

        if edge >= settings.min_edge:
            best_picks.append(top)

    # Context summary if available
    ctx = fixture.get("context_json") or {}
    ctx_lines = _format_context_summary(ctx)
    if ctx_lines:
        lines.append("📋 <b>CONTEXTO</b>")
        lines.extend(ctx_lines)
        lines.append("")

    # Best pick recommendation
    if best_picks:
        # Sort by edge
        best_picks.sort(key=lambda x: x["edge"], reverse=True)
        bp = best_picks[0]
        mkt = bp["market"]
        sel = bp["selection"]
        lines.append(
            f"💡 <b>PICK RECOMENDADO:</b> "
            f"{_market_label(mkt)} — {_selection_label(mkt, sel)} "
            f"(Edge: <b>{_edge_str(bp['edge'])}</b>)"
        )
        arg = bp.get("argument_json") or {}
        risk = arg.get("main_risk", "")
        if risk:
            lines.append(f"⚡ <b>Riesgo principal:</b> {risk}")
    else:
        lines.append(
            "⚠️ <b>Sin picks con edge suficiente</b> — "
            f"umbral mínimo: {_pct(settings.min_edge)}"
        )

    return "\n".join(lines)


def _format_context_summary(ctx: dict) -> list[str]:
    """Return a short context summary block (standings + form). Empty if no data."""
    lines = []
    home_std = ctx.get("home_standing", {})
    away_std = ctx.get("away_standing", {})
    home_st = ctx.get("home_stats", {})
    away_st = ctx.get("away_stats", {})

    if home_std.get("rank") and away_std.get("rank"):
        lines.append(
            f"  Posición: Local #{home_std['rank']} ({home_std.get('points','?')} pts) "
            f"· Visitante #{away_std['rank']} ({away_std.get('points','?')} pts)"
        )

    home_form = (home_st.get("form") or "")[-5:] or "—"
    away_form = (away_st.get("form") or "")[-5:] or "—"
    if home_form != "—" or away_form != "—":
        lines.append(f"  Forma reciente: Local {home_form} · Visitante {away_form}")

    injuries = ctx.get("injuries", [])
    if injuries:
        lines.append(f"  Bajas reportadas: {len(injuries)}")

    lineups = ctx.get("lineups", {})
    if lineups:
        home_l = lineups.get("home", {})
        away_l = lineups.get("away", {})
        parts = []
        if home_l.get("formation"):
            parts.append(f"Local {home_l['formation']}")
        if away_l.get("formation"):
            parts.append(f"Visitante {away_l['formation']}")
        if parts:
            lines.append(f"  Alineaciones: {' · '.join(parts)}")

    return lines


# ── /estado formatter ─────────────────────────────────────────────────────────


def format_estado(
    schema: SchemaStatus,
    counts: dict | None,
    *,
    rate_state: dict | None = None,
    last_sync: dict | None = None,
) -> str:
    """Build the HTML /estado message.

    Args:
        schema: Result of ``db_health.check_schema()``.
        counts: Dict with keys ``fixtures_today``, ``odds_rows``, ``picks_today``; or None.
        rate_state: Dict from ``client.get_rate_limit_state()``; or None.
        last_sync: Last row from ``api_sync_runs``; or None.
    """
    lines: list[str] = ["<b>Estado del sistema</b>", ""]

    # ── Telegram (always OK if the handler is running) ─────────────────────────
    lines.append("📡 Telegram:            <b>OK</b>")

    # ── Supabase ───────────────────────────────────────────────────────────────
    if schema.connection_ok:
        lines.append("🔌 Supabase:            <b>OK</b>")
    else:
        lines.append("🔌 Supabase:            <b>FAIL ✗</b>")
        if schema.error:
            lines.append(f"   └ {schema.error}")
        lines.append("")
        lines.append("Verifica <code>SUPABASE_URL</code> y <code>SUPABASE_KEY</code> en <code>.env</code>")
        return "\n".join(lines)

    # ── API-Football (from in-memory rate-limit state) ─────────────────────────
    if rate_state and rate_state.get("requests_limit") is not None:
        daily_remaining = rate_state.get("requests_remaining", "?")
        minute_remaining = rate_state.get("minute_remaining", "?")
        daily_limit = rate_state.get("requests_limit", "?")
        minute_limit = rate_state.get("minute_limit", "?")
        lines.append(f"🌐 API-Football:        <b>OK</b>")
        lines.append(
            f"   └ Cuota diaria:  {daily_remaining}/{daily_limit} restantes"
        )
        lines.append(
            f"   └ Cuota/minuto:  {minute_remaining}/{minute_limit} restantes"
        )
    else:
        lines.append("🌐 API-Football:        <b>Sin datos recientes</b>")
        lines.append("   └ Ejecuta un sync para actualizar el estado de la API")

    # ── Schema ─────────────────────────────────────────────────────────────────
    if schema.tables_ok:
        lines.append("🗄️  Schema:              <b>aplicado ✓</b>")
    else:
        lines.append("🗄️  Schema:              <b>NO APLICADO ✗</b>")
        missing = "  ".join(schema.missing_tables)
        lines.append(f"   └ Faltantes: <code>{missing}</code>")
        lines.append("")
        lines.append("<b>Aplica las migraciones en Supabase → SQL Editor:</b>")
        lines.append("  <code>sql/migrations/001_init.sql</code>")
        lines.append("  <code>sql/migrations/002_constraints_and_indexes.sql</code>")
        lines.append("  <code>sql/migrations/003_api_football_v2.sql</code>")
        return "\n".join(lines)

    # ── Data counts ────────────────────────────────────────────────────────────
    lines.append("")
    if counts is None:
        lines.append("❌ Error al obtener conteos — revisa los logs")
    else:
        fx = counts.get("fixtures_today", 0)
        odds = counts.get("odds_rows", 0)
        picks = counts.get("picks_today", 0)
        lines.append(f"📅 Fixtures hoy:        <b>{fx}</b>")
        lines.append(f"💹 Cuotas almacenadas:  <b>{odds}</b>")
        lines.append(f"🎯 Picks publicables:   <b>{picks}</b>")

        if fx == 0:
            lines.append("")
            lines.append("Sin datos para hoy. Ejecuta:")
            lines.append("  <code>python scripts/sync_today.py</code>")

    # ── Last sync run ──────────────────────────────────────────────────────────
    lines.append("")
    if last_sync:
        phase = last_sync.get("phase", "?")
        status = last_sync.get("status", "?")
        started = last_sync.get("started_at", "")[:16].replace("T", " ")
        fixtures_s = last_sync.get("fixtures_synced", 0)
        odds_s = last_sync.get("odds_rows_synced", 0)
        status_icon = "✓" if status == "completed" else ("⚡" if status == "running" else "✗")
        lines.append(
            f"🔄 Último sync ({phase}): <b>{status_icon} {status}</b>"
        )
        lines.append(
            f"   └ {started} · {fixtures_s} fixtures · {odds_s} cuotas"
        )
        if last_sync.get("error_message"):
            lines.append(f"   └ Error: {last_sync['error_message'][:80]}")
    else:
        lines.append("🔄 Último sync:         <b>sin registro</b>")
        lines.append("   └ Ejecuta: <code>python scripts/sync_today.py</code>")

    return "\n".join(lines)
