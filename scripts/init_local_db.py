#!/usr/bin/env python
"""Initialize the local DuckDB history database.

Creates the database file and applies the schema (idempotent).
Safe to run multiple times — uses IF NOT EXISTS throughout.

Usage:
    python scripts/init_local_db.py
    python scripts/init_local_db.py --path ./data/custom.duckdb
"""

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.config import settings
    from app.core.logger import setup_logger
    from app.data.local.duckdb_client import get_local_db, init_schema, table_names
except ImportError as e:
    print(f"Error de importación: {e}")
    sys.exit(1)

logger = logging.getLogger(__name__)

EXPECTED_TABLES = [
    # 001_history_schema.sql
    "fixtures_history",
    "standings_history",
    "team_stats_history",
    "odds_history",
    "published_picks_history",
    "pick_results_history",
    "backtest_runs",
    "backtest_metrics",
    "history_metadata",
    # 002_backtest_schema.sql
    "competition_context",
    "team_elo_history",
    "training_samples",
    # 003_value_engine_schema.sql
    "calibration_registry",
    "market_quality_summary",
    "shadow_value_picks",
    # 004_entity_catalog_schema.sql
    "team_identity",
    "team_season_membership",
    "player_identity",
    "squad_membership",
    "entity_sync_runs",
    # 005_availability_schema.sql
    "fixture_injuries_history",
    "fixture_lineups_history",
    "player_availability_signals",
    "team_availability_summary",
    # 006_prematch_intelligence_schema.sql
    "prematch_odds_movement",
    "prematch_fixture_alerts",
    "prematch_lineup_status",
    # 007_live_monitor_schema.sql
    "live_fixture_snapshots",
    "live_fixture_events",
    "live_pick_tracking",
    "live_notifications_log",
    # 008_parlay_engine_schema.sql
    "parlay_candidates",
    "parlay_legs",
    "parlay_results",
    "parlay_risk_rules",
    # 009_ai_router_schema.sql
    "ai_router_logs",
    "ai_user_context",
    "ai_intent_examples",
    # 010_scheduler_schema.sql
    "scheduler_runs",
    "scheduler_notifications",
    "scheduler_state",
    # 011_player_intelligence_schema.sql
    "player_fixture_stats",
    "player_season_profiles",
    "player_recent_form",
    "player_prop_signals",
    # 012_market_intelligence_schema.sql
    "market_odds_history",
    "market_closing_lines",
    "pick_clv_results",
    "market_movement_signals",
    # 013_strategy_learning_schema.sql
    "strategy_profiles",
    "strategy_learning_runs",
    "strategy_adjustments",
    "pick_learning_annotations",
    # 014_bankroll_risk_schema.sql
    "bankroll_profiles",
    "stake_recommendations",
    "portfolio_risk_snapshots",
    "risk_events",
    # 015_model_governance_schema.sql
    "experiment_registry",
    "experiment_pick_assignments",
    "experiment_results",
    "model_decision_audit",
    "activation_recommendations",
]


def main(db_path: str | None) -> None:
    path = db_path or settings.local_db_path
    resolved = Path(path).resolve()

    print(f"\n=== init_local_db ===")
    print(f"Base de datos: {resolved}")

    conn = get_local_db(path)
    n = init_schema(conn)
    print(f"Schema aplicado: {n} statements")

    tables = table_names(conn)
    print(f"\nTablas creadas ({len(tables)}):")
    for t in tables:
        mark = "  OK" if t in EXPECTED_TABLES else "  ?"
        print(f"{mark} {t}")

    missing = [t for t in EXPECTED_TABLES if t not in tables]
    if missing:
        print(f"\n  FAIL — Tablas faltantes: {', '.join(missing)}")
        sys.exit(1)

    print(f"\nOK — Base local lista en: {resolved}\n")
    print("Próximos pasos:")
    print("  python scripts/test_local_db.py    # verificar inserción y lectura")
    print("  python scripts/archive_local.py    # archivar datos warm de Supabase (opcional)")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Initialize local DuckDB history database.")
    p.add_argument(
        "--path",
        default=None,
        help="Override LOCAL_DB_PATH from .env.",
    )
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    args = parse_args()
    main(args.path)
