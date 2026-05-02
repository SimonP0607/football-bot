#!/usr/bin/env python
"""Phase 7: Live Monitor CLI — track pending picks during live matches.

Fetches current fixture states from API-Football, updates DuckDB tracking,
optionally auto-settles finished fixtures and sends Telegram notifications.

Usage:
    python scripts/live_monitor.py --dry-run         # default: read-only
    python scripts/live_monitor.py --execute         # write to DuckDB + Supabase
    python scripts/live_monitor.py --execute --settle-only   # only auto-settle
    python scripts/live_monitor.py --execute --notify        # + Telegram push
    python scripts/live_monitor.py --fixture 1060362 --execute
    python scripts/live_monitor.py --execute --loop  # poll every 60s
    python scripts/live_monitor.py --execute --loop --interval 120
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401


async def _run_once(args: argparse.Namespace) -> dict:
    from app.services.live_monitor_service import run_live_monitor

    return await run_live_monitor(
        dry_run     = not args.execute,
        execute     = args.execute,
        settle      = args.execute and not args.no_settle,
        notify      = args.execute and args.notify,
        fixture_filter = args.fixture,
        hours_before   = args.hours_before,
        hours_after    = args.hours_after,
        max_requests   = args.max_requests,
        verbose        = args.verbose,
    )


def _print_stats(stats: dict, dry_run: bool) -> None:
    mode = "DRY-RUN" if dry_run else "EJECUTADO"
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    print()
    print("=" * 60)
    print(f"  LIVE MONITOR — {mode}  [{now} UTC]")
    print("=" * 60)
    print(f"  Fixtures verificados : {stats['fixtures_checked']}")
    print(f"  En juego             : {stats['fixtures_live']}")
    print(f"  Finalizados          : {stats['fixtures_finished']}")
    print(f"  Picks rastreados     : {stats['picks_tracked']}")
    print(f"  Picks ganando        : {stats['picks_winning']}")
    print(f"  Picks perdiendo      : {stats['picks_losing']}")
    print(f"  Picks abiertos       : {stats['picks_open']}")
    print(f"  Picks liquidados     : {stats['picks_settled']}")
    print(f"  Llamadas API         : {stats['api_calls']}")
    print(f"  Notificaciones       : {len(stats['notifications'])}")

    if stats["errors"]:
        print(f"\n  ERRORES ({len(stats['errors'])}):")
        for err in stats["errors"]:
            print(f"    {err}")

    if stats["notifications"]:
        print(f"\n  NOTIFICACIONES:")
        for n in stats["notifications"]:
            print(f"    [{n['type']}] fixture={n['fixture_id']} pick={n['pick_id']}")
            for line in n["text"].replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").splitlines():
                print(f"      {line}")

    print("=" * 60)
    print()


async def main(args: argparse.Namespace) -> None:
    if args.loop:
        interval = args.interval or settings.live_monitor_interval_seconds
        print(f"Modo loop — intervalo {interval}s. Ctrl+C para detener.")
        try:
            while True:
                stats = await _run_once(args)
                _print_stats(stats, dry_run=not args.execute)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nLoop detenido.")
    else:
        stats = await _run_once(args)
        _print_stats(stats, dry_run=not args.execute)

        if not args.execute:
            print("Sin --execute: no se guardaron cambios.")
            print("Ejecuta con --execute para escribir a DuckDB y liquidar picks.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live monitor de picks en juego (Phase 7)")
    p.add_argument(
        "--execute", action="store_true",
        help="Escribir a DuckDB y Supabase (default: solo lectura/dry-run)",
    )
    p.add_argument(
        "--no-settle", action="store_true",
        help="No auto-liquidar picks aunque el partido haya terminado",
    )
    p.add_argument(
        "--notify", action="store_true",
        help="Enviar notificaciones Telegram (requiere LIVE_MONITOR_NOTIFY=true en .env)",
    )
    p.add_argument(
        "--settle-only", action="store_true",
        help="Solo liquidar partidos finalizados, sin actualizar tracking live",
    )
    p.add_argument(
        "--fixture", type=int, metavar="ID",
        help="Monitorear solo este provider_fixture_id",
    )
    p.add_argument(
        "--hours-before", type=float, default=None, metavar="H",
        help=f"Incluir partidos N horas antes del kickoff (default: {settings.live_monitor_hours_before})",
    )
    p.add_argument(
        "--hours-after", type=float, default=None, metavar="H",
        help=f"Incluir partidos hasta N horas después del kickoff (default: {settings.live_monitor_hours_after})",
    )
    p.add_argument(
        "--max-requests", type=int, default=None, metavar="N",
        help=f"Máx llamadas API (default: {settings.live_monitor_max_requests})",
    )
    p.add_argument(
        "--loop", action="store_true",
        help="Ejecutar en bucle continuo (polling)",
    )
    p.add_argument(
        "--interval", type=int, default=None, metavar="S",
        help=f"Segundos entre iteraciones en modo --loop (default: {settings.live_monitor_interval_seconds})",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Logging detallado",
    )
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
