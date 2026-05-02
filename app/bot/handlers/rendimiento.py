"""Handler for /rendimiento — performance metrics panel."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.middleware.db_guard import ensure_db_ready
from app.bot.utils import esc, send_html

logger = logging.getLogger(__name__)

_MIN_SAMPLE = 20  # warn if fewer settled picks


@require_auth
async def rendimiento_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rendimiento — Performance metrics: hit rate, ROI, yield by period and market."""
    logger.info("/rendimiento solicitado por user_id=%s", update.effective_user.id)

    if not await ensure_db_ready(update, context):
        return

    try:
        text = _build_text()
    except Exception as exc:
        logger.error("/rendimiento: error — %s", exc, exc_info=True)
        text = "Error al calcular métricas. Usa /estado para diagnosticar."

    await send_html(update.message, text)


def _fmt_stats(s: dict) -> str:
    """One-line summary of a stats dict."""
    if s["settled"] == 0:
        return "—"
    return (
        f"{s['wins']}G/{s['losses']}P  "
        f"({s['hit_rate_pct']:.1f}%)  "
        f"{s['profit_units']:+.2f}u  "
        f"ROI {s['roi_pct']:+.2f}%"
    )


def _small_sample_warning(settled: int) -> str | None:
    if settled == 0:
        return None
    if settled < _MIN_SAMPLE:
        return (
            f"⚠️ <i>Muestra pequeña ({settled} picks resueltos, mínimo recomendado {_MIN_SAMPLE}).\n"
            "Las métricas no son estadísticamente significativas todavía.</i>"
        )
    return None


def _build_text() -> str:
    from app.data.repositories import settlement_repo

    # Fetch data for three horizons
    rows_7   = settlement_repo.get_settled(days=7)
    rows_30  = settlement_repo.get_settled(days=30)
    rows_all = settlement_repo.get_settled()

    s7   = settlement_repo.compute_performance_stats(rows_7)
    s30  = settlement_repo.compute_performance_stats(rows_30)
    sall = settlement_repo.compute_performance_stats(rows_all)

    lines: list[str] = ["<b>Rendimiento del sistema</b>", ""]

    if sall["total"] == 0:
        lines.append("Sin picks resueltos todavía.")
        lines.append("")
        lines.append("Para liquidar picks pasados:")
        lines.append("<code>python scripts/settle_results.py --dry-run</code>")
        lines.append("<code>python scripts/settle_results.py</code>")
        return "\n".join(lines)

    # ── Resumen temporal ───────────────────────────────────────────────────────
    lines.append("<b>Resumen por período</b>")
    for label, s in [("Últimos 7 días", s7), ("Últimos 30 días", s30), ("Histórico", sall)]:
        if s["settled"]:
            lines.append(
                f"  {label}:  "
                f"<b>{s['wins']}G / {s['losses']}P</b>  ·  "
                f"Hit: <b>{s['hit_rate_pct']:.1f}%</b>  ·  "
                f"ROI: <b>{s['roi_pct']:+.2f}%</b>  ·  "
                f"Profit: <b>{s['profit_units']:+.2f}u</b>"
            )
        else:
            lines.append(f"  {label}:  Sin datos")

    warn = _small_sample_warning(sall["settled"])
    if warn:
        lines.append("")
        lines.append(warn)

    # ── Por mercado ────────────────────────────────────────────────────────────
    by_mkt = settlement_repo.get_stats_by_market(rows_all)
    if len(by_mkt) > 1:
        lines.append("")
        lines.append("<b>Por mercado (histórico)</b>")
        for mk, s in sorted(by_mkt.items()):
            if s["settled"]:
                lines.append(
                    f"  {esc(mk)}:  "
                    f"<b>{s['wins']}G / {s['losses']}P</b>  ·  "
                    f"Hit: <b>{s['hit_rate_pct']:.1f}%</b>  ·  "
                    f"ROI: <b>{s['roi_pct']:+.2f}%</b>"
                )

    # ── Por liga (top 5) ───────────────────────────────────────────────────────
    if rows_all:
        try:
            from app.data.repositories.supabase_client import get_supabase
            client = get_supabase()
            fixture_ids = list({r["fixture_id"] for r in rows_all})

            fix_rows = (
                client.table("fixtures")
                .select("id, league_id")
                .in_("id", fixture_ids)
                .execute()
            ).data or []
            cs_ids = list({f["league_id"] for f in fix_rows if f.get("league_id")})

            cs_rows = (
                client.table("competition_seasons")
                .select("id, competition_id")
                .in_("id", cs_ids)
                .execute()
            ).data or []
            comp_ids = list({c["competition_id"] for c in cs_rows if c.get("competition_id")})

            comp_rows = (
                client.table("competitions").select("id, name").in_("id", comp_ids).execute()
            ).data or []
            comp_map = {c["id"]: c["name"] for c in comp_rows}
            cs_map   = {c["id"]: comp_map.get(c["competition_id"], "?") for c in cs_rows}
            fix_league = {f["id"]: cs_map.get(f["league_id"], "?") for f in fix_rows}

            by_league: dict[str, list] = {}
            for r in rows_all:
                league = fix_league.get(r["fixture_id"], "?")
                by_league.setdefault(league, []).append(r)

            leagues_sorted = sorted(
                by_league.items(),
                key=lambda kv: settlement_repo.compute_performance_stats(kv[1])["settled"],
                reverse=True,
            )

            if leagues_sorted:
                lines.append("")
                lines.append("<b>Por liga — top 5 (histórico)</b>")
                for league, league_rows in leagues_sorted[:5]:
                    s = settlement_repo.compute_performance_stats(league_rows)
                    if s["settled"]:
                        lines.append(
                            f"  {esc(league[:30])}: "
                            f"<b>{s['wins']}G / {s['losses']}P</b>  ·  "
                            f"Hit <b>{s['hit_rate_pct']:.1f}%</b>  ·  "
                            f"ROI <b>{s['roi_pct']:+.2f}%</b>"
                        )
        except Exception as exc:
            logger.debug("/rendimiento: no se pudo obtener breakdown por liga: %s", exc)

    # ── Pending counts ─────────────────────────────────────────────────────────
    try:
        pending = settlement_repo.get_pending()
        if pending:
            lines.append("")
            lines.append(f"⏳ <b>{len(pending)} picks pendientes</b> de liquidación.")
            lines.append(
                "Para liquidar: <code>python scripts/settle_results.py</code>"
            )
    except Exception:
        pass

    lines.append("")
    lines.append("Ver picks: /resultados  ·  Ver VE: /valor")
    return "\n".join(lines)
