"""CLI: Audit the AI Router — logs, intents, errors, recommendations.

Usage:
  python scripts/audit_ai_router.py
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
    print(f"\nAI Router Audit — {today}")

    # ── Configuration ─────────────────────────────────────────────────────────
    _section("1. Configuración")
    print(f"  ai_router_enabled:        {settings.ai_router_enabled}")
    print(f"  ai_router_provider:       {settings.ai_router_provider}")
    print(f"  ai_router_model:          {settings.ai_router_model or '(none)'}")
    print(f"  ai_router_min_confidence: {settings.ai_router_min_confidence}")
    print(f"  ai_router_timeout_seconds:{settings.ai_router_timeout_seconds}")
    print(f"  ai_router_log_queries:    {settings.ai_router_log_queries}")
    print(f"  ai_router_safe_mode:      {settings.ai_router_safe_mode}")
    print(f"  ai_router_allow_parlay:   {settings.ai_router_allow_parlay}")
    print(f"  ai_router_allow_live:     {settings.ai_router_allow_live}")

    # ── Table counts ──────────────────────────────────────────────────────────
    _section("2. Tablas DuckDB")
    for table in ("ai_router_logs", "ai_user_context", "ai_intent_examples"):
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {cnt}")
        except Exception as exc:
            print(f"  {table}: ERROR — {exc}")

    # ── Total stats ───────────────────────────────────────────────────────────
    _section("3. Estadísticas totales")
    try:
        total = conn.execute("SELECT COUNT(*) FROM ai_router_logs").fetchone()[0]
        errors = conn.execute(
            "SELECT COUNT(*) FROM ai_router_logs WHERE response_status = 'error'"
        ).fetchone()[0]
        clarifs = conn.execute(
            "SELECT COUNT(*) FROM ai_router_logs WHERE handler_target LIKE 'clarification%'"
        ).fetchone()[0]
        print(f"  Total consultas:    {total}")
        print(f"  Errores:            {errors}")
        print(f"  Aclaraciones:       {clarifs}")
        if total > 0:
            print(f"  Tasa de error:      {errors/total*100:.1f}%")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ── Top intents ────────────────────────────────────────────────────────────
    _section("4. Intents más usados")
    try:
        rows = conn.execute(
            """
            SELECT detected_intent, COUNT(*) as cnt,
                   AVG(confidence) as avg_conf
            FROM ai_router_logs
            GROUP BY detected_intent
            ORDER BY cnt DESC
            LIMIT 15
            """
        ).fetchall()
        if rows:
            print(f"  {'Intent':<25} {'Count':>6} {'Avg Conf':>10}")
            print(f"  {'─'*25} {'─'*6} {'─'*10}")
            for intent, cnt, avg_conf in rows:
                print(f"  {intent:<25} {cnt:>6} {avg_conf*100:>9.1f}%")
        else:
            print("  Sin logs todavía.")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ── Unknown intents ────────────────────────────────────────────────────────
    _section("5. Mensajes no reconocidos (unknown)")
    try:
        rows = conn.execute(
            """
            SELECT raw_message, confidence, created_at
            FROM ai_router_logs
            WHERE detected_intent = 'unknown'
            ORDER BY created_at DESC
            LIMIT 10
            """
        ).fetchall()
        if rows:
            for msg, conf, ts in rows:
                print(f"  [{ts}] conf={conf:.2f}  {msg[:60]!r}")
        else:
            print("  Sin mensajes desconocidos. Buen cobertura.")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ── Recent queries ─────────────────────────────────────────────────────────
    _section("6. Últimas 20 consultas")
    try:
        rows = conn.execute(
            """
            SELECT created_at, detected_intent, confidence, handler_target,
                   raw_message, response_status
            FROM ai_router_logs
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()
        if rows:
            for ts, intent, conf, target, raw, status in rows:
                icon = "OK" if status == "ok" else "ERR"
                raw_s = (raw or "")[:40]
                print(f"  [{icon}] {ts}  {intent:<20} {conf:.2f}  {raw_s!r}")
        else:
            print("  Sin logs todavía.")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ── Active users ──────────────────────────────────────────────────────────
    _section("7. Usuarios activos")
    try:
        rows = conn.execute(
            """
            SELECT user_id, COUNT(*) as queries, MAX(created_at) as last_seen
            FROM ai_router_logs
            GROUP BY user_id
            ORDER BY queries DESC
            """
        ).fetchall()
        if rows:
            for uid, cnt, last in rows:
                print(f"  user_id={uid}  consultas={cnt}  última={last}")
        else:
            print("  Sin usuarios registrados.")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ── Recommendations ───────────────────────────────────────────────────────
    _section("8. Recomendaciones")
    recommendations: list[str] = []
    try:
        total = conn.execute("SELECT COUNT(*) FROM ai_router_logs").fetchone()[0]
        unknown_cnt = conn.execute(
            "SELECT COUNT(*) FROM ai_router_logs WHERE detected_intent = 'unknown'"
        ).fetchone()[0]
        if total > 10 and unknown_cnt / max(total, 1) > 0.20:
            recommendations.append(
                f"Alta tasa de mensajes desconocidos ({unknown_cnt/total*100:.0f}%). "
                "Considera añadir más patrones a classify_with_rules()."
            )
        error_cnt = conn.execute(
            "SELECT COUNT(*) FROM ai_router_logs WHERE response_status = 'error'"
        ).fetchone()[0]
        if error_cnt > 0:
            recommendations.append(f"{error_cnt} errores en el router. Revisar logs del sistema.")
        if not settings.ai_router_enabled:
            recommendations.append(
                "AI_ROUTER_ENABLED=false. El router está desactivado. "
                "Actívalo en .env con AI_ROUTER_ENABLED=true."
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
