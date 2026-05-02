"""Handler for /scheduler — admin scheduler management and manual job execution."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.utils import send_html

logger = logging.getLogger(__name__)

_VALID_JOBS = ("sync", "prematch", "live", "settlement", "report")

_HELP = (
    "<b>Scheduler Admin</b>\n\n"
    "/scheduler — estado del scheduler\n"
    "/scheduler run sync — forzar sync diario\n"
    "/scheduler run prematch — forzar prematch refresh\n"
    "/scheduler run live — forzar live monitor tick\n"
    "/scheduler run settlement — forzar liquidación\n"
    "/scheduler run report — forzar reporte diario\n"
)


@require_auth
async def scheduler_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/scheduler [run <job>] — Admin scheduler control."""
    logger.info("/scheduler llamado por user_id=%s args=%s", update.effective_user.id, context.args)

    args = context.args or []

    if len(args) >= 2 and args[0].lower() == "run":
        job_name = args[1].lower()
        if job_name not in _VALID_JOBS:
            await send_html(
                update.message,
                f"Job desconocido: <code>{job_name}</code>\n"
                f"Jobs válidos: {', '.join(_VALID_JOBS)}",
            )
            return
        await _run_job(update, context, job_name)
        return

    if args and args[0].lower() == "help":
        await send_html(update.message, _HELP)
        return

    from app.core.config import settings
    await _send_status(update, settings)


async def _send_status(update: Update, settings) -> None:
    sched_icon = "✅" if settings.scheduler_enabled else "⭕"

    lines = [
        "<b>Scheduler — Estado</b>",
        "",
        f"{sched_icon} Habilitado: <b>{'sí' if settings.scheduler_enabled else 'no'}</b>",
        f"Zona horaria: {settings.scheduler_timezone}",
        f"Sync diario: {settings.scheduler_daily_sync_time} ({_yn(settings.scheduler_daily_sync_enabled)})",
        f"Settlement: {settings.scheduler_settlement_time} ({_yn(settings.scheduler_settlement_enabled)})",
        f"Report: {settings.scheduler_report_time} ({_yn(settings.scheduler_daily_report_enabled)})",
        f"Live: cada {settings.scheduler_live_interval_seconds}s ({_yn(settings.scheduler_live_monitor_enabled)})",
        f"Prematch ventanas: {settings.scheduler_prematch_windows}",
        f"Budget diario API: {settings.scheduler_api_budget_daily}",
        "",
    ]

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        from app.services.scheduler_service import get_scheduler_status, get_scheduler_state
        conn = get_local_db()
        init_schema(conn)
        st = get_scheduler_status(conn)
        if "error" not in st:
            lines.append(f"Runs: {st['total_runs']} · Errores: {st['errors']}")
            if st.get("last_run_job"):
                lines.append(
                    f"Último: {st['last_run_job']} → {st['last_run_status']} @ {st['last_run_at']}"
                )
        for key in ("last_daily_sync", "last_prematch", "last_live_monitor", "last_settlement", "last_daily_report"):
            val = get_scheduler_state(conn, key) or "nunca"
            label = key.replace("last_", "")
            lines.append(f"  {label}: {val}")
    except Exception as exc:
        lines.append(f"DuckDB: error — {exc}")

    await send_html(update.message, "\n".join(lines))


async def _run_job(update: Update, context: ContextTypes.DEFAULT_TYPE, job_name: str) -> None:
    await send_html(update.message, f"⏳ Ejecutando job: <b>{job_name}</b>…")

    try:
        from app.services.scheduled_jobs import (
            daily_sync_job,
            prematch_refresh_job,
            live_monitor_job,
            settlement_job,
            daily_report_job,
        )

        job_map = {
            "sync": daily_sync_job,
            "prematch": prematch_refresh_job,
            "live": live_monitor_job,
            "settlement": settlement_job,
            "report": daily_report_job,
        }

        class _MockJob:
            data = {"application": context.application}

        class _MockCtx:
            job = _MockJob()
            application = context.application

        await job_map[job_name](_MockCtx())
        await send_html(update.message, f"✅ Job <b>{job_name}</b> completado.")

    except Exception as exc:
        logger.error("Manual run of %s failed: %s", job_name, exc, exc_info=True)
        await send_html(
            update.message,
            f"❌ Job <b>{job_name}</b> error:\n<code>{exc}</code>",
        )


def _yn(val: bool) -> str:
    return "✅" if val else "⭕"
