#!/usr/bin/env python
"""Interactive settlement script — mark pending picks as win / loss / void.

Usage:
    python scripts/settle_picks.py          # settle all pending picks interactively
    python scripts/settle_picks.py --summary  # show ROI summary only (no settlement)

For each pending pick the script shows:
  - Match, market, selection, odd taken, date
  - Prompts: W = win | L = loss | V = void | S = skip | Q = quit

Profit accounting (1-unit stake):
    win  → odd_taken - 1
    loss → -1
    void → 0
"""

import argparse
import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data.repositories import settlement_repo
from app.core.config import settings  # noqa: F401 — ensures .env is loaded


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fetch_fixture_info(fixture_id: int) -> dict:
    """Return basic fixture info (teams, kickoff) for display."""
    from app.data.repositories.supabase_client import get_supabase
    client = get_supabase()
    result = (
        client.table("fixtures")
        .select("id, kickoff_at, home_team_id, away_team_id, league_id, status_short")
        .eq("id", fixture_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def _fetch_team_name(team_id: int) -> str:
    from app.data.repositories.supabase_client import get_supabase
    client = get_supabase()
    result = (
        client.table("teams")
        .select("name")
        .eq("id", team_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]["name"]
    return f"team#{team_id}"


def _market_label(market: str) -> str:
    return {"1X2": "Resultado 1X2", "OU25": "Más/Menos 2.5", "BTTS": "Ambos anotan"}.get(market, market)


def _selection_label(market: str, sel: str) -> str:
    mapping = {
        ("1X2", "Home"): "Local",
        ("1X2", "Draw"): "Empate",
        ("1X2", "Away"): "Visitante",
        ("OU25", "Over 2.5"): "Más de 2.5",
        ("OU25", "Under 2.5"): "Menos de 2.5",
        ("BTTS", "Yes"): "Sí anotan ambos",
        ("BTTS", "No"): "No anotan ambos",
    }
    return mapping.get((market, sel), sel)


def _prompt(row: dict) -> str | None:
    """Show pick info and ask user for result. Returns 'win'/'loss'/'void' or None to skip."""
    fix = _fetch_fixture_info(row["fixture_id"])
    home = _fetch_team_name(fix.get("home_team_id", 0)) if fix else "?"
    away = _fetch_team_name(fix.get("away_team_id", 0)) if fix else "?"
    kickoff = (fix.get("kickoff_at") or "")[:16].replace("T", " ")
    status = fix.get("status_short", "?")

    print()
    print("─" * 60)
    print(f"  {home} vs {away}  [{kickoff}] ({status})")
    print(f"  Mercado:  {_market_label(row['market_key'])}")
    print(f"  Pick:     {_selection_label(row['market_key'], row['selection'])}")
    print(f"  Cuota:    {row['odd_taken']}")
    print(f"  ID pick:  {row['pick_candidate_id']}")
    print()

    while True:
        choice = input("  Resultado  W=Win  L=Loss  V=Void  S=Skip  Q=Quit : ").strip().upper()
        if choice == "W":
            return "win"
        if choice == "L":
            return "loss"
        if choice == "V":
            return "void"
        if choice == "S":
            return None
        if choice == "Q":
            raise KeyboardInterrupt
        print("  Opción inválida — ingresa W, L, V, S o Q.")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Settlement interactivo de picks")
    parser.add_argument("--summary", action="store_true", help="Solo mostrar resumen ROI")
    args = parser.parse_args()

    summary = settlement_repo.get_roi_summary()
    print()
    print("═" * 60)
    print("  RESUMEN ROI")
    print(f"  Ganados:   {summary['wins']}")
    print(f"  Perdidos:  {summary['losses']}")
    print(f"  Anulados:  {summary['voids']}")
    print(f"  Unidades:  {summary['total_profit_units']:+.4f}")
    if summary["settled"]:
        print(f"  ROI:       {summary['roi_pct']:+.2f}%")
    print("═" * 60)

    if args.summary:
        return

    pending = settlement_repo.get_pending()
    if not pending:
        print()
        print("No hay picks pendientes de settlement.")
        return

    print(f"\n  {len(pending)} pick(s) pendiente(s).\n")

    settled_count = 0
    try:
        for row in pending:
            result = _prompt(row)
            if result is None:
                continue
            odd = float(row["odd_taken"])
            if result == "win":
                profit = round(odd - 1, 4)
            elif result == "loss":
                profit = -1.0
            else:
                profit = 0.0
            settlement_repo.settle(row["pick_candidate_id"], result, profit)
            settled_count += 1
            print(f"  ✓ Registrado: {result.upper()} ({profit:+.4f} u)")
    except KeyboardInterrupt:
        print("\n\n  Interrumpido.")

    print(f"\n  {settled_count} pick(s) liquidados.")

    if settled_count:
        updated = settlement_repo.get_roi_summary()
        print()
        print("═" * 60)
        print("  ROI ACTUALIZADO")
        print(f"  Ganados:   {updated['wins']}")
        print(f"  Perdidos:  {updated['losses']}")
        print(f"  Unidades:  {updated['total_profit_units']:+.4f}")
        if updated["settled"]:
            print(f"  ROI:       {updated['roi_pct']:+.2f}%")
        print("═" * 60)


if __name__ == "__main__":
    main()
