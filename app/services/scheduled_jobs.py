"""Phase 10: Async job functions executed by PTB JobQueue."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram.ext import ContextTypes

from app.core.config import settings

logger = logging.getLogger(__name__)


def _conn():
    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)
    return conn


def _budget_ok(job_key: str) -> bool:
    try:
        from app.services.api_budget_service import get_budget_status
        status = get_budget_status()
        remaining = status.get("remaining_today", settings.scheduler_api_budget_daily)
        if remaining <= 0:
            logger.warning("[scheduler] %s: budget agotado, saltando", job_key)
            return False
        return True
    except Exception:
        return True


def _application(context: ContextTypes.DEFAULT_TYPE):
    return (context.job.data or {}).get("application") if context.job else None


# ── Jobs ──────────────────────────────────────────────────────────────────────

async def daily_sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily fixture + odds sync for configured leagues."""
    job_key = "daily_sync"
    start = datetime.now(timezone.utc)
    conn = _conn()
    app = _application(context)

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state, send_admin_notification

    api_calls = 0
    try:
        if not _budget_ok(job_key):
            log_scheduler_run(conn, job_key, "skipped_budget", start, datetime.now(timezone.utc))
            return

        logger.info("[scheduler] %s: iniciando sync diario", job_key)

        from app.data.api_football.client import get_fixtures_today
        fixtures = get_fixtures_today() or []
        api_calls += 1

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_daily_sync", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            api_calls_used=api_calls,
            metadata={"fixtures": len(fixtures)},
        )
        logger.info("[scheduler] %s: %d fixtures en %.1fs", job_key, len(fixtures), (end - start).total_seconds())

        if settings.scheduler_notify_alerts and app:
            await send_admin_notification(
                app,
                f"✅ <b>Sync diario</b> completado — {len(fixtures)} fixtures",
            )

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc, exc_info=True)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))
        if app:
            try:
                await send_admin_notification(app, f"❌ <b>Sync diario</b> error: {exc}")
            except Exception:
                pass


async def prematch_refresh_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prematch intelligence refresh for upcoming fixtures."""
    job_key = "prematch_refresh"
    start = datetime.now(timezone.utc)
    conn = _conn()
    window = (context.job.data or {}).get("window_minutes", 180) if context.job else 180

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state

    api_calls = 0
    try:
        if not _budget_ok(job_key):
            log_scheduler_run(conn, job_key, "skipped_budget", start, datetime.now(timezone.utc))
            return

        logger.info("[scheduler] %s: ventana=%dm", job_key, window)

        try:
            from app.services.prematch_intelligence_service import get_prematch_fixtures
            fixtures = get_prematch_fixtures(window_minutes=window) or []
            api_calls += len(fixtures)
            logger.info("[scheduler] %s: %d fixtures prematch procesados", job_key, len(fixtures))
        except (ImportError, AttributeError):
            fixtures = []
            logger.debug("[scheduler] %s: prematch_intelligence_service sin get_prematch_fixtures", job_key)

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_prematch", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            api_calls_used=api_calls,
            metadata={"window_minutes": window, "fixtures": len(fixtures)},
        )

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc, exc_info=True)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))


async def live_monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Live fixture monitoring tick."""
    job_key = "live_monitor"
    start = datetime.now(timezone.utc)
    conn = _conn()

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state

    api_calls = 0
    try:
        if not settings.live_monitor_enabled:
            return

        if not _budget_ok(job_key):
            log_scheduler_run(conn, job_key, "skipped_budget", start, datetime.now(timezone.utc))
            return

        try:
            from app.services.live_monitor_service import run_live_monitor_tick
            result = await run_live_monitor_tick(conn)
            api_calls = result.get("api_calls", 0) if isinstance(result, dict) else 0
        except (ImportError, AttributeError):
            logger.debug("[scheduler] %s: run_live_monitor_tick no disponible", job_key)

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_live_monitor", end.isoformat())
        log_scheduler_run(conn, job_key, "completed", start, end, api_calls_used=api_calls)

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))


async def settlement_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily auto-settlement of finished picks."""
    job_key = "settlement"
    start = datetime.now(timezone.utc)
    conn = _conn()
    app = _application(context)

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state, send_admin_notification

    try:
        logger.info("[scheduler] %s: iniciando liquidación", job_key)

        settled = 0
        win = loss = void_ = 0
        try:
            from app.data.repositories.settlement_repo import settle_pending_picks
            result = settle_pending_picks(dry_run=False)
            if isinstance(result, dict):
                settled = result.get("settled", 0)
                win = result.get("win", 0)
                loss = result.get("loss", 0)
                void_ = result.get("void", 0)
        except (ImportError, AttributeError, TypeError):
            logger.debug("[scheduler] %s: settle_pending_picks no disponible", job_key)

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_settlement", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            metadata={"settled": settled, "win": win, "loss": loss, "void": void_},
        )
        logger.info("[scheduler] %s: %d picks liquidados", job_key, settled)

        if settings.scheduler_notify_alerts and app and settled > 0:
            from app.services.alert_service import format_settlement_summary
            await send_admin_notification(
                app,
                format_settlement_summary(settled, win, loss, void_),
            )

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc, exc_info=True)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send daily performance report to admin users."""
    job_key = "daily_report"
    start = datetime.now(timezone.utc)
    conn = _conn()
    app = _application(context)

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state, send_admin_notification

    messages_sent = 0
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logger.info("[scheduler] %s: generando reporte de %s", job_key, today)

        picks_today = picks_published = parlays_today = 0
        settled_today = win = loss = void_ = 0
        api_calls_used = 0
        budget_remaining = settings.scheduler_api_budget_daily

        try:
            from app.services.prediction_service import prediction_service
            estado = prediction_service.get_estado()
            picks_today = estado.get("candidates_today", 0)
            picks_published = estado.get("published_today", 0)
        except Exception:
            pass

        try:
            from app.services.parlay_engine_service import get_parlay_engine_status
            ps = get_parlay_engine_status(conn)
            parlays_today = ps.get("today_total", 0)
        except Exception:
            pass

        try:
            from app.data.repositories.settlement_repo import get_settlement_summary
            ss = get_settlement_summary(days=1)
            settled_today = ss.get("total", 0)
            win = ss.get("win", 0)
            loss = ss.get("loss", 0)
            void_ = ss.get("void", 0)
        except Exception:
            pass

        try:
            from app.services.api_budget_service import get_budget_status
            bstatus = get_budget_status()
            api_calls_used = bstatus.get("calls_today", 0)
            budget_remaining = bstatus.get("remaining_today", settings.scheduler_api_budget_daily)
        except Exception:
            pass

        from app.services.alert_service import format_daily_report
        report_text = format_daily_report(
            picks_today=picks_today,
            picks_published=picks_published,
            parlays_today=parlays_today,
            settled_today=settled_today,
            win=win,
            loss=loss,
            void=void_,
            api_calls_used=api_calls_used,
            budget_remaining=budget_remaining,
        )

        if settings.scheduler_notify_alerts and app:
            messages_sent = await send_admin_notification(app, report_text)

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_daily_report", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            messages_sent=messages_sent,
            metadata={"date": today, "picks_today": picks_today},
        )
        logger.info("[scheduler] %s: reporte enviado a %d usuario(s)", job_key, messages_sent)

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc, exc_info=True)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))
