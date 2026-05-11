#!/usr/bin/env python
"""Phase 13: Daily edge report — combines picks, settlement, CLV, strategies,
market signals, parlay, and live tracking into a unified summary.

Usage:
    python scripts/report_daily_edge.py --today
    python scripts/report_daily_edge.py --days 7
    python scripts/report_daily_edge.py --days 7 --json
    python scripts/report_daily_edge.py --today --telegram   (sends via alert_service)
"""
import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import setup_logger

logger = logging.getLogger(__name__)


def _pct(v):
    if v is None:
        return "n/a"
    return f"{v * 100:+.1f}%"


def _float2(v):
    if v is None:
        return "n/a"
    return f"{v:.2f}"


def _section(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print('─' * 55)


def _build_report(conn, days: int) -> dict:
    """Build a combined edge report from DuckDB data."""
    report: dict = {"days": days}

    # ── Settlement / picks ────────────────────────────────────────────────────
    try:
        rows = conn.execute(
            """
            SELECT result_status, COUNT(*), SUM(profit), AVG(profit)
            FROM pick_learning_annotations
            WHERE created_at >= current_timestamp - INTERVAL (?) DAY
              AND result_status IS NOT NULL
            GROUP BY result_status
            """,
            [days],
        ).fetchall()
        win_row  = next((r for r in rows if r[0] == "win"),  None)
        loss_row = next((r for r in rows if r[0] == "loss"), None)
        void_row = next((r for r in rows if r[0] == "void"), None)
        wins     = win_row[1]  if win_row  else 0
        losses   = loss_row[1] if loss_row else 0
        voids    = void_row[1] if void_row else 0
        total_profit = 0.0
        if win_row  and win_row[2]:  total_profit += win_row[2]
        if loss_row and loss_row[2]: total_profit += loss_row[2]
        n_decided = wins + losses
        report["settlement"] = {
            "wins": wins, "losses": losses, "voids": voids,
            "hit_rate": round(wins / n_decided, 4) if n_decided else None,
            "total_profit": round(total_profit, 4) if n_decided else None,
            "roi": round(total_profit / n_decided, 4) if n_decided else None,
        }
    except Exception as e:
        report["settlement"] = {"error": str(e)}

    # ── CLV ───────────────────────────────────────────────────────────────────
    try:
        r = conn.execute(
            """
            SELECT COUNT(*), AVG(clv_percent),
                   SUM(CASE WHEN beat_closing_line THEN 1 ELSE 0 END)
            FROM pick_clv_results
            WHERE created_at >= current_timestamp - INTERVAL (?) DAY
            """,
            [days],
        ).fetchone()
        n_clv = r[0] or 0
        avg_clv = r[1]
        beat_n  = r[2] or 0
        report["clv"] = {
            "n_picks":        n_clv,
            "avg_clv_percent": round(avg_clv, 3) if avg_clv is not None else None,
            "beat_rate":      round(beat_n / n_clv, 4) if n_clv else None,
        }
    except Exception as e:
        report["clv"] = {"error": str(e)}

    # ── Strategy learning ─────────────────────────────────────────────────────
    try:
        from app.data.local.strategy_learning_repo import get_best_strategies, get_weak_strategies
        best = get_best_strategies(conn, limit=3)
        weak = get_weak_strategies(conn, limit=3)
        report["strategies"] = {
            "best":  [{"key": p.get("strategy_key"), "score": p.get("strategy_score"),
                       "rec": p.get("recommendation")} for p in best],
            "weak":  [{"key": p.get("strategy_key"), "score": p.get("strategy_score"),
                       "rec": p.get("recommendation")} for p in weak],
        }
    except Exception as e:
        report["strategies"] = {"error": str(e)}

    # ── Bankroll risk ─────────────────────────────────────────────────────────
    try:
        from app.data.local.bankroll_risk_repo import (
            get_latest_portfolio_snapshot, get_bankroll_summary,
        )
        snap    = get_latest_portfolio_snapshot(conn)
        summary = get_bankroll_summary(conn, days=days)
        report["bankroll"] = {
            "total_recommendations": summary.get("total_recommendations", 0),
            "with_stake":            summary.get("with_stake", 0),
            "rejected":              summary.get("rejected", 0),
            "avg_units":             summary.get("avg_recommended_units"),
            "portfolio_score":       snap.get("portfolio_score") if snap else None,
            "risk_level":            snap.get("risk_level") if snap else None,
            "warnings":              (snap.get("warnings_json") or []) if snap else [],
        }
    except Exception as e:
        report["bankroll"] = {"error": str(e)}

    # ── Market signals ────────────────────────────────────────────────────────
    try:
        r = conn.execute(
            """
            SELECT signal_type, COUNT(*)
            FROM market_movement_signals
            WHERE created_at >= current_timestamp - INTERVAL (?) DAY
            GROUP BY signal_type ORDER BY COUNT(*) DESC
            """,
            [days],
        ).fetchall()
        report["market_signals"] = {row[0]: row[1] for row in r}
    except Exception as e:
        report["market_signals"] = {"error": str(e)}

    # ── Parlay ────────────────────────────────────────────────────────────────
    try:
        r = conn.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN status='recommended' THEN 1 ELSE 0 END)
            FROM parlay_candidates
            WHERE created_at >= current_timestamp - INTERVAL (?) DAY
            """,
            [days],
        ).fetchone()
        report["parlay"] = {"total": r[0] or 0, "recommended": r[1] or 0}
    except Exception as e:
        report["parlay"] = {"error": str(e)}

    # ── Live tracking ─────────────────────────────────────────────────────────
    try:
        r = conn.execute(
            """
            SELECT COUNT(DISTINCT provider_fixture_id)
            FROM live_fixture_snapshots
            WHERE updated_at >= current_timestamp - INTERVAL (?) DAY
            """,
            [days],
        ).fetchone()
        report["live"] = {"fixtures_tracked": r[0] or 0}
    except Exception as e:
        report["live"] = {"error": str(e)}

    # ── Model Governance ──────────────────────────────────────────────────────
    try:
        from app.data.local.model_governance_repo import (
            get_activation_recommendations,
            get_governance_summary,
        )
        summary = get_governance_summary(conn)
        recs = get_activation_recommendations(conn)
        report["governance"] = {
            "experiments": summary.get("experiments", {}),
            "audit_7d":    summary.get("audit_7d", {}),
            "recommendations": {
                r["module"]: r["recommendation"] for r in recs
            },
        }
    except Exception as e:
        report["governance"] = {"error": str(e)}

    return report


def _print_report(report: dict) -> None:
    days = report.get("days", 30)
    print(f"\n{'=' * 55}")
    print(f"  Daily Edge Report — last {days} days")
    print(f"{'=' * 55}")

    _section("Settlement")
    s = report.get("settlement") or {}
    if "error" in s:
        print(f"  [warn] {s['error']}")
    else:
        n = (s.get("wins") or 0) + (s.get("losses") or 0)
        print(f"  Picks decided:  {n}  ({s.get('wins')}W / {s.get('losses')}L / {s.get('voids')}V)")
        hr = s.get("hit_rate")
        print(f"  Hit rate:       {hr * 100:.1f}%" if hr else "  Hit rate:       n/a")
        print(f"  Total profit:   {_float2(s.get('total_profit'))} u")
        print(f"  ROI:            {_pct(s.get('roi'))}")

    _section("Closing Line Value")
    c = report.get("clv") or {}
    if "error" in c:
        print(f"  [warn] {c['error']}")
    else:
        print(f"  Picks with CLV: {c.get('n_picks', 0)}")
        print(f"  Avg CLV:        {c.get('avg_clv_percent') or 'n/a'}")
        br = c.get("beat_rate")
        print(f"  Beat rate:      {br * 100:.1f}%" if br else "  Beat rate:      n/a")

    _section("Strategy Learning")
    st = report.get("strategies") or {}
    if "error" in st:
        print(f"  [warn] {st['error']}")
    else:
        best = st.get("best") or []
        weak = st.get("weak") or []
        if best:
            print("  Best strategies:")
            for b in best:
                sc = b.get("score") or 0
                print(f"    [{sc:5.1f}] {b.get('rec', '-'):12s}  {(b.get('key') or '')[:45]}")
        else:
            print("  No strategy profiles yet — run build_strategy_learning.py")
        if weak:
            print("  Weak strategies:")
            for w in weak:
                sc = w.get("score") or 0
                print(f"    [{sc:5.1f}] {w.get('rec', '-'):12s}  {(w.get('key') or '')[:45]}")

    _section("Bankroll Engine")
    br = report.get("bankroll") or {}
    if "error" in br:
        print(f"  [warn] {br['error']}")
    else:
        score = br.get("portfolio_score")
        level = br.get("risk_level") or "n/a"
        print(f"  Portfolio score: {f'{score:.1f}/100' if score else 'n/a'}  ({level.upper()})")
        print(f"  Recomendaciones: {br.get('total_recommendations', 0)}")
        print(f"  Con stake:       {br.get('with_stake', 0)}")
        print(f"  Rechazados:      {br.get('rejected', 0)}")
        avg_u = br.get("avg_units")
        if avg_u:
            print(f"  Avg unidades:    {avg_u:.2f}u")
        warnings = br.get("warnings") or []
        if warnings:
            print(f"  Alertas ({len(warnings)}):")
            for w in warnings[:3]:
                print(f"    - {w}")

    _section("Market Signals")
    ms = report.get("market_signals") or {}
    if "error" in ms:
        print(f"  [warn] {ms['error']}")
    elif ms:
        for sig, cnt in ms.items():
            print(f"  {sig:25s} {cnt:4d}")
    else:
        print("  No market signals in period")

    _section("Parlay")
    pa = report.get("parlay") or {}
    if "error" in pa:
        print(f"  [warn] {pa['error']}")
    else:
        print(f"  Candidates:  {pa.get('total', 0)}")
        print(f"  Recommended: {pa.get('recommended', 0)}")

    _section("Live Tracking")
    lv = report.get("live") or {}
    if "error" in lv:
        print(f"  [warn] {lv['error']}")
    else:
        print(f"  Fixtures tracked: {lv.get('fixtures_tracked', 0)}")

    _section("Model Governance")
    gv = report.get("governance") or {}
    if "error" in gv:
        print(f"  [warn] {gv['error']}")
    else:
        exp_counts = gv.get("experiments") or {}
        active_n = exp_counts.get("active", 0)
        print(f"  Active experiments: {active_n}")
        audit = gv.get("audit_7d") or {}
        print(f"  Audits (7d):        {audit.get('total', 0)}  changed={audit.get('changed', 0)}")
        recs = gv.get("recommendations") or {}
        if recs:
            print("  Module recommendations:")
            for module, rec in recs.items():
                print(f"    {module:20s}  {rec}")
        else:
            print("  No recommendations yet — run run_experiment_lab.py --execute")

    print(f"\n{'=' * 55}\n")


async def _send_telegram(report: dict) -> None:
    """Send a Telegram summary via alert_service if available."""
    try:
        from app.services.alert_service import format_daily_report
        s   = report.get("settlement") or {}
        clv = report.get("clv") or {}
        text_parts = [
            f"<b>Daily Edge Report — {report['days']}d</b>",
            f"Picks: {s.get('wins', 0)}W/{s.get('losses', 0)}L  ROI: {_pct(s.get('roi'))}",
            f"CLV beat: {(clv.get('beat_rate') or 0) * 100:.0f}%  Avg CLV: {clv.get('avg_clv_percent') or 'n/a'}",
        ]
        text = "\n".join(text_parts)
        print(f"\n[Telegram] Would send:\n{text}")
        print("  (No bot connection in CLI mode)")
    except Exception as e:
        print(f"  [warn] Telegram send failed: {e}")


def main(args: argparse.Namespace) -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)

    days = 1 if args.today else args.days
    report = _build_report(conn, days)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return

    _print_report(report)

    if args.telegram:
        import asyncio
        asyncio.run(_send_telegram(report))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily edge report — picks, CLV, strategies, market")
    p.add_argument("--today", action="store_true", help="Report for today only (days=1)")
    p.add_argument("--days", type=int, default=7, help="Days of history (default: 7)")
    p.add_argument("--telegram", action="store_true", help="Send summary to Telegram")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    main(parse_args())
