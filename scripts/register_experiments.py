#!/usr/bin/env python
"""Phase 15: Register default experiment variants in DuckDB.

Usage:
    python scripts/register_experiments.py --dry-run
    python scripts/register_experiments.py --execute
    python scripts/register_experiments.py --execute --reset-defaults
    python scripts/register_experiments.py --execute --json
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import setup_logger

logger = logging.getLogger(__name__)


def main(args: argparse.Namespace) -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.model_governance_repo import (
        DEFAULT_EXPERIMENTS,
        create_experiment,
        get_active_experiments,
    )

    conn = get_local_db()
    init_schema(conn)

    if args.dry_run:
        print("\n[DRY RUN] Would register the following experiments:\n")
        for defn in DEFAULT_EXPERIMENTS:
            print(f"  {defn['experiment_key']:35s}  module={defn['module']}")
        print(f"\n  Total: {len(DEFAULT_EXPERIMENTS)} experiments")
        return

    if args.reset_defaults:
        print("[reset-defaults] Archiving existing experiments before re-registering…")
        try:
            conn.execute("UPDATE experiment_registry SET status = 'archived'")
        except Exception as exc:
            print(f"  [warn] Could not archive: {exc}")

    registered = []
    errors = []
    for defn in DEFAULT_EXPERIMENTS:
        try:
            row = create_experiment(
                conn,
                experiment_key=defn["experiment_key"],
                variant_name=defn["variant_name"],
                module=defn["module"],
                description=defn.get("description", ""),
                status="active",
                min_sample=defn.get("min_sample", 100),
            )
            registered.append(row)
            print(f"  [ok] {defn['experiment_key']}")
        except Exception as exc:
            err = {"key": defn["experiment_key"], "error": str(exc)}
            errors.append(err)
            print(f"  [fail] {defn['experiment_key']}: {exc}")

    active = get_active_experiments(conn)

    if args.json:
        out = {
            "registered": len(registered),
            "errors": len(errors),
            "active_experiments": [
                {"key": e["experiment_key"], "module": e["module"], "status": e["status"]}
                for e in active
            ],
        }
        print(json.dumps(out, indent=2, default=str))
        return

    print(f"\nRegistered: {len(registered)}  Errors: {len(errors)}")
    print(f"\nActive experiments ({len(active)}):")
    for e in active:
        print(f"  [{e['status']:6s}] {e['experiment_key']:35s}  {e['module']}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Register Phase 15 experiment variants")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--execute", action="store_true", help="Write experiments to DB")
    g.add_argument("--dry-run", action="store_true", help="Show what would be registered")
    p.add_argument("--reset-defaults", action="store_true",
                   help="Archive all existing experiments before re-registering defaults")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    main(parse_args())
