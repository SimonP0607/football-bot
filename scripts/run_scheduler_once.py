"""CLI: Run a single scheduler job once (dry-run by default).

Usage:
  python scripts/run_scheduler_once.py --job sync
  python scripts/run_scheduler_once.py --job prematch --execute
  python scripts/run_scheduler_once.py --job live --execute
  python scripts/run_scheduler_once.py --job settlement --execute
  python scripts/run_scheduler_once.py --job report --execute

Jobs:
  sync       — daily fixture + odds sync
  prematch   — prematch intelligence refresh (180-minute window)
  live       — live monitor tick
  settlement — settle pending picks
  report     — generate and print daily report (no Telegram send in dry-run)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

VALID_JOBS = ("sync", "prematch", "live", "settlement", "report")

logger = logging.getLogger(__name__)


class _MockJob:
    def __init__(self, data: dict):
        self.data = data


class _MockCtx:
    def __init__(self, data: dict):
        self.job = _MockJob(data)
        self.application = None


async def _run(job_name: str, dry_run: bool) -> None:
    from app.core.logger import setup_logger
    setup_logger()

    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)

    print(f"\n=== run_scheduler_once ===")
    print(f"Job:     {job_name}")
    print(f"Mode:    {'DRY-RUN (no API calls, no DB writes)' if dry_run else 'EXECUTE'}")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print()

    if dry_run:
        print("[dry-run] Validando imports y configuración…")
        _dry_run_checks(job_name)
        print("[dry-run] OK — para ejecutar de verdad usa --execute")
        return

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

    ctx = _MockCtx(data={"application": None})
    if job_name == "prematch":
        ctx.job.data["window_minutes"] = 180

    print(f"Ejecutando {job_name}…")
    start = datetime.now(timezone.utc)
    try:
        await job_map[job_name](ctx)
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        print(f"\nCompletado en {elapsed:.1f}s")
    except Exception as exc:
        print(f"\nERROR: {exc}")
        logger.error("Job %s failed: %s", job_name, exc, exc_info=True)
        sys.exit(1)


def _dry_run_checks(job_name: str) -> None:
    from app.core.config import settings
    print(f"  scheduler_enabled: {settings.scheduler_enabled}")
    print(f"  scheduler_api_budget_daily: {settings.scheduler_api_budget_daily}")

    try:
        from app.services.scheduled_jobs import (
            daily_sync_job, prematch_refresh_job, live_monitor_job,
            settlement_job, daily_report_job,
        )
        print("  scheduled_jobs: importado OK")
    except ImportError as exc:
        print(f"  scheduled_jobs: ERROR — {exc}")

    try:
        from app.services.scheduler_service import get_scheduler_status
        print("  scheduler_service: importado OK")
    except ImportError as exc:
        print(f"  scheduler_service: ERROR — {exc}")

    try:
        from app.services.alert_service import format_daily_report
        print("  alert_service: importado OK")
    except ImportError as exc:
        print(f"  alert_service: ERROR — {exc}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a single scheduler job.")
    p.add_argument(
        "--job",
        required=True,
        choices=VALID_JOBS,
        help="Job a ejecutar",
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="Ejecutar de verdad (sin este flag solo valida en dry-run)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(_run(args.job, dry_run=not args.execute))
