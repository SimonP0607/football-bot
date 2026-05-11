"""Audit dashboard availability — checks tables, streamlit install, and DuckDB connection.

Usage:
    python scripts/audit_dashboard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

EXPECTED_TABLES = [
    "shadow_value_picks",
    "pick_candidates",
    "stake_recommendations",
    "bankroll_profiles",
    "portfolio_history",
    "parlay_candidates",
    "parlay_results",
    "api_usage",
    "sync_runs",
    "live_snapshots",
    "prematch_intelligence",
    "market_movement_signals",
    "pick_clv_tracking",
    "player_signals",
    "players",
    "teams",
    "scheduler_runs",
    "experiment_registry",
    "experiment_results",
    "activation_recommendations",
    "model_decision_audit",
]


def check_streamlit() -> bool:
    try:
        import streamlit
        print(f"  [OK] streamlit {streamlit.__version__}")
        return True
    except ImportError:
        print("  [MISSING] streamlit -- run: pip install streamlit")
        return False


def check_plotly() -> bool:
    try:
        import plotly
        print(f"  [OK] plotly {plotly.__version__}")
        return True
    except ImportError:
        print("  [MISSING] plotly -- run: pip install plotly")
        return False


def check_duckdb() -> bool:
    try:
        import duckdb
        print(f"  [OK] duckdb {duckdb.__version__}")
        return True
    except ImportError:
        print("  [MISSING] duckdb")
        return False


def check_connection():
    try:
        from app.dashboard.data_sources import get_connection, list_all_tables, table_exists, get_table_count
        conn = get_connection()
        tables = list_all_tables(conn)
        print(f"\n  DuckDB conectado. Tablas encontradas: {len(tables)}")

        present = []
        missing = []
        for t in EXPECTED_TABLES:
            if t in tables:
                count = get_table_count(conn, t)
                present.append((t, count))
            else:
                missing.append(t)

        print(f"\n  Tablas presentes ({len(present)}/{len(EXPECTED_TABLES)}):")
        for name, count in present:
            status = f"{count} filas" if count is not None else "existe"
            print(f"    [OK] {name} -- {status}")

        if missing:
            print(f"\n  Tablas faltantes ({len(missing)}):")
            for name in missing:
                print(f"    [N/A] {name} -- no existe (el dashboard mostrara mensaje amigable)")

        conn.close()
        return True
    except FileNotFoundError as exc:
        print(f"  [ERROR] Base de datos no encontrada: {exc}")
        return False
    except Exception as exc:
        print(f"  [ERROR] No se pudo conectar: {exc}")
        return False


def check_dashboard_files():
    files = [
        "app/dashboard/__init__.py",
        "app/dashboard/main.py",
        "app/dashboard/data_sources.py",
        "app/dashboard/formatters.py",
        "app/dashboard/components.py",
        "app/dashboard/pages/__init__.py",
        "app/dashboard/pages/overview.py",
        "app/dashboard/pages/picks.py",
        "app/dashboard/pages/performance.py",
        "app/dashboard/pages/governance.py",
        "app/dashboard/pages/bankroll.py",
        "app/dashboard/pages/parlays.py",
        "app/dashboard/pages/api_budget.py",
        "app/dashboard/pages/live_prematch.py",
        "app/dashboard/pages/entities_players.py",
        "app/dashboard/pages/system_health.py",
    ]
    ok = True
    for f in files:
        path = ROOT / f
        if path.exists():
            print(f"  [OK] {f}")
        else:
            print(f"  [MISSING] {f}")
            ok = False
    return ok


def main() -> None:
    print("=" * 60)
    print("  Football Bot — Dashboard Audit")
    print("=" * 60)

    print("\n[1] Dependencias Python")
    st_ok = check_streamlit()
    pl_ok = check_plotly()
    db_ok = check_duckdb()

    print("\n[2] Archivos del dashboard")
    files_ok = check_dashboard_files()

    print("\n[3] Conexion DuckDB y tablas")
    conn_ok = check_connection()

    print("\n" + "=" * 60)
    all_ok = st_ok and db_ok and files_ok and conn_ok
    if all_ok:
        print("  Estado: TODO OK -- el dashboard esta listo")
        print("  Lanzar: python scripts/run_dashboard.py")
    else:
        print("  Estado: REVISAR items marcados [MISSING] o [ERROR]")
    print("=" * 60)


if __name__ == "__main__":
    main()
