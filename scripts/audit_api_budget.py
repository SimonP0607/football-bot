#!/usr/bin/env python
"""API budget diagnostic: daily quota, call breakdown, budget status.

Reads from the local JSONL log (data/local/api_usage_log.jsonl) and the
in-memory rate-limit state populated by any prior API call in this process.

Usage:
    python scripts/audit_api_budget.py              # today's usage
    python scripts/audit_api_budget.py --days 7     # last 7 calendar days
    python scripts/audit_api_budget.py --json        # machine-readable output
    python scripts/audit_api_budget.py --verbose     # show per-endpoint detail
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401 -- loads .env
from app.services.api_budget_service import (
    get_daily_state,
    get_log_path,
    get_today_log,
    get_log_for_days,
)


def _pct(used: int | None, limit: int | None) -> str:
    if used is None or limit is None or limit == 0:
        return "?%"
    return f"{used / limit * 100:.1f}%"


def _status_label(status: str) -> str:
    return {"ok": "OK", "warning": "ADVERTENCIA", "critical": "CRITICO", "unknown": "desconocido"}.get(
        status, status.upper()
    )


def _print_report(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)

    if args.days and args.days > 1:
        entries = get_log_for_days(args.days)
        period_label = f"ultimos {args.days} dias"
    else:
        entries = get_today_log()
        period_label = "hoy (UTC)"

    state = get_daily_state()
    remaining = state["remaining"]
    limit = state["limit"]
    used_today = state["used_today"]
    status = state["status"]

    print()
    print("=" * 60)
    print("  AUDITORIA DE PRESUPUESTO API-Football")
    print(f"  {now.strftime('%Y-%m-%d %H:%M')} UTC  |  Periodo: {period_label}")
    print("=" * 60)

    # -- Quota summary ----------------------------------------------------------
    print()
    print("  CUOTA DIARIA")
    print(f"  Estado:     {_status_label(status)}")
    if remaining is not None and limit is not None:
        print(f"  Restantes:  {remaining} / {limit}  ({_pct(limit - remaining, limit)} usado)")
        print(f"  Usadas hoy: {used_today} llamadas (registro JSONL)")
    elif remaining is not None:
        print(f"  Restantes:  {remaining} (limite desconocido)")
    else:
        print("  Restantes:  desconocido (ejecuta sync para actualizar el estado)")

    rate = state.get("rate_state", {})
    m_rem = rate.get("minute_remaining")
    m_lim = rate.get("minute_limit")
    if m_rem is not None:
        print(f"  Por minuto: {m_rem} / {m_lim}")

    last_ep = rate.get("last_endpoint")
    last_at = rate.get("last_updated_at")
    if last_ep:
        print(f"  Ultima API: {last_ep}  ({(last_at or '')[:16]})")

    # -- Budget gates -----------------------------------------------------------
    print()
    print("  PERMISOS POR PRIORIDAD")
    can = state["can_run_by_priority"]
    for priority in ("critical", "high", "medium", "low", "batch"):
        icon = "SI" if can.get(priority) else "NO"
        print(f"    {priority:<10} {icon}")

    if not entries:
        log_path = get_log_path()
        print()
        if log_path.exists():
            print(f"  Sin registros en el periodo. Log: {log_path}")
        else:
            print(f"  Log no existe aun: {log_path}")
            print("  Se crea automaticamente en la primera llamada API.")
        print()
        print("=" * 60)
        print()
        return

    # -- Calls by endpoint ------------------------------------------------------
    by_endpoint: dict[str, list[dict]] = defaultdict(list)
    by_source: dict[str, list[dict]] = defaultdict(list)
    errors: list[dict] = []

    for e in entries:
        ep = e.get("endpoint", "?")
        src = e.get("source_script", "") or "(directo)"
        sc = e.get("status_code", 200)
        by_endpoint[ep].append(e)
        by_source[src].append(e)
        if sc >= 400:
            errors.append(e)

    print()
    print(f"  LLAMADAS POR ENDPOINT  ({len(entries)} total)")
    print(f"  {'Endpoint':<35}  {'N':>4}  {'Avg ms':>7}  {'Err':>4}")
    print("  " + "-" * 55)
    for ep, rows in sorted(by_endpoint.items(), key=lambda kv: -len(kv[1])):
        n = len(rows)
        avg_ms = sum(r.get("duration_ms", 0) for r in rows) / n if n else 0
        errs = sum(1 for r in rows if r.get("status_code", 200) >= 400)
        print(f"  {ep[:35]:<35}  {n:>4}  {avg_ms:>6.0f}ms  {errs:>4}")

    # -- Calls by source script -------------------------------------------------
    if len(by_source) > 1 or args.verbose:
        print()
        print(f"  LLAMADAS POR SCRIPT  ({len(entries)} total)")
        print(f"  {'Script':<30}  {'N':>4}  {'Avg ms':>7}")
        print("  " + "-" * 46)
        for src, rows in sorted(by_source.items(), key=lambda kv: -len(kv[1])):
            n = len(rows)
            avg_ms = sum(r.get("duration_ms", 0) for r in rows) / n if n else 0
            print(f"  {src[:30]:<30}  {n:>4}  {avg_ms:>6.0f}ms")

    # -- Errors -----------------------------------------------------------------
    if errors:
        print()
        print(f"  ERRORES ({len(errors)})")
        for e in errors[:10]:
            ts = e.get("ts", "")[:16]
            ep = e.get("endpoint", "?")
            sc = e.get("status_code", "?")
            src = e.get("source_script", "?")
            print(f"    {ts}  {ep}  HTTP {sc}  [{src}]")
        if len(errors) > 10:
            print(f"    ... y {len(errors) - 10} mas")

    # -- Recommendations --------------------------------------------------------
    print()
    print("  RECOMENDACIONES")
    if status == "ok" and remaining is not None and remaining >= 3500:
        print("    Cuota amplia: sync_today, sync_enrichment_today --execute disponibles")
    elif status == "ok" and remaining is not None and remaining >= 2000:
        print("    Cuota normal: sync_today disponible; enriquecimiento moderado OK")
    elif status == "warning":
        print("    ! Cuota baja: evita backfill y enriquecimiento opcional")
        print("    ! Solo sync_today (prioridad high) y settlement (critical)")
    elif status == "critical":
        print("    !! Cuota CRITICA: solo settlement. Postpone todo lo demas.")
    else:
        print("    Estado desconocido: ejecuta sync para actualizar la cuota")

    can_sync = can.get("high", False)
    can_enrich = can.get("medium", False)
    can_batch = can.get("batch", False)

    print(f"    sync_today.py:             {'OK' if can_sync else 'BLOQUEADO'}")
    print(f"    sync_enrichment_today.py:  {'OK' if can_enrich else 'BLOQUEADO'}")
    print(f"    backfill historico:        {'OK' if can_batch else 'BLOQUEADO'}")

    print()
    print(f"  Log: {get_log_path()}")
    print()
    print("=" * 60)
    print()


def _print_json(args: argparse.Namespace) -> None:
    state = get_daily_state()
    if args.days and args.days > 1:
        entries = get_log_for_days(args.days)
    else:
        entries = get_today_log()

    by_endpoint: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    for e in entries:
        by_endpoint[e.get("endpoint", "?")] += 1
        by_source[e.get("source_script", "") or "(directo)"] += 1

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "period_entries": len(entries),
        "by_endpoint": dict(by_endpoint),
        "by_source": dict(by_source),
        "errors": len([e for e in entries if e.get("status_code", 200) >= 400]),
    }
    print(json.dumps(output, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="Auditoria de presupuesto API-Football")
    parser.add_argument("--days", type=int, default=1, metavar="N",
                        help="Numero de dias a analizar (default: 1 = hoy)")
    parser.add_argument("--json", action="store_true",
                        help="Salida en formato JSON")
    parser.add_argument("--verbose", action="store_true",
                        help="Mostrar detalle adicional por script")
    args = parser.parse_args()

    if args.json:
        _print_json(args)
    else:
        _print_report(args)


if __name__ == "__main__":
    main()
