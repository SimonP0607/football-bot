"""Handler for /alertas — proactive alert preferences and scheduler status."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.utils import send_html

logger = logging.getLogger(__name__)

_HELP = (
    "<b>Alertas proactivas</b>\n\n"
    "Cuando el scheduler está activo, recibirás notificaciones sobre:\n"
    "· Sync diario completado\n"
    "· Picks publicados del día\n"
    "· Parlays recomendados\n"
    "· Goles y resultados en vivo\n"
    "· Reporte diario de rendimiento\n"
    "· Liquidación de picks\n\n"
    "<b>Subcomandos</b>\n"
    "/alertas — mostrar este menú\n"
    "/alertas estado — estado actual del scheduler\n\n"
    "<i>Activa el scheduler con SCHEDULER_ENABLED=true en .env</i>"
)


@require_auth
async def alertas_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/alertas [estado] — Proactive alert info and scheduler status."""
    logger.info("/alertas llamado por user_id=%s args=%s", update.effective_user.id, context.args)

    args = context.args or []
    sub = args[0].lower() if args else ""

    if sub == "estado":
        await _send_status(update)
    else:
        await send_html(update.message, _HELP)


async def _send_status(update: Update) -> None:
    from app.core.config import settings as s

    sched_icon = "✅" if s.scheduler_enabled else "⭕"
    notify_icon = "✅" if s.scheduler_notify_alerts else "⭕"

    lines = [
        "<b>Estado del Scheduler</b>",
        "",
        f"{sched_icon} Scheduler: <b>{'activado' if s.scheduler_enabled else 'desactivado'}</b>",
        f"{notify_icon} Notificaciones: <b>{'activadas' if s.scheduler_notify_alerts else 'desactivadas'}</b>",
        f"Zona horaria: {s.scheduler_timezone}",
        f"Budget API diario: {s.scheduler_api_budget_daily}",
        "",
    ]

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        from app.services.scheduler_service import get_scheduler_status, get_scheduler_state
        conn = get_local_db()
        init_schema(conn)
        st = get_scheduler_status(conn)
        if "error" not in st:
            lines.append(f"Runs totales: {st['total_runs']} · Errores: {st['errors']}")
            if st.get("last_run_job"):
                lines.append(
                    f"Último job: {st['last_run_job']} ({st['last_run_status']}) @ {st['last_run_at']}"
                )
            lines.append(
                f"Notificaciones: {st['total_notifications']} total · {st['pending_notifications']} pendientes"
            )
            lines.append("")
            for key in ("last_daily_sync", "last_prematch", "last_live_monitor", "last_settlement", "last_daily_report"):
                val = get_scheduler_state(conn, key) or "nunca"
                label = key.replace("last_", "")
                lines.append(f"  {label}: {val}")
    except Exception as exc:
        lines.append(f"DuckDB: no disponible — {exc}")

    await send_html(update.message, "\n".join(lines))
