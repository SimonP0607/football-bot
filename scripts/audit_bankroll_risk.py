#!/usr/bin/env python
"""Phase 14: Audit bankroll risk engine state.

Usage:
    python scripts/audit_bankroll_risk.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.config import settings
    from app.core.logger import setup_logger
except ImportError as e:
    print(f"Error de importacion: {e}")
    sys.exit(1)

logger = logging.getLogger(__name__)


def _ok(label, val):
    print(f"  OK  {label}: {val}")


def _warn(label, val):
    print(f"  WARN  {label}: {val}")


def _fail(label, val):
    print(f"  FAIL  {label}: {val}")


def main() -> None:
    print("\n=== audit_bankroll_risk ===")

    # ── Config ────────────────────────────────────────────────────────────────
    print("\n--- Configuracion ---")
    _ok("BANKROLL_ENGINE_ENABLED",      settings.bankroll_engine_enabled)
    _ok("BANKROLL_USE_FOR_SELECTION",   settings.bankroll_use_for_selection)
    _ok("BANKROLL_DEFAULT_UNITS",       settings.bankroll_default_units)
    _ok("BANKROLL_KELLY_FRACTION",      settings.bankroll_kelly_fraction)
    _ok("BANKROLL_MAX_PICK_UNITS",      settings.bankroll_max_pick_units)
    _ok("BANKROLL_MAX_DAILY_UNITS",     settings.bankroll_max_daily_units)
    _ok("BANKROLL_MIN_EDGE",            settings.bankroll_min_edge)
    _ok("BANKROLL_MIN_CONFIDENCE",      settings.bankroll_min_confidence)
    _ok("SCHEDULER_BANKROLL_ENABLED",   settings.scheduler_bankroll_enabled)
    _ok("SCHEDULER_BANKROLL_TIME",      settings.scheduler_bankroll_time)

    if settings.bankroll_use_for_selection:
        _warn("bankroll_use_for_selection",
              "TRUE — el engine puede modificar seleccion de picks")

    # ── DB Tables ─────────────────────────────────────────────────────────────
    print("\n--- Tablas DuckDB ---")
    try:
        from app.data.local.duckdb_client import get_local_db, init_schema, table_names
        conn = get_local_db()
        init_schema(conn)
        tables = table_names(conn)

        for tbl in ("bankroll_profiles", "stake_recommendations",
                    "portfolio_risk_snapshots", "risk_events"):
            if tbl in tables:
                _ok(f"tabla {tbl}", "existe")
            else:
                _fail(f"tabla {tbl}", "FALTANTE")
    except Exception as exc:
        _fail("DuckDB connection", str(exc))
        return

    # ── Active profile ────────────────────────────────────────────────────────
    print("\n--- Perfil activo ---")
    try:
        from app.data.local.bankroll_risk_repo import get_active_bankroll_profile
        profile = get_active_bankroll_profile(conn)
        if profile:
            _ok("perfil_activo", profile.get("profile_name"))
            _ok("bankroll_units", f"{profile.get('bankroll_units', 0):.0f}u")
            _ok("kelly_fraction", profile.get("kelly_fraction"))
        else:
            _warn("perfil_activo", "no hay perfil — se usaran defaults de settings")
    except Exception as exc:
        _fail("perfil activo", str(exc))

    # ── Pick counts ───────────────────────────────────────────────────────────
    print("\n--- Picks con stake ---")
    try:
        from app.data.local.bankroll_risk_repo import get_stake_recommendations_by_day, get_bankroll_summary
        recs = get_stake_recommendations_by_day(conn, days=1)
        with_stake = [r for r in recs if (r.get("recommended_units") or 0) > 0]
        rejected   = [r for r in recs if r.get("rejection_reason")]
        _ok("picks con stake hoy", len(with_stake))
        _ok("picks rechazados hoy", len(rejected))

        summary = get_bankroll_summary(conn, days=30)
        _ok("total recomendaciones (30d)", summary.get("total_recommendations", 0))
        avg_u = summary.get("avg_recommended_units")
        if avg_u is not None:
            _ok("avg unidades (30d)", f"{avg_u:.2f}u")
    except Exception as exc:
        _fail("stake recommendations", str(exc))

    # ── Portfolio snapshot ────────────────────────────────────────────────────
    print("\n--- Portfolio snapshot ---")
    try:
        from app.data.local.bankroll_risk_repo import get_latest_portfolio_snapshot
        snap = get_latest_portfolio_snapshot(conn)
        if snap:
            _ok("snapshot_date", snap.get("snapshot_date"))
            _ok("portfolio_score", f"{snap.get('portfolio_score', 0):.1f}/100")
            _ok("risk_level", snap.get("risk_level"))
            _ok("total_units", f"{snap.get('total_recommended_units', 0):.2f}u")
            warnings = snap.get("warnings_json") or []
            if warnings:
                _warn("alertas activas", len(warnings))
                for w in warnings:
                    print(f"      - {w}")
            else:
                _ok("alertas", "ninguna")
        else:
            _warn("portfolio_snapshot", "sin datos — ejecuta build_bankroll_risk.py")
    except Exception as exc:
        _fail("portfolio snapshot", str(exc))

    # ── Exposure ──────────────────────────────────────────────────────────────
    print("\n--- Exposicion (30d) ---")
    try:
        from app.services.bankroll_risk_service import compute_exposure, _default_profile
        recs_all = get_stake_recommendations_by_day(conn, days=30)
        prof = get_active_bankroll_profile(conn) or _default_profile()
        if recs_all:
            exp = compute_exposure(recs_all, prof)
            for lg, units in sorted((exp.get("by_league") or {}).items(),
                                    key=lambda x: -x[1])[:3]:
                _ok(f"liga_{lg}_exposure", f"{units:.2f}u")
            for mkt, units in sorted((exp.get("by_market") or {}).items(),
                                     key=lambda x: -x[1])[:3]:
                _ok(f"mkt_{mkt}_exposure", f"{units:.2f}u")
        else:
            _warn("exposicion", "sin datos de stakes")
    except Exception as exc:
        _fail("exposicion", str(exc))

    print("\n=== Auditoria completada ===\n")


if __name__ == "__main__":
    setup_logger()
    main()
