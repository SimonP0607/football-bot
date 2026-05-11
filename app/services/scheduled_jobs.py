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


async def player_stats_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nightly sync of player stats for recent finished fixtures."""
    job_key = "player_stats"
    start = datetime.now(timezone.utc)
    conn = _conn()
    app = _application(context)

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state, send_admin_notification

    api_calls = 0
    rows_written = 0
    try:
        if not settings.scheduler_player_stats_enabled:
            return

        if not _budget_ok(job_key):
            log_scheduler_run(conn, job_key, "skipped_budget", start, datetime.now(timezone.utc))
            return

        logger.info("[scheduler] %s: iniciando sync de estadísticas de jugadores", job_key)

        try:
            from app.services.player_intelligence_service import sync_player_stats_bulk
            result = await sync_player_stats_bulk(
                conn,
                date=None,
                league_id=None,
                limit=30,
                max_requests=settings.scheduler_player_stats_lookback_days * 15,
                dry_run=False,
            )
            api_calls = result.get("api_calls", 0)
            rows_written = result.get("rows_written", 0)
        except (ImportError, AttributeError) as exc:
            logger.debug("[scheduler] %s: player_intelligence_service no disponible: %s", job_key, exc)

        if settings.scheduler_player_signals_enabled:
            try:
                from app.services.player_intelligence_service import (
                    build_all_recent_forms,
                    generate_player_signals,
                )
                build_all_recent_forms(conn, windows=[3, 5, 10], dry_run=False)
                generate_player_signals(conn, dry_run=False)
            except (ImportError, AttributeError) as exc:
                logger.debug("[scheduler] %s: generación de señales no disponible: %s", job_key, exc)

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_player_stats", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            api_calls_used=api_calls,
            metadata={"rows_written": rows_written},
        )
        logger.info("[scheduler] %s: %d filas en %.1fs", job_key, rows_written, (end - start).total_seconds())

        if settings.scheduler_notify_alerts and app and rows_written > 0:
            await send_admin_notification(
                app,
                f"✅ <b>Player stats sync</b> completado — {rows_written} filas",
            )

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc, exc_info=True)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))
        if app:
            try:
                await send_admin_notification(app, f"❌ <b>Player stats</b> error: {exc}")
            except Exception:
                pass


async def market_opening_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch opening odds for today's upcoming fixtures."""
    job_key = "market_opening"
    start = datetime.now(timezone.utc)
    conn = _conn()

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state

    api_calls = 0
    rows_written = 0
    try:
        if not _budget_ok(job_key):
            log_scheduler_run(conn, job_key, "skipped_budget", start, datetime.now(timezone.utc))
            return

        logger.info("[scheduler] %s: capturando odds de apertura", job_key)

        try:
            from app.services.market_intelligence_service import sync_market_odds

            # Get today's upcoming fixtures from local DB
            today = start.strftime("%Y-%m-%d")
            rows = conn.execute(
                """
                SELECT DISTINCT provider_fixture_id FROM fixtures
                WHERE date = ? AND status_short NOT IN ('FT','AET','PEN','CANC','AWD','WO')
                LIMIT ?
                """,
                [today, settings.market_intelligence_max_requests_per_run],
            ).fetchall()
            fixture_ids = [r[0] for r in rows if r[0]]

            if fixture_ids:
                result = await sync_market_odds(
                    conn, fixture_ids, snapshot_type="opening", dry_run=False
                )
                api_calls = result.get("api_calls", 0)
                rows_written = result.get("rows_written", 0)
        except (ImportError, AttributeError, Exception) as exc:
            logger.debug("[scheduler] %s: error — %s", job_key, exc)

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_market_opening", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            api_calls_used=api_calls,
            metadata={"rows_written": rows_written},
        )
        logger.info("[scheduler] %s: %d filas en %.1fs", job_key, rows_written, (end - start).total_seconds())

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))


async def market_prematch_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch prematch odds for fixtures kicking off within the configured window."""
    job_key = "market_prematch"
    start = datetime.now(timezone.utc)
    conn = _conn()
    hours = settings.scheduler_market_prematch_hours

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state

    api_calls = 0
    rows_written = 0
    try:
        if not _budget_ok(job_key):
            log_scheduler_run(conn, job_key, "skipped_budget", start, datetime.now(timezone.utc))
            return

        logger.info("[scheduler] %s: ventana=%dh", job_key, hours)

        try:
            from app.services.market_intelligence_service import sync_market_odds
            from datetime import timedelta

            cutoff = (start + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
            rows = conn.execute(
                """
                SELECT DISTINCT provider_fixture_id FROM fixtures
                WHERE date <= ? AND status_short NOT IN ('FT','AET','PEN','CANC','AWD','WO')
                LIMIT ?
                """,
                [cutoff, settings.market_intelligence_max_requests_per_run],
            ).fetchall()
            fixture_ids = [r[0] for r in rows if r[0]]

            if fixture_ids:
                result = await sync_market_odds(
                    conn, fixture_ids, snapshot_type="prematch", dry_run=False
                )
                api_calls = result.get("api_calls", 0)
                rows_written = result.get("rows_written", 0)
        except (ImportError, AttributeError, Exception) as exc:
            logger.debug("[scheduler] %s: error — %s", job_key, exc)

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_market_prematch", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            api_calls_used=api_calls,
            metadata={"window_hours": hours, "rows_written": rows_written},
        )

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))


async def market_closing_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch closing odds for near-kickoff fixtures, then build closing lines."""
    job_key = "market_closing"
    start = datetime.now(timezone.utc)
    conn = _conn()
    minutes = settings.scheduler_market_closing_minutes

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state

    api_calls = 0
    rows_written = 0
    try:
        if not _budget_ok(job_key):
            log_scheduler_run(conn, job_key, "skipped_budget", start, datetime.now(timezone.utc))
            return

        logger.info("[scheduler] %s: ventana=%dm previos al KO", job_key, minutes)

        try:
            from app.services.market_intelligence_service import sync_market_odds, build_closing_lines
            from datetime import timedelta

            window_start = start.strftime("%Y-%m-%d %H:%M:%S")
            window_end = (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
            rows = conn.execute(
                """
                SELECT DISTINCT provider_fixture_id FROM fixtures
                WHERE date BETWEEN ? AND ?
                  AND status_short NOT IN ('FT','AET','PEN','CANC','AWD','WO')
                LIMIT 30
                """,
                [window_start, window_end],
            ).fetchall()
            fixture_ids = [r[0] for r in rows if r[0]]

            if fixture_ids:
                result = await sync_market_odds(
                    conn, fixture_ids, snapshot_type="closing", dry_run=False
                )
                api_calls = result.get("api_calls", 0)
                rows_written = result.get("rows_written", 0)
                build_closing_lines(conn, fixture_ids, dry_run=False)
        except (ImportError, AttributeError, Exception) as exc:
            logger.debug("[scheduler] %s: error — %s", job_key, exc)

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_market_closing", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            api_calls_used=api_calls,
            metadata={"window_minutes": minutes, "rows_written": rows_written},
        )

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))


async def market_clv_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Compute CLV for picks against closing lines at end of day."""
    job_key = "market_clv"
    start = datetime.now(timezone.utc)
    conn = _conn()
    app = _application(context)

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state, send_admin_notification

    computed = 0
    try:
        logger.info("[scheduler] %s: calculando CLV del día", job_key)

        try:
            from app.services.market_intelligence_service import compute_pick_clv

            # Pull today's picks with odds
            today = start.strftime("%Y-%m-%d")
            rows = conn.execute(
                """
                SELECT id, fixture_id, provider_fixture_id, league_id,
                       market_key, selection, best_available_odd
                FROM pick_candidates
                WHERE created_at >= ?
                  AND best_available_odd IS NOT NULL AND best_available_odd > 1.0
                """,
                [today],
            ).fetchall()
            pick_rows = [
                {
                    "pick_candidate_id": r[0],
                    "fixture_id": r[1],
                    "provider_fixture_id": r[2],
                    "league_id": r[3],
                    "market_key": r[4],
                    "selection": r[5],
                    "pick_odds": r[6],
                }
                for r in rows if r[0]
            ]

            if pick_rows:
                result = compute_pick_clv(conn, pick_rows, dry_run=False)
                computed = result.get("computed", 0)
        except (ImportError, AttributeError, Exception) as exc:
            logger.debug("[scheduler] %s: error — %s", job_key, exc)

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_market_clv", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            metadata={"computed": computed},
        )
        logger.info("[scheduler] %s: %d CLV calculados", job_key, computed)

        if settings.scheduler_notify_alerts and app and computed > 0:
            await send_admin_notification(
                app,
                f"📊 <b>CLV</b> calculado — {computed} picks procesados hoy",
            )

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))


async def strategy_learning_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily strategy learning profile rebuild from resolved picks + CLV data."""
    job_key = "strategy_learning"
    start = datetime.now(timezone.utc)
    conn = _conn()

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state

    try:
        if not settings.strategy_learning_enabled:
            return

        logger.info("[scheduler] %s: iniciando rebuild de perfiles de estrategia", job_key)

        from app.services.strategy_learning_service import run_strategy_learning
        result = run_strategy_learning(
            conn,
            days=settings.scheduler_strategy_learning_days,
            dry_run=False,
        )

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_strategy_learning", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            metadata={
                "profiles":    result.get("profiles", 0),
                "annotations": result.get("annotations", 0),
                "adjustments": result.get("adjustments", 0),
            },
        )
        logger.info(
            "[scheduler] %s: %d perfiles en %.1fs",
            job_key, result.get("profiles", 0), (end - start).total_seconds(),
        )

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc, exc_info=True)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))


async def bankroll_risk_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily bankroll & stake sizing computation (dry_run by default)."""
    job_key = "bankroll_risk"
    start = datetime.now(timezone.utc)
    conn = _conn()

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state

    try:
        if not settings.bankroll_engine_enabled:
            return

        logger.info("[scheduler] %s: iniciando calculo de bankroll", job_key)

        from app.services.bankroll_risk_service import run_bankroll_risk
        result = run_bankroll_risk(conn, days=1, dry_run=not settings.bankroll_use_for_selection)

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_bankroll_risk", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            metadata={
                "picks":       result.get("picks", 0),
                "with_stake":  result.get("with_stake", 0),
                "total_units": result.get("total_units", 0.0),
                "risk_level":  result.get("risk_level"),
            },
        )
        logger.info(
            "[scheduler] %s: %d picks, %d con stake, %.2fu, nivel=%s en %.1fs",
            job_key, result.get("picks", 0), result.get("with_stake", 0),
            result.get("total_units", 0.0), result.get("risk_level", "?"),
            (end - start).total_seconds(),
        )

    except Exception as exc:
        logger.error("[scheduler] %s: ERROR — %s", job_key, exc, exc_info=True)
        log_scheduler_run(conn, job_key, "error", start, datetime.now(timezone.utc), error_message=str(exc))


async def model_governance_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nightly model governance — run experiment lab and update activation recommendations."""
    job_key = "model_governance"
    start = datetime.now(timezone.utc)
    conn = _conn()

    from app.services.scheduler_service import log_scheduler_run, update_scheduler_state

    try:
        if not settings.scheduler_governance_enabled:
            return

        logger.info("[scheduler] %s: iniciando governance lab", job_key)

        from app.services.model_governance_service import run_governance_job
        dry_run = not settings.model_governance_write_to_duckdb
        outcome = run_governance_job(conn, days=30, dry_run=dry_run)

        end = datetime.now(timezone.utc)
        update_scheduler_state(conn, "last_model_governance", end.isoformat())
        log_scheduler_run(
            conn, job_key, "completed", start, end,
            metadata={
                "experiments_processed": outcome.get("experiments_processed", 0),
                "dry_run": dry_run,
            },
        )
        logger.info(
            "[scheduler] %s: %d experimentos procesados (dry_run=%s) en %.1fs",
            job_key, outcome.get("experiments_processed", 0), dry_run,
            (end - start).total_seconds(),
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
