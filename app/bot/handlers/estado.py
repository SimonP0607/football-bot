"""Handler for /estado — full system health and data status report.

Always runs, even when the DB is not initialized. This is intentional:
/estado is the diagnostic command the operator uses to understand what is
wrong and what to do next. It must never crash.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.formatters.pick_formatter import format_estado
from app.bot.utils import send_html
from app.data.db_health import check_schema

logger = logging.getLogger(__name__)


@require_auth
async def estado_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/estado — Show full system health: Telegram, Supabase, API-Football, and data counts."""
    logger.info("/estado solicitado por user_id=%s", update.effective_user.id)

    # Always re-probe schema so the report reflects the actual current state.
    schema = check_schema()
    context.bot_data["schema_status"] = schema

    counts: dict | None = None
    last_sync: dict | None = None
    rate_state: dict | None = None

    if schema.db_ready:
        try:
            from app.services.prediction_service import prediction_service
            counts = prediction_service.get_estado()
        except Exception as exc:
            logger.error("/estado: error al obtener conteos — %s", exc)

        try:
            from app.data.repositories.sync_runs_repo import get_last_sync_run
            last_sync = get_last_sync_run()
        except Exception as exc:
            logger.debug("/estado: no se pudo obtener last_sync_run — %s", exc)

    # Always try to read in-memory rate-limit state (updated on any API call)
    try:
        from app.data.api_football.client import get_rate_limit_state
        rate_state = get_rate_limit_state()
    except Exception as exc:
        logger.debug("/estado: no se pudo leer rate_state — %s", exc)

    text = format_estado(schema, counts, rate_state=rate_state, last_sync=last_sync)

    # Append Phase 8/9 extended status
    try:
        text += _build_extended_estado()
    except Exception as exc:
        logger.debug("/estado: extended section failed — %s", exc)

    await send_html(update.message, text)


def _build_extended_estado() -> str:
    """Build AI Router + Parlay Engine + Live Monitor status section."""
    from app.core.config import settings as s
    lines: list[str] = ["", "─" * 28, "<b>Módulos adicionales</b>", ""]

    # Live Monitor
    live_icon = "✅" if s.live_monitor_enabled else "⭕"
    lines.append(f"{live_icon} Live Monitor: <b>{'activo' if s.live_monitor_enabled else 'inactivo'}</b>")
    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        from app.data.local.live_monitor_repo import live_monitor_counts
        conn = get_local_db()
        init_schema(conn)
        counts = live_monitor_counts(conn)
        snap = counts.get("live_fixture_snapshots", 0)
        track = counts.get("live_pick_tracking", 0)
        lines.append(f"  Snapshots: {snap} · Picks tracked: {track}")
    except Exception:
        lines.append("  Sin datos DuckDB")

    # Parlay Engine
    parlay_icon = "✅" if s.parlay_engine_enabled else "⭕"
    lines.append(f"{parlay_icon} Parlay Engine: <b>{'activo' if s.parlay_engine_enabled else 'inactivo'}</b>")
    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        from app.services.parlay_engine_service import get_parlay_engine_status
        conn = get_local_db()
        init_schema(conn)
        ps = get_parlay_engine_status(conn)
        if "error" not in ps:
            lines.append(f"  Hoy: {ps.get('today_total', 0)} parlays · {ps.get('today_recommended', 0)} recomendados")
            perf = ps.get("performance", {})
            if perf.get("settled", 0) > 0:
                lines.append(f"  ROI: {perf.get('roi_pct', 0):+.2f}%  Profit: {perf.get('profit_units', 0):+.4f}u")
    except Exception:
        lines.append("  Sin datos DuckDB")

    # AI Router
    ai_icon = "✅" if s.ai_router_enabled else "⭕"
    lines.append(f"{ai_icon} AI Router: <b>{'activo' if s.ai_router_enabled else 'inactivo'}</b>")
    if s.ai_router_enabled:
        lines.append(f"  Provider: {s.ai_router_provider}")
        try:
            from app.data.local.duckdb_client import get_local_db, init_schema
            from app.services.ai_router_service import get_router_status
            conn = get_local_db()
            init_schema(conn)
            rs = get_router_status(conn)
            if "error" not in rs:
                last = rs.get("last_intent") or "—"
                total = rs.get("total_queries", 0)
                lines.append(f"  Consultas: {total} · Última intención: {last}")
        except Exception:
            pass

    # Scheduler
    sched_icon = "✅" if s.scheduler_enabled else "⭕"
    lines.append(f"{sched_icon} Scheduler: <b>{'activo' if s.scheduler_enabled else 'inactivo'}</b>")
    if s.scheduler_enabled:
        try:
            from app.data.local.duckdb_client import get_local_db, init_schema
            from app.services.scheduler_service import get_scheduler_status
            conn = get_local_db()
            init_schema(conn)
            ss = get_scheduler_status(conn)
            if "error" not in ss:
                lines.append(f"  Runs: {ss['total_runs']} · Errores: {ss['errors']}")
                if ss.get("last_run_job"):
                    lines.append(f"  Último: {ss['last_run_job']} ({ss['last_run_status']})")
                lines.append(f"  Notificaciones: {ss['total_notifications']} · pendientes: {ss['pending_notifications']}")
        except Exception:
            lines.append("  Sin datos DuckDB")

    return "\n".join(lines)
