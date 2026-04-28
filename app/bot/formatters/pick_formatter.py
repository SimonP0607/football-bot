"""Telegram message formatters for picks, estado, and partido analysis.

All formatters output HTML for parse_mode="HTML".
Dynamic content (team names, league names, user strings) is always escaped
with html.escape() to prevent Telegram Bad Request errors.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.core.config import settings

if TYPE_CHECKING:
    from app.data.db_health import SchemaStatus


# ── Shared helpers ────────────────────────────────────────────────────────────

def esc(text: str | int | float | None) -> str:
    """Escape dynamic content for Telegram HTML mode."""
    if text is None:
        return ""
    return html.escape(str(text))


def _kickoff_str(kickoff_at: str) -> str:
    try:
        tz = ZoneInfo(settings.default_timezone)
        dt = datetime.fromisoformat(kickoff_at).astimezone(tz)
        return dt.strftime("%H:%M")
    except Exception:
        return kickoff_at[:16] if kickoff_at else "—"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _edge_str(edge: float) -> str:
    sign = "+" if edge >= 0 else ""
    return f"{sign}{edge * 100:.1f}%"


def _fmt_odd(val: object) -> str:
    try:
        return f"{float(val):.2f}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(val) if val is not None else "?"


def _market_label(market: str) -> str:
    return {"1X2": "Resultado 1X2", "OU25": "Más/Menos 2.5", "BTTS": "Ambos anotan"}.get(
        market, market
    )


def _selection_label(market: str, selection: str) -> str:
    labels: dict[tuple[str, str], str] = {
        ("1X2", "Home"):      "Local",
        ("1X2", "Draw"):      "Empate",
        ("1X2", "Away"):      "Visitante",
        ("OU25", "Over 2.5"): "Más de 2.5",
        ("OU25", "Under 2.5"):"Menos de 2.5",
        ("BTTS", "Yes"):      "Sí anotan ambos",
        ("BTTS", "No"):       "No anotan ambos",
    }
    return labels.get((market, selection), selection)


def _edge_icon(edge: float) -> str:
    if edge >= 0.08:
        return "🔥"
    if edge >= 0.05:
        return "✅"
    if edge >= 0.02:
        return "📊"
    if edge >= 0:
        return "📉"
    return "⛔"


_SEP = "─" * 28


# ── Pick block (shared by /hoy and /top) ─────────────────────────────────────


def _pick_block(pred: dict, fix: dict, team_names: dict, league_names: dict) -> str:
    """Build a compact pick HTML block."""
    home   = esc(team_names.get(fix["home_team_id"], "Local"))
    away   = esc(team_names.get(fix["away_team_id"], "Visitante"))
    league = esc(league_names.get(fix["league_id"], ""))
    ko     = _kickoff_str(fix.get("kickoff_at", ""))

    mkt_lbl  = _market_label(pred["market_key"])
    pick_lbl = _selection_label(pred["market_key"], pred["selection"])
    edge     = pred["edge"]
    icon     = _edge_icon(edge)
    arg      = pred.get("argument_json") or {}

    best_odd = _fmt_odd(arg.get("best_odd") or pred.get("best_odd"))
    best_bk  = arg.get("best_bookmaker") or pred.get("best_bookmaker") or ""
    reasons: list[str] = arg.get("reasons") or []
    main_risk: str      = arg.get("main_risk") or ""

    bk_str = f" <i>({esc(best_bk)})</i>" if best_bk else ""

    lines = [
        f"<b>{home} vs {away}</b>",
        f"⚽ {league} · {ko}",
        "",
        f"{icon} <b>{mkt_lbl} — {pick_lbl}</b>",
        f"💰 <b>{best_odd}</b>{bk_str}  ·  Edge: <b>{_edge_str(edge)}</b>  ·  Conf: <b>{_pct(pred['confidence_score'])}</b>",
        f"📊 Modelo: <b>{_pct(pred['model_probability'])}</b>  ·  Implícita: {_pct(pred['implied_probability'])}",
    ]
    if reasons:
        lines.append(f"📋 {esc(reasons[0])}")
        for r in reasons[1:2]:
            lines.append(f"   {esc(r)}")
    if main_risk:
        lines.append(f"⚠️ {esc(main_risk)}")

    return "\n".join(lines)


# ── /hoy ─────────────────────────────────────────────────────────────────────


def format_picks(
    predictions: list[dict],
    fixtures: list[dict],
    team_names: dict[int, str],
    league_names: dict[int, str],
) -> str:
    """Build the /hoy HTML message: publishable picks ordered by kickoff."""
    if not predictions:
        return (
            "📅 <b>Sin picks publicables para hoy</b>\n\n"
            "Opciones:\n"
            "  · /top — análisis completo y candidatos observados\n"
            "  · /valor — diagnóstico del Value Engine\n"
            "  · /estado — estado del sistema\n\n"
            "Si aún no sincronizaste hoy:\n"
            "<code>python scripts/sync_today.py</code>"
        )

    fixture_by_id = {f["id"]: f for f in fixtures}
    sorted_preds  = sorted(
        predictions,
        key=lambda p: (fixture_by_id.get(p["fixture_id"], {}).get("kickoff_at") or ""),
    )

    blocks: list[str] = []
    for pred in sorted_preds:
        fix = fixture_by_id.get(pred["fixture_id"])
        if fix:
            blocks.append(_pick_block(pred, fix, team_names, league_names))

    if not blocks:
        return "Sin picks publicables para hoy."

    header    = f"📅 <b>Picks del día</b> ({len(blocks)}) — orden cronológico\n"
    return header + f"\n\n{_SEP}\n\n".join(blocks)


# ── /top ─────────────────────────────────────────────────────────────────────


def _observado_mini(
    cand: dict,
    fixture_by_id: dict[int, dict],
    team_names: dict[int, str],
    league_names: dict[int, str],
) -> str | None:
    """Format a compact 'observado' candidate block. Returns None if fixture missing."""
    fix = fixture_by_id.get(cand["fixture_id"])
    if not fix:
        return None
    home   = esc(team_names.get(fix["home_team_id"], "?"))
    away   = esc(team_names.get(fix["away_team_id"], "?"))
    league = esc(league_names.get(fix["league_id"], ""))
    ko     = _kickoff_str(fix.get("kickoff_at", ""))
    mkt    = _market_label(cand["market_key"])
    sel    = _selection_label(cand["market_key"], cand["selection"])
    edge   = cand["edge"]
    arg    = cand.get("argument_json") or {}
    odd    = _fmt_odd(arg.get("best_odd") or cand.get("best_odd"))
    return (
        f"📊 <b>OBSERVADO</b> · {home} vs {away} · {ko}\n"
        f"   {mkt} — {sel}\n"
        f"   Cuota: {odd}  ·  Edge: {_edge_str(edge)}  ·  Modelo: {_pct(cand['model_probability'])}"
    )


def format_top_picks(
    predictions: list[dict],
    fixtures: list[dict],
    team_names: dict[int, str],
    league_names: dict[int, str],
    *,
    ve_summary: dict | None = None,
) -> str:
    """Build the /top HTML message.

    ve_summary keys: mode, evaluated, selected, pickfilter_rejected,
                     low_quality, low_edge, missing, _observados.
    """
    fixture_by_id = {f["id"]: f for f in fixtures}

    # ── No official picks ─────────────────────────────────────────────────────
    if not predictions:
        lines = ["🏆 <b>Top picks</b> — Sin picks oficiales hoy\n"]

        if ve_summary and ve_summary.get("evaluated", 0) > 0:
            mode             = ve_summary.get("mode", "")
            evaluated        = ve_summary.get("evaluated", 0)
            selected         = ve_summary.get("selected", 0)
            pf_rejected      = ve_summary.get("pickfilter_rejected", 0)
            low_quality      = ve_summary.get("low_quality", 0)
            low_edge         = ve_summary.get("low_edge", 0)
            missing          = ve_summary.get("missing", 0)

            lines.append(f"<b>Value Engine</b> (modo: {esc(mode)}) — {evaluated} candidatos analizados")
            if selected > 0:
                lines.append(f"  ✅ {selected} pasaron análisis VE (p_cal · quality · ev_adj)")
                if pf_rejected > 0:
                    lines.append(f"  ❌ {pf_rejected} rechazados por el modelo de cuotas")
                    lines.append(
                        "<i>  El VE usa p_cal calibrada (DuckDB); el modelo usa "
                        "consenso de bookmakers. Cuando divergen, el modelo prevalece.</i>"
                    )
            else:
                lines.append("  Sin candidatos con valor suficiente hoy")
            if low_quality:
                lines.append(f"  ⚠️ {low_quality} quality insuficiente")
            if low_edge:
                lines.append(f"  📉 {low_edge} edge VE insuficiente")
            if missing:
                lines.append(f"  ℹ️ {missing} sin historial / calibrador DuckDB")

            lines.append("")
            lines.append("💬 <i>Hoy el sistema no encontró valor suficiente para publicar picks oficiales.</i>")

            # Observados section
            observados: list[dict] = ve_summary.get("_observados") or []
            if observados:
                lines.append(f"\n{_SEP}\n")
                lines.append("👁 <b>Candidatos en observación</b> <i>(no oficiales)</i>")
                lines.append(
                    "<i>Edge positivo según el modelo de cuotas, "
                    "pero no superaron todos los filtros de confianza.</i>\n"
                )
                for cand in observados[:3]:
                    block = _observado_mini(cand, fixture_by_id, team_names, league_names)
                    if block:
                        lines.append(block)

        lines.append("")
        lines.append("Para más detalle: /valor · /estado")
        lines.append("Para sincronizar: <code>python scripts/sync_today.py</code>")
        return "\n".join(lines)

    # ── Official picks ────────────────────────────────────────────────────────
    ranked = sorted(
        predictions,
        key=lambda p: p.get("confidence_score", 0) * 0.6 + max(0.0, p.get("edge", 0)) * 0.4,
        reverse=True,
    )

    rank_badges = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    blocks: list[str] = []

    for idx, pred in enumerate(ranked):
        fix = fixture_by_id.get(pred["fixture_id"])
        if not fix:
            continue
        badge = rank_badges[idx] if idx < len(rank_badges) else f"#{idx + 1}"
        body  = _pick_block(pred, fix, team_names, league_names)
        blocks.append(f"{badge} <b>Opción {idx + 1}</b>\n{body}")

    if not blocks:
        return "Sin picks publicables para hoy."

    header = f"🏆 <b>Top picks</b> ({len(blocks)}) — mejores oportunidades\n"
    return header + f"\n\n{_SEP}\n\n".join(blocks)


# ── /partido ─────────────────────────────────────────────────────────────────


def format_partido(
    fixture: dict,
    candidates: list[dict],
    team_names: dict[int, str],
    league_names: dict[int, str],
) -> str:
    """Build the full pre-match analysis message for /partido."""
    home   = esc(team_names.get(fixture["home_team_id"], "Local"))
    away   = esc(team_names.get(fixture["away_team_id"], "Visitante"))
    league = esc(league_names.get(fixture["league_id"], ""))
    ko     = _kickoff_str(fixture.get("kickoff_at", ""))
    pid    = fixture.get("provider_fixture_id", "?")

    lines = [
        f"⚽ <b>{home} vs {away}</b>",
        f"{league} · {ko}",
        f"<code>ID: {pid}</code>",
        "",
    ]

    if not candidates:
        lines += [
            "Sin cuotas disponibles para analizar este partido.",
            "",
            "Ejecuta el sync diario y vuelve a consultar:",
            "<code>python scripts/sync_today.py</code>",
        ]
        return "\n".join(lines)

    markets_order = ["1X2", "OU25", "BTTS"]
    by_market: dict[str, list[dict]] = {}
    for c in candidates:
        by_market.setdefault(c["market_key"], []).append(c)

    best_picks: list[dict] = []
    lines.append(f"{_SEP}")
    lines.append("")

    for market in markets_order:
        mkt_candidates = by_market.get(market, [])
        if not mkt_candidates:
            continue

        mkt_candidates.sort(key=lambda x: x["edge"], reverse=True)
        top  = mkt_candidates[0]
        edge = top["edge"]
        arg  = top.get("argument_json") or {}

        # Status
        if edge >= settings.min_edge and top.get("confidence_score", 0) >= settings.min_confidence:
            badge  = "✅ RECOMENDADO"
            best_picks.append(top)
        elif edge >= 0:
            badge = "📊 OBSERVADO"
        else:
            badge = "⛔ SIN VALOR"

        mkt_lbl  = _market_label(market)
        pick_lbl = _selection_label(market, top["selection"])
        odd_str  = _fmt_odd(top.get("best_odd"))
        bk       = top.get("best_bookmaker") or arg.get("best_bookmaker") or ""
        bk_str   = f" <i>({esc(bk)})</i>" if bk else ""

        lines.append(f"<b>{mkt_lbl}</b>  {badge}")
        lines.append(f"   Pick: <b>{pick_lbl}</b>  ·  Cuota: <b>{odd_str}</b>{bk_str}")
        lines.append(
            f"   Edge: <b>{_edge_str(edge)}</b>  ·  "
            f"Modelo: <b>{_pct(top['model_probability'])}</b>  ·  "
            f"Implícita: {_pct(top['implied_probability'])}"
        )

        reasons  = arg.get("reasons") or []
        main_risk = arg.get("main_risk") or ""
        if reasons:
            lines.append(f"   📋 {esc(reasons[0])}")
        if main_risk:
            lines.append(f"   ⚠️ {esc(main_risk)}")
        lines.append("")

    # Context
    ctx = fixture.get("context_json") or {}
    ctx_lines = _format_context_summary(ctx)
    if ctx_lines:
        lines.append(f"{_SEP}")
        lines.append("📋 <b>Contexto del partido</b>")
        lines.extend(ctx_lines)
        lines.append("")

    # Summary verdict
    lines.append(f"{_SEP}")
    if best_picks:
        best_picks.sort(key=lambda x: x["edge"], reverse=True)
        bp      = best_picks[0]
        mkt     = bp["market_key"]
        sel     = bp["selection"]
        bp_edge = _edge_str(bp["edge"])
        lines.append(
            f"💡 <b>Pick recomendado:</b> {_market_label(mkt)} — "
            f"{_selection_label(mkt, sel)}  (Edge: <b>{bp_edge}</b>)"
        )
        bp_risk = (bp.get("argument_json") or {}).get("main_risk", "")
        if bp_risk:
            lines.append(f"⚠️ {esc(bp_risk)}")
    else:
        lines.append("⚠️ <b>Sin valor suficiente para pick oficial</b>")
        observados = [t for t in by_market.values() if t and t[0]["edge"] >= 0]
        if observados:
            lines.append(
                "   Los mercados OBSERVADO tienen edge positivo pero no alcanzan "
                f"el umbral mínimo ({_pct(settings.min_edge)})."
            )
        else:
            lines.append(
                "   Todos los mercados tienen edge negativo — "
                "el modelo de cuotas no ve valor en este partido hoy."
            )

    return "\n".join(lines)


def _format_context_summary(ctx: dict) -> list[str]:
    """Return a short context summary (standings + form). Empty if no data."""
    lines: list[str] = []
    home_std = ctx.get("home_standing") or {}
    away_std = ctx.get("away_standing") or {}
    home_st  = ctx.get("home_stats") or {}
    away_st  = ctx.get("away_stats") or {}

    if home_std.get("rank") and away_std.get("rank"):
        lines.append(
            f"  Posición: Local #{home_std['rank']} ({home_std.get('points','?')} pts)"
            f"  ·  Visitante #{away_std['rank']} ({away_std.get('points','?')} pts)"
        )

    home_form = (home_st.get("form") or "")[-5:] or "—"
    away_form = (away_st.get("form") or "")[-5:] or "—"
    if home_form != "—" or away_form != "—":
        lines.append(f"  Forma: Local {home_form}  ·  Visitante {away_form}")

    injuries = ctx.get("injuries") or []
    if injuries:
        lines.append(f"  Bajas reportadas: {len(injuries)}")

    lineups = ctx.get("lineups") or {}
    parts: list[str] = []
    if lineups.get("home", {}).get("formation"):
        parts.append(f"Local {lineups['home']['formation']}")
    if lineups.get("away", {}).get("formation"):
        parts.append(f"Visitante {lineups['away']['formation']}")
    if parts:
        lines.append(f"  Alineaciones: {' · '.join(parts)}")

    return lines


# ── /estado ───────────────────────────────────────────────────────────────────


def format_estado(
    schema: "SchemaStatus",
    counts: dict | None,
    *,
    rate_state: dict | None = None,
    last_sync: dict | None = None,
) -> str:
    """Build the /estado HTML panel."""

    _sj: dict = (last_sync.get("summary_json") or {}) if last_sync else {}

    lines: list[str] = ["<b>📊 Panel del sistema</b>", ""]

    # ── Conexiones ────────────────────────────────────────────────────────────
    sb_ok = "✓" if schema.connection_ok else "✗"
    sc_ok = "✓" if schema.tables_ok else "✗"
    lines.append(f"📡 Telegram ✓  ·  Supabase {sb_ok}  ·  Schema {sc_ok}")

    if not schema.connection_ok:
        lines.append(f"   └ {esc(schema.error)}")
        lines.append("")
        lines.append("Verifica <code>SUPABASE_URL</code> y <code>SUPABASE_KEY</code> en .env")
        return "\n".join(lines)

    # API-Football
    if rate_state and rate_state.get("requests_limit") is not None:
        d_rem = rate_state.get("requests_remaining", "?")
        d_lim = rate_state.get("requests_limit", "?")
        m_rem = rate_state.get("minute_remaining", "?")
        m_lim = rate_state.get("minute_limit", "?")
        lines.append(
            f"🌐 API-Football: <b>{d_rem}/{d_lim}</b> diarias  ·  <b>{m_rem}/{m_lim}</b>/min"
        )
    else:
        lines.append("🌐 API-Football: sin datos (ejecuta sync para actualizar)")

    if not schema.tables_ok:
        lines.append("🗄️ Schema: <b>NO APLICADO</b>")
        missing_str = ", ".join(schema.missing_tables[:4])
        lines.append(f"   └ Tablas faltantes: <code>{missing_str}</code>")
        lines.append("")
        lines.append("Aplica en Supabase SQL Editor:")
        lines.append("  <code>sql/migrations/010_production_schema.sql</code>")
        return "\n".join(lines)

    # ── Datos de hoy ──────────────────────────────────────────────────────────
    lines.append("")
    if counts is None:
        lines.append("❌ Error obteniendo conteos — revisa los logs")
    else:
        fx   = counts.get("fixtures_today", 0)
        odds = counts.get("odds_rows", 0)
        picks = counts.get("picks_today", 0)
        lines.append(f"📅 Fixtures hoy:    <b>{fx}</b>")
        lines.append(f"💹 Cuotas:          <b>{odds}</b>")

        # Smart picks line — detect stale vs current
        sync_pub: int | None = None
        try:
            _sp = _sj.get("publishable_saved")
            if _sp is not None:
                sync_pub = int(_sp)
        except (TypeError, ValueError):
            pass

        if sync_pub is not None and sync_pub != picks:
            lines.append(f"🎯 Picks oficiales: <b>{picks}</b> en BD  ·  <b>{sync_pub}</b> del último sync")
            lines.append("   └ La BD puede tener picks de un sync anterior. El próximo sync los sobreescribe.")
        else:
            lines.append(f"🎯 Picks oficiales: <b>{picks}</b>")

        if fx == 0:
            lines.append("   └ Sin datos hoy. Ejecuta <code>python scripts/sync_today.py</code>")

    # ── Value Engine ──────────────────────────────────────────────────────────
    _ve_mode = _sj.get("value_engine_mode")
    if _ve_mode and _ve_mode != "off":
        ve_eval = _sj.get("value_engine_evaluated", "—")
        ve_sel  = _sj.get("value_engine_selected", "—")
        ve_pf   = _sj.get("value_engine_pickfilter_rejected") or 0
        ve_pub  = _sj.get("publishable_saved", "—")
        lines.append("")
        lines.append(f"🤖 <b>Value Engine</b> ({esc(_ve_mode)})")
        lines.append(
            f"   Evaluados: <b>{ve_eval}</b>  ·  "
            f"Pasaron VE: <b>{ve_sel}</b>  ·  "
            f"Rechazados modelo: <b>{ve_pf}</b>  ·  "
            f"Oficiales: <b>{ve_pub}</b>"
        )

    # ── Último sync ───────────────────────────────────────────────────────────
    lines.append("")
    if last_sync:
        phase    = last_sync.get("phase", "?")
        status   = last_sync.get("status", "?")
        started  = (last_sync.get("started_at") or "")[:16].replace("T", " ")
        fx_s     = last_sync.get("fixtures_synced", 0)
        odds_s   = last_sync.get("odds_rows_synced", 0)
        ok_str   = "OK" if status == "completed" else ("..." if status == "running" else "ERR")
        lines.append(f"🔄 Último sync ({phase}): <b>{started}</b> — {ok_str}")
        lines.append(f"   {fx_s} fixtures · {odds_s} cuotas")
        if last_sync.get("error_message"):
            lines.append(f"   ⚠️ {esc(last_sync['error_message'][:80])}")
    else:
        lines.append("🔄 Último sync: <b>sin registro</b>")
        lines.append("   Ejecuta: <code>python scripts/sync_today.py</code>")

    return "\n".join(lines)
