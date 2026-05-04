#!/usr/bin/env python
"""Phase 12: Audit market intelligence tables and data quality.

Usage:
  python scripts/audit_market_intelligence.py
  python scripts/audit_market_intelligence.py --verbose
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
_src_root = _scripts_dir.parent
sys.path.insert(0, str(_src_root))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit market intelligence data quality")
    p.add_argument("-v", "--verbose", action="store_true", help="Show extra detail")
    return p.parse_args()


def _section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def _check_tables(conn) -> None:
    _section("1. Tablas Market Intelligence")
    tables = [
        "market_odds_history",
        "market_closing_lines",
        "pick_clv_results",
        "market_movement_signals",
    ]
    for t in tables:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  ✓ {t:<40} {cnt:>8} filas")
        except Exception as exc:
            print(f"  ✗ {t:<40} ERROR: {exc}")


def _check_sequences(conn) -> None:
    _section("2. Secuencias")
    seqs = [
        "market_odds_history_seq",
        "market_closing_lines_seq",
        "pick_clv_results_seq",
        "market_movement_signals_seq",
    ]
    for s in seqs:
        try:
            val = conn.execute(f"SELECT currval('{s}')").fetchone()
            print(f"  ✓ {s:<45} currval={val[0] if val else 'N/A'}")
        except Exception:
            # currval fails if sequence was never advanced — still OK
            print(f"  ✓ {s:<45} (no usada aún)")


def _check_odds_coverage(conn, verbose: bool) -> None:
    _section("3. Cobertura de Odds History")
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_snapshots,
                COUNT(DISTINCT provider_fixture_id) AS fixtures,
                COUNT(DISTINCT bookmaker_id) AS bookmakers,
                COUNT(DISTINCT market_key) AS markets,
                MIN(snapshot_time) AS oldest,
                MAX(snapshot_time) AS newest
            FROM market_odds_history
            """
        ).fetchone()
        if row:
            print(f"  Snapshots totales  : {row[0]}")
            print(f"  Fixtures distintos : {row[1]}")
            print(f"  Bookmakers         : {row[2]}")
            print(f"  Markets            : {row[3]}")
            print(f"  Más antiguo        : {row[4]}")
            print(f"  Más reciente       : {row[5]}")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    if verbose:
        try:
            rows = conn.execute(
                """
                SELECT market_key, COUNT(*) AS n
                FROM market_odds_history
                GROUP BY market_key
                ORDER BY n DESC
                LIMIT 20
                """
            ).fetchall()
            print("\n  Top mercados:")
            for r in rows:
                print(f"    {r[0]:<35} {r[1]:>6}")
        except Exception:
            pass


def _check_closing_lines(conn, verbose: bool) -> None:
    _section("4. Closing Lines")
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(DISTINCT provider_fixture_id) AS fixtures,
                SUM(CASE WHEN movement_label = 'steam_towards_selection' THEN 1 ELSE 0 END) AS steam,
                SUM(CASE WHEN movement_label = 'drift_against_selection' THEN 1 ELSE 0 END) AS drift,
                SUM(CASE WHEN movement_label = 'stable' THEN 1 ELSE 0 END) AS stable,
                SUM(CASE WHEN movement_label = 'no_data' THEN 1 ELSE 0 END) AS no_data
            FROM market_closing_lines
            """
        ).fetchone()
        if row:
            print(f"  Total líneas de cierre : {row[0]}")
            print(f"  Fixtures cubiertos     : {row[1]}")
            print(f"  Steam                  : {row[2]}")
            print(f"  Drift                  : {row[3]}")
            print(f"  Stable                 : {row[4]}")
            print(f"  Sin datos              : {row[5]}")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    if verbose:
        try:
            rows = conn.execute(
                """
                SELECT movement_label, COUNT(*) AS n, AVG(ABS(implied_delta)) AS avg_delta
                FROM market_closing_lines
                WHERE implied_delta IS NOT NULL
                GROUP BY movement_label
                ORDER BY n DESC
                """
            ).fetchall()
            if rows:
                print("\n  Por movement_label:")
                for r in rows:
                    avg = f"{r[2]*100:.2f}pp" if r[2] else "N/A"
                    print(f"    {r[0]:<35} {r[1]:>5}  avg_delta={avg}")
        except Exception:
            pass


def _check_clv(conn, verbose: bool) -> None:
    _section("5. CLV Results")
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN beat_closing_line THEN 1 ELSE 0 END) AS beat,
                AVG(clv_percent) AS avg_clv,
                SUM(CASE WHEN clv_result = 'positive' THEN 1 ELSE 0 END) AS pos,
                SUM(CASE WHEN clv_result = 'neutral'  THEN 1 ELSE 0 END) AS neu,
                SUM(CASE WHEN clv_result = 'negative' THEN 1 ELSE 0 END) AS neg,
                SUM(CASE WHEN clv_result = 'no_data'  THEN 1 ELSE 0 END) AS nd
            FROM pick_clv_results
            """
        ).fetchone()
        if row:
            total = row[0] or 0
            beat = row[1] or 0
            avg = row[2]
            print(f"  Total CLV computados   : {total}")
            if total > 0:
                print(f"  Beat closing line      : {beat} ({beat/total*100:.1f}%)")
                print(f"  CLV promedio           : {avg*100:.2f}pp" if avg else "  CLV promedio           : N/A")
                print(f"  Positivo               : {row[3]}")
                print(f"  Neutro                 : {row[4]}")
                print(f"  Negativo               : {row[5]}")
                print(f"  Sin datos              : {row[6]}")
    except Exception as exc:
        print(f"  ERROR: {exc}")


def _check_signals(conn, verbose: bool) -> None:
    _section("6. Market Movement Signals")
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(DISTINCT provider_fixture_id) AS fixtures,
                SUM(CASE WHEN signal_type = 'steam'        THEN 1 ELSE 0 END) AS steam,
                SUM(CASE WHEN signal_type = 'drift'        THEN 1 ELSE 0 END) AS drift,
                SUM(CASE WHEN signal_type = 'sharp_move'   THEN 1 ELSE 0 END) AS sharp,
                SUM(CASE WHEN signal_type = 'stable'       THEN 1 ELSE 0 END) AS stable,
                SUM(CASE WHEN signal_type = 'reverse_line' THEN 1 ELSE 0 END) AS reverse
            FROM market_movement_signals
            """
        ).fetchone()
        if row:
            print(f"  Total señales          : {row[0]}")
            print(f"  Fixtures cubiertos     : {row[1]}")
            print(f"  Steam                  : {row[2]}")
            print(f"  Drift                  : {row[3]}")
            print(f"  Sharp move             : {row[4]}")
            print(f"  Stable                 : {row[5]}")
            print(f"  Reverse line           : {row[6]}")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    if verbose:
        try:
            rows = conn.execute(
                """
                SELECT market_key, signal_type, COUNT(*) AS n
                FROM market_movement_signals
                GROUP BY market_key, signal_type
                ORDER BY n DESC
                LIMIT 15
                """
            ).fetchall()
            if rows:
                print("\n  Top señales por mercado:")
                for r in rows:
                    print(f"    {r[0]:<30} {r[1]:<15} {r[2]:>5}")
        except Exception:
            pass


def _check_config() -> None:
    _section("7. Configuración (settings)")
    try:
        from app.core.config import settings
        print(f"  MARKET_INTELLIGENCE_ENABLED        : {settings.market_intelligence_enabled}")
        print(f"  MARKET_INTELLIGENCE_USE_LIVE_ODDS  : {settings.market_intelligence_use_live_odds}")
        print(f"  MARKET_INTELLIGENCE_MIN_MOVEMENT   : {settings.market_intelligence_min_movement}")
        print(f"  MARKET_INTELLIGENCE_CLV_NEUTRAL_BAND: {settings.market_intelligence_clv_neutral_band}")
        print(f"  MARKET_INTELLIGENCE_MAX_REQUESTS   : {settings.market_intelligence_max_requests_per_run}")
        print(f"  SCHEDULER_MARKET_ENABLED           : {settings.scheduler_market_enabled}")
        print(f"  SCHEDULER_MARKET_OPENING_TIME      : {settings.scheduler_market_opening_time}")
        print(f"  SCHEDULER_CLV_TIME                 : {settings.scheduler_clv_time}")
    except Exception as exc:
        print(f"  ERROR importando settings: {exc}")


def _check_recommendations(conn) -> None:
    _section("8. Recomendaciones")
    recs: list[str] = []

    try:
        cnt = conn.execute("SELECT COUNT(*) FROM market_odds_history").fetchone()[0]
        if cnt == 0:
            recs.append("Sin datos de odds history — ejecuta: python scripts/sync_market_odds.py --dry-run")
    except Exception:
        recs.append("Tabla market_odds_history no disponible — ejecuta init_local_db.py")

    try:
        cnt = conn.execute("SELECT COUNT(*) FROM market_closing_lines").fetchone()[0]
        if cnt == 0:
            recs.append("Sin closing lines — ejecuta: python scripts/build_closing_lines.py --dry-run")
    except Exception:
        pass

    try:
        cnt = conn.execute("SELECT COUNT(*) FROM pick_clv_results").fetchone()[0]
        if cnt == 0:
            recs.append("Sin CLV computado — ejecuta: python scripts/compute_pick_clv.py --dry-run")
    except Exception:
        pass

    if not recs:
        print("  ✓ Todo parece en orden.")
    else:
        for r in recs:
            print(f"  → {r}")


def main() -> None:
    args = _parse_args()

    from app.data.local.duckdb_client import get_local_db, init_schema

    conn = get_local_db()
    init_schema(conn)

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║       AUDIT — Market Intelligence (Phase 12)             ║")
    print("╚══════════════════════════════════════════════════════════╝")

    _check_tables(conn)
    _check_sequences(conn)
    _check_odds_coverage(conn, args.verbose)
    _check_closing_lines(conn, args.verbose)
    _check_clv(conn, args.verbose)
    _check_signals(conn, args.verbose)
    _check_config()
    _check_recommendations(conn)
    print()


if __name__ == "__main__":
    main()
