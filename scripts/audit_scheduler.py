"""CLI: Audit the Scheduler — config, job runs, notifications, errors, recommendations.

Usage:
  python scripts/audit_scheduler.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


def _section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main() -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.core.config import settings

    conn = get_local_db()
    init_schema(conn)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"\nScheduler Audit — {today}")

    # ── Configuration ─────────────────────────────────────────────────────────
    _section("1. Configuración")
    print(f"  scheduler_enabled:             {settings.scheduler_enabled}")
    print(f"  scheduler_timezone:            {settings.scheduler_timezone}")
    print(f"  scheduler_daily_sync_enabled:  {settings.scheduler_daily_sync_enabled}")
    print(f"  scheduler_daily_sync_time:     {settings.scheduler_daily_sync_time}")
    print(f"  scheduler_prematch_enabled:    {settings.scheduler_prematch_enabled}")
    print(f"  scheduler_prematch_windows:    {settings.scheduler_prematch_windows}")
    print(f"  scheduler_prematch_max_fixtures:{settings.scheduler_prematch_max_fixtures}")
    print(f"  scheduler_live_monitor_enabled:{settings.scheduler_live_monitor_enabled}")
    print(f"  scheduler_live_interval_seconds:{settings.scheduler_live_interval_seconds}")
    print(f"  scheduler_settlement_enabled:  {settings.scheduler_settlement_enabled}")
    print(f"  scheduler_settlement_time:     {settings.scheduler_settlement_time}")
    print(f"  scheduler_daily_report_enabled:{settings.scheduler_daily_report_enabled}")
    print(f"  scheduler_report_time:         {settings.scheduler_report_time}")
    print(f"  scheduler_api_budget_daily:    {settings.scheduler_api_budget_daily}")
    print(f"  scheduler_notify_alerts:       {settings.scheduler_notify_alerts}")

    # ── JobQueue availability ─────────────────────────────────────────────────
    _section("2. JobQueue")
    try:
        import importlib
        has_jq = importlib.util.find_spec("apscheduler") is not None
        print(f"  apscheduler disponible: {has_jq}")
        print("  (PTB JobQueue requiere python-telegram-bot[job-queue])")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ── Table counts ──────────────────────────────────────────────────────────
    _section("3. Tablas DuckDB")
    for table in ("scheduler_runs", "scheduler_notifications", "scheduler_state"):
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {cnt}")
        except Exception as exc:
            print(f"  {table}: ERROR — {exc}")

    # ── Job run history ───────────────────────────────────────────────────────
    _section("4. Historial de runs (últimos 20)")
    try:
        rows = conn.execute(
            """
            SELECT job_key, status, started_at, duration_ms, api_calls_used, error_message
            FROM scheduler_runs
            ORDER BY started_at DESC
            LIMIT 20
            """
        ).fetchall()
        if rows:
            print(f"  {'Job':<20} {'Status':<20} {'Started':<22} {'ms':>6} {'API':>5}")
            print(f"  {'─'*20} {'─'*20} {'─'*22} {'─'*6} {'─'*5}")
            for job, status, started, dur, api, err in rows:
                icon = "OK" if status == "completed" else ("SK" if "skipped" in (status or "") else "ERR")
                print(f"  [{icon}] {job:<17} {status:<20} {str(started):<22} {(dur or 0):>6} {(api or 0):>5}")
                if err:
                    print(f"       {err[:70]}")
        else:
            print("  Sin runs todavía.")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ── Job stats per key ─────────────────────────────────────────────────────
    _section("5. Estadísticas por job")
    try:
        rows = conn.execute(
            """
            SELECT job_key,
                   COUNT(*) as total,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as ok,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as err,
                   AVG(duration_ms) as avg_ms,
                   MAX(started_at) as last_run
            FROM scheduler_runs
            GROUP BY job_key
            ORDER BY last_run DESC
            """
        ).fetchall()
        if rows:
            print(f"  {'Job':<22} {'Total':>6} {'OK':>5} {'Err':>5} {'AvgMs':>7} {'Last run'}")
            print(f"  {'─'*22} {'─'*6} {'─'*5} {'─'*5} {'─'*7} {'─'*22}")
            for job, total, ok, err, avg_ms, last in rows:
                print(f"  {job:<22} {total:>6} {ok:>5} {err:>5} {(avg_ms or 0):>7.0f} {last}")
        else:
            print("  Sin estadísticas todavía.")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ── Scheduler state ───────────────────────────────────────────────────────
    _section("6. Estado del scheduler (last runs)")
    try:
        rows = conn.execute(
            "SELECT key, value_json, updated_at FROM scheduler_state ORDER BY key"
        ).fetchall()
        if rows:
            for key, val, updated in rows:
                v = val if val and val != "null" else "nunca"
                print(f"  {key:<30} {v:<30} {updated}")
        else:
            print("  Sin estado guardado.")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ── Notifications ─────────────────────────────────────────────────────────
    _section("7. Notificaciones (últimas 20)")
    try:
        rows = conn.execute(
            """
            SELECT notification_type, sent_status, created_at, title
            FROM scheduler_notifications
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()
        if rows:
            for ntype, status, ts, title in rows:
                icon = "✓" if status == "sent" else ("✗" if status == "failed" else "·")
                print(f"  [{icon}] {ts}  {ntype:<20} {(title or '')[:40]}")
        else:
            print("  Sin notificaciones todavía.")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ── API budget ────────────────────────────────────────────────────────────
    _section("8. API Budget")
    try:
        from app.services.api_budget_service import get_budget_status
        bs = get_budget_status()
        print(f"  Calls today:     {bs.get('calls_today', '?')}")
        print(f"  Remaining today: {bs.get('remaining_today', '?')}")
        print(f"  Daily limit:     {bs.get('daily_limit', '?')}")
    except Exception as exc:
        print(f"  No disponible: {exc}")

    # ── Recommendations ───────────────────────────────────────────────────────
    _section("9. Recomendaciones")
    recommendations: list[str] = []

    if not settings.scheduler_enabled:
        recommendations.append(
            "SCHEDULER_ENABLED=false. Actívalo en .env para habilitar jobs automáticos."
        )

    try:
        error_cnt = conn.execute(
            "SELECT COUNT(*) FROM scheduler_runs WHERE status = 'error'"
        ).fetchone()[0]
        if error_cnt > 0:
            recommendations.append(f"{error_cnt} runs con error. Revisar sección 4.")
    except Exception:
        pass

    try:
        budget_cnt = conn.execute(
            "SELECT COUNT(*) FROM scheduler_runs WHERE status = 'skipped_budget'"
        ).fetchone()[0]
        if budget_cnt > 0:
            recommendations.append(
                f"{budget_cnt} runs saltados por budget. Considera aumentar SCHEDULER_API_BUDGET_DAILY."
            )
    except Exception:
        pass

    if recommendations:
        for r in recommendations:
            print(f"  ⚠  {r}")
    else:
        print("  Sin recomendaciones pendientes.")

    print(f"\n{'='*60}")
    print("  Audit completo.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
