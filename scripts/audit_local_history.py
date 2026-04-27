#!/usr/bin/env python
"""Audit the local DuckDB history database.

Shows a complete inventory of all data present in the offline stack:
leagues, seasons, fixture counts, training_samples, calibrators,
shadow picks, and eligibility vs. the backtest thresholds.

Read-only — never writes to the database.

Usage:
    python scripts/audit_local_history.py
    python scripts/audit_local_history.py --min-seasons 2 --min-fixtures 20
    python scripts/audit_local_history.py --show-all      # include non-eligible
    python scripts/audit_local_history.py --league 39     # single league only

Exit codes:
    0  OK
    2  import error
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.data.local.duckdb_client import get_local_db
except ImportError as e:
    print(f"Error de importacion: {e}")
    sys.exit(2)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _yn(v: bool) -> str:
    return "SI" if v else "NO"


def _pct(v: float | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else "  --  "


def _league_inventory(conn, min_seasons: int, min_fixtures: int) -> list[dict]:
    """Return per-league stats from fixtures_history."""
    rows = conn.execute(
        """
        SELECT
            fh.provider_league_id,
            MAX(fh.league_name)               AS league_name,
            COUNT(DISTINCT fh.season)         AS n_seasons,
            MIN(fh.season)                    AS min_season,
            MAX(fh.season)                    AS max_season,
            COUNT(*)                          AS total_fixtures,
            COUNT(*) FILTER (
                WHERE fh.goals_home IS NOT NULL
                  AND fh.goals_away IS NOT NULL
            )                                 AS completed_fixtures
        FROM fixtures_history fh
        GROUP BY fh.provider_league_id
        ORDER BY fh.provider_league_id
        """
    ).fetchall()

    # Seasons breakdown per league
    season_rows = conn.execute(
        """
        SELECT provider_league_id, season,
               COUNT(*) FILTER (WHERE goals_home IS NOT NULL AND goals_away IS NOT NULL) AS n_done,
               COUNT(*) AS n_total
        FROM fixtures_history
        GROUP BY provider_league_id, season
        ORDER BY provider_league_id, season
        """
    ).fetchall()
    seasons_by_league: dict[int, list[dict]] = {}
    for lid, season, done, total in season_rows:
        seasons_by_league.setdefault(lid, []).append(
            {"season": season, "done": done, "total": total}
        )

    result = []
    for lid, name, n_seasons, min_s, max_s, total_fix, done_fix in rows:
        slist = seasons_by_league.get(lid, [])
        eligible_seasons = [s for s in slist if s["done"] >= min_fixtures]
        ok_seasons = len(eligible_seasons) >= min_seasons
        eligible = ok_seasons

        reason = ""
        if n_seasons < min_seasons:
            reason = f"solo {n_seasons} temporadas (min={min_seasons})"
        elif not ok_seasons:
            reason = (
                f"solo {len(eligible_seasons)} temporadas con >={min_fixtures} fixtures "
                f"(min={min_seasons})"
            )

        result.append({
            "league_id":        lid,
            "league_name":      name or f"League {lid}",
            "n_seasons":        n_seasons,
            "min_season":       min_s,
            "max_season":       max_s,
            "total_fixtures":   total_fix,
            "done_fixtures":    done_fix,
            "seasons":          slist,
            "eligible":         eligible,
            "reason":           reason,
        })
    return result


# ── Sections ───────────────────────────────────────────────────────────────────


def _print_league_inventory(
    inventory: list[dict], *, show_all: bool, min_seasons: int, min_fixtures: int
) -> None:
    eligible = [l for l in inventory if l["eligible"]]
    ineligible = [l for l in inventory if not l["eligible"]]

    print(f"\n{'=' * 72}")
    print(f"  INVENTARIO DE LIGAS ({len(inventory)} total)  "
          f"elegibles={len(eligible)}  omitidas={len(ineligible)}")
    print(f"  Umbral: min_seasons={min_seasons}  min_fixtures_per_season={min_fixtures}")
    print(f"{'=' * 72}")

    def _print_group(items: list[dict], label: str) -> None:
        if not items:
            return
        print(f"\n  {label}")
        print(f"  {'Liga':>6}  {'Nombre':<32}  {'Seasons':>7}  {'Rango':>11}  {'Fixtures':>8}  {'Elegible'}")
        print(f"  {'-' * 72}")
        for l in items:
            rango = f"{l['min_season']}-{l['max_season']}"
            print(
                f"  {l['league_id']:>6}  {str(l['league_name'])[:32]:<32}  "
                f"{l['n_seasons']:>7}  {rango:>11}  "
                f"{l['done_fixtures']:>8}  "
                f"{_yn(l['eligible'])}"
                + (f"  [{l['reason']}]" if l["reason"] else "")
            )

    _print_group(eligible, "LIGAS ELEGIBLES (suficientes datos para backtest):")
    if show_all:
        _print_group(ineligible, "LIGAS OMITIDAS (insuficientes):")
    else:
        if ineligible:
            print(f"\n  {len(ineligible)} ligas omitidas (usa --show-all para verlas)")


def _print_season_detail(inventory: list[dict], league_id: int | None) -> None:
    items = inventory if league_id is None else [l for l in inventory if l["league_id"] == league_id]
    if not items:
        return
    print(f"\n  DETALLE POR TEMPORADA:")
    for l in items:
        print(f"\n  Liga {l['league_id']} — {l['league_name']}")
        for s in l["seasons"]:
            print(f"    {s['season']}  completados={s['done']:>4}  total={s['total']:>4}")


def _print_training_samples(conn) -> None:
    rows = conn.execute(
        """
        SELECT ts.provider_league_id,
               names.league_name,
               COUNT(*)                       AS n_samples,
               COUNT(DISTINCT ts.season)      AS n_seasons,
               COUNT(DISTINCT ts.backtest_run_id) AS n_runs
        FROM training_samples ts
        LEFT JOIN (
            SELECT provider_league_id, MAX(league_name) AS league_name
            FROM fixtures_history
            GROUP BY provider_league_id
        ) names USING (provider_league_id)
        GROUP BY ts.provider_league_id, names.league_name
        ORDER BY ts.provider_league_id
        """
    ).fetchall()
    if not rows:
        print("\n  training_samples: VACIO (ejecuta run_backtest.py primero)")
        return
    print(f"\n  TRAINING SAMPLES ({sum(r[2] for r in rows)} total)")
    print(f"  {'Liga':>6}  {'Nombre':<30}  {'Samples':>8}  {'Seasons':>7}  {'Runs':>5}")
    print(f"  {'-' * 58}")
    for r in rows:
        print(
            f"  {r[0]:>6}  {str(r[1] or '')[:30]:<30}  {r[2]:>8}  {r[3]:>7}  {r[4]:>5}"
        )


def _print_backtest_runs(conn) -> None:
    rows = conn.execute(
        """
        SELECT id, run_name, status, total_picks,
               CAST(started_at AS VARCHAR) AS started,
               CAST(finished_at AS VARCHAR) AS finished
        FROM backtest_runs
        ORDER BY id
        """
    ).fetchall()
    if not rows:
        print("\n  backtest_runs: VACIO")
        return
    print(f"\n  BACKTEST RUNS ({len(rows)} total)")
    print(f"  {'ID':>4}  {'Nombre':<32}  {'Status':<18}  {'Picks':>6}")
    print(f"  {'-' * 66}")
    for r in rows:
        print(
            f"  {r[0]:>4}  {r[1][:32]:<32}  {r[2]:<18}  "
            f"{str(r[3] or '--'):>6}"
        )


def _print_calibrators(conn) -> None:
    rows = conn.execute(
        """
        SELECT market_key, entity_type,
               COALESCE(CAST(provider_league_id AS VARCHAR), 'global') AS scope,
               n_train, ece_after, val_ece,
               CAST(trained_at AS VARCHAR) AS trained_at
        FROM calibration_registry
        ORDER BY market_key, entity_type, provider_league_id
        """
    ).fetchall()
    if not rows:
        print("\n  calibration_registry: VACIO (ejecuta run_calibration.py primero)")
        return
    print(f"\n  CALIBRADORES ({len(rows)} total)")
    print(f"  {'Mercado':<6}  {'Entity':<13}  {'Scope':>7}  {'N':>5}  {'ECE-train':>10}  {'ECE-val':>8}")
    print(f"  {'-' * 58}")
    for r in rows:
        val_s = f"{r[5]:.4f}" if r[5] is not None else "   --  "
        print(
            f"  {r[0]:<6}  {r[1]:<13}  {r[2]:>7}  {r[3]:>5}  "
            f"{(r[4] or 0):.4f}     {val_s:>8}"
        )


def _print_shadow_picks(conn) -> None:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE decision_status = 'selected')         AS selected,
               COUNT(*) FILTER (WHERE model_correct IS NOT NULL)            AS graded,
               COUNT(*) FILTER (WHERE model_correct = true)                 AS correct
        FROM shadow_value_picks
        """
    ).fetchone()
    total, selected, graded, correct = row
    hit = round(correct / graded, 4) if graded else None
    print(f"\n  SHADOW VALUE PICKS")
    print(f"  Total     : {total}")
    print(f"  Selected  : {selected}")
    print(f"  Gradados  : {graded}  correctos={correct}  hit_rate={_pct(hit)}")


def _print_global_counts(conn) -> None:
    tables = [
        "fixtures_history", "standings_history", "team_stats_history",
        "odds_history", "team_elo_history", "training_samples",
        "backtest_runs", "backtest_metrics", "competition_context",
        "calibration_registry", "market_quality_summary", "shadow_value_picks",
    ]
    print(f"\n  CONTEOS GLOBALES")
    print(f"  {'Tabla':<30}  {'Filas':>8}")
    print(f"  {'-' * 42}")
    for t in tables:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<30}  {n:>8}")
        except Exception:
            print(f"  {t:<30}  {'(ERROR)':>8}")


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(
        description="Audit the local DuckDB history database (read-only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--min-seasons", type=int, default=2, metavar="N",
                   help="Minimo de temporadas con suficientes fixtures (default: 2).")
    p.add_argument("--min-fixtures", type=int, default=20, metavar="N",
                   help="Minimo de fixtures completados por temporada (default: 20).")
    p.add_argument("--show-all", action="store_true",
                   help="Mostrar tambien ligas no elegibles.")
    p.add_argument("--league", type=int, default=None, metavar="ID",
                   help="Mostrar detalle de temporadas solo para esta liga.")
    p.add_argument("--detail", action="store_true",
                   help="Mostrar detalle de temporadas por liga elegible.")
    args = p.parse_args()

    setup_logger()
    logging.getLogger("app").setLevel(logging.WARNING)

    conn = get_local_db()

    inventory = _league_inventory(conn, args.min_seasons, args.min_fixtures)

    if args.league:
        inventory = [l for l in inventory if l["league_id"] == args.league]
        if not inventory:
            print(f"Liga {args.league} no encontrada en fixtures_history.")
            sys.exit(0)

    _print_league_inventory(inventory, show_all=args.show_all,
                            min_seasons=args.min_seasons, min_fixtures=args.min_fixtures)

    if args.detail or args.league:
        _print_season_detail(inventory, args.league)

    _print_training_samples(conn)
    _print_backtest_runs(conn)
    _print_calibrators(conn)
    _print_shadow_picks(conn)
    _print_global_counts(conn)

    print()


if __name__ == "__main__":
    main()
