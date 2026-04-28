#!/usr/bin/env python
"""Sincroniza tracked_competitions desde DEFAULT_LEAGUE_IDS + LEAGUE_SEASONS del .env.

Para cada liga en DEFAULT_LEAGUE_IDS:
  1. Busca competition_season con el season correcto (de LEAGUE_SEASONS).
  2. Si existe: upsert en tracked_competitions (ON CONFLICT competition_season_id UPDATE).
  3. Si no existe: reporta como "necesita Phase B".

Idempotente. Nunca borra filas existentes. No modifica ligas no configuradas en .env.

Usage:
    python scripts/sync_tracked_competitions_from_env.py
    python scripts/sync_tracked_competitions_from_env.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.config import settings
    from app.core.logger import setup_logger
    from app.data.repositories.supabase_client import get_supabase
except ImportError as exc:
    print(f"Error de importación: {exc}")
    sys.exit(2)

SEP = "=" * 70

# ── Tier assignment ───────────────────────────────────────────────────────────
# Static map: (sync_tier, priority) per known provider_league_id.
# Leagues not in this map default to (DEFAULT_TIER, DEFAULT_PRIORITY).

_TIER_MAP: dict[int, tuple[str, int]] = {
    # ── tier_1_daily — sync completo todos los días ──────────────────────────
    1:   ("tier_1_daily", 1),   # FIFA World Cup
    2:   ("tier_1_daily", 1),   # UEFA Champions League
    3:   ("tier_1_daily", 1),   # UEFA Europa League
    39:  ("tier_1_daily", 1),   # Premier League
    40:  ("tier_1_daily", 2),   # Championship
    61:  ("tier_1_daily", 1),   # Ligue 1
    71:  ("tier_1_daily", 1),   # Brasileirao Serie A
    78:  ("tier_1_daily", 1),   # Bundesliga
    88:  ("tier_1_daily", 2),   # Eredivisie
    94:  ("tier_1_daily", 2),   # Primeira Liga
    98:  ("tier_1_daily", 2),   # J1 League
    128: ("tier_1_daily", 2),   # Liga Profesional Argentina
    135: ("tier_1_daily", 1),   # Serie A Italy
    140: ("tier_1_daily", 1),   # La Liga
    144: ("tier_1_daily", 3),   # Jupiler Pro League
    239: ("tier_1_daily", 2),   # Primera A Colombia
    # ── tier_2_matchday — sync completo solo en días de partido ──────────────
    11:  ("tier_2_matchday", 3),  # CONMEBOL Sudamericana
    13:  ("tier_2_matchday", 3),  # CONMEBOL Libertadores
    16:  ("tier_2_matchday", 5),  # CONCACAF Champions League
    17:  ("tier_2_matchday", 5),  # AFC Champions League
    22:  ("tier_2_matchday", 4),  # CONCACAF Gold Cup
    32:  ("tier_2_matchday", 4),  # WC Qualification Europe
    34:  ("tier_2_matchday", 4),  # WC Qualification CONMEBOL
    45:  ("tier_2_matchday", 4),  # FA Cup
    62:  ("tier_2_matchday", 4),  # Ligue 2
    63:  ("tier_2_matchday", 5),  # National France / Coupe Ligue
    66:  ("tier_2_matchday", 5),  # Coupe de France
    72:  ("tier_2_matchday", 4),  # Brasileirao Serie B
    73:  ("tier_2_matchday", 4),  # Copa do Brasil
    79:  ("tier_2_matchday", 4),  # 2. Bundesliga
    80:  ("tier_2_matchday", 5),  # 3. Liga Germany
    81:  ("tier_2_matchday", 5),  # DFB Pokal
    89:  ("tier_2_matchday", 5),  # Eerste Divisie
    90:  ("tier_2_matchday", 5),  # KNVB Cup
    103: ("tier_2_matchday", 5),  # Eliteserien
    104: ("tier_2_matchday", 5),  # 1. divisjon Norway
    106: ("tier_2_matchday", 5),  # Ekstraklasa
    107: ("tier_2_matchday", 5),  # 1. liga Poland
    113: ("tier_2_matchday", 5),  # Allsvenskan
    114: ("tier_2_matchday", 5),  # Superettan
    119: ("tier_2_matchday", 5),  # Superliga Denmark
    120: ("tier_2_matchday", 5),  # 1. Division Denmark
    129: ("tier_2_matchday", 5),  # Torneo Federal Argentina
    130: ("tier_2_matchday", 5),  # Copa Argentina
    136: ("tier_2_matchday", 4),  # Serie B Italy
    137: ("tier_2_matchday", 4),  # Coppa Italia
    141: ("tier_2_matchday", 4),  # Segunda División Spain
    143: ("tier_2_matchday", 4),  # Copa del Rey
    145: ("tier_2_matchday", 4),  # Pro League Belgium 1B
    240: ("tier_2_matchday", 5),  # Torneo BetPlay Colombia
    241: ("tier_2_matchday", 5),  # Copa Colombia
    253: ("tier_2_matchday", 4),  # Liga BetPlay Colombia
    255: ("tier_2_matchday", 5),
    257: ("tier_2_matchday", 5),
    # ── tier_3_light — solo fixtures + odds, sin standings/stats ─────────────
    29:  ("tier_3_light", 7),  # WC Qualification Africa
    30:  ("tier_3_light", 7),  # WC Qualification Asia
    31:  ("tier_3_light", 7),  # WC Qualification CONCACAF
    95:  ("tier_3_light", 6),  # Segunda Liga Portugal
    96:  ("tier_3_light", 7),  # Taca de Portugal
    105: ("tier_3_light", 7),  # NM Cupen Norway
    108: ("tier_3_light", 7),  # Polish Cup
    115: ("tier_3_light", 7),  # Svenska Cupen
    121: ("tier_3_light", 7),  # DBU Pokalen Denmark
    122: ("tier_3_light", 7),  # Danish Cup
    147: ("tier_3_light", 7),  # Belgian Cup
    262: ("tier_3_light", 7),
    263: ("tier_3_light", 7),
    265: ("tier_3_light", 7),
    266: ("tier_3_light", 7),
    268: ("tier_3_light", 7),
    269: ("tier_3_light", 7),
    281: ("tier_3_light", 8),
    282: ("tier_3_light", 8),
    283: ("tier_3_light", 8),
    284: ("tier_3_light", 8),
    286: ("tier_3_light", 8),
    287: ("tier_3_light", 8),
    288: ("tier_3_light", 8),
    289: ("tier_3_light", 8),
    292: ("tier_3_light", 8),
    293: ("tier_3_light", 8),
    294: ("tier_3_light", 8),
    295: ("tier_3_light", 8),
    296: ("tier_3_light", 8),
    297: ("tier_3_light", 8),
    298: ("tier_3_light", 8),
    299: ("tier_3_light", 8),
    300: ("tier_3_light", 8),
}

_DEFAULT_TIER     = "tier_2_matchday"
_DEFAULT_PRIORITY = 5


def _tier_for(league_id: int) -> tuple[str, int]:
    return _TIER_MAP.get(league_id, (_DEFAULT_TIER, _DEFAULT_PRIORITY))


# ── Supabase helpers ──────────────────────────────────────────────────────────


def _find_competition_season_id(
    client,
    provider_league_id: int,
    season: int,
) -> int | None:
    """Return competition_seasons.id for the given league+season, or None."""
    resp = (
        client.table("competition_seasons")
        .select("id, competitions!inner(provider_league_id)")
        .eq("competitions.provider_league_id", provider_league_id)
        .eq("season", season)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if rows:
        return rows[0]["id"]
    return None


def _upsert_tracked(
    client,
    competition_season_id: int,
    sync_tier: str,
    priority: int,
    notes: str,
    dry_run: bool,
) -> str:
    """Upsert a row in tracked_competitions. Returns 'created', 'updated'."""
    # Check if exists
    existing = (
        client.table("tracked_competitions")
        .select("id, is_active, sync_tier")
        .eq("competition_season_id", competition_season_id)
        .limit(1)
        .execute()
    )
    payload = {
        "competition_season_id": competition_season_id,
        "is_active":            True,
        "market_winner":        True,
        "market_btts":          True,
        "market_ou25":          True,
        "market_corners_ou":    False,
        "sync_tier":            sync_tier,
        "priority":             priority,
        "notes":                notes,
    }
    exists = bool(existing.data)
    if dry_run:
        return "updated (dry-run)" if exists else "created (dry-run)"

    (
        client.table("tracked_competitions")
        .upsert(payload, on_conflict="competition_season_id")
        .execute()
    )
    return "updated" if exists else "created"


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(
        description="Sincroniza tracked_competitions desde .env (idempotente).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Muestra qué haría sin escribir nada.")
    args = p.parse_args()

    setup_logger()
    logging.getLogger("app").setLevel(logging.WARNING)

    env_ids  = settings.league_ids_list
    env_seas = settings.league_seasons_map

    print(f"\n{SEP}")
    print(f"  SYNC TRACKED COMPETITIONS FROM ENV"
          + (" [DRY-RUN]" if args.dry_run else ""))
    print(SEP)
    print(f"\n  DEFAULT_LEAGUE_IDS  : {len(env_ids)} ligas")
    print(f"  LEAGUE_SEASONS      : {len(env_seas)} mapeos explícitos")

    if not env_ids:
        print("\n  ERROR: DEFAULT_LEAGUE_IDS vacío en .env.")
        sys.exit(1)

    client = get_supabase()
    n_created  = 0
    n_updated  = 0
    n_skipped  = 0
    n_errors   = 0
    no_phase_b: list[int] = []

    print(f"\n  Procesando {len(env_ids)} ligas...\n")

    for lid in sorted(env_ids):
        season = env_seas.get(lid, settings.default_season)
        if not season:
            print(f"  [{lid:>5}] Sin temporada configurada — omitido "
                  f"(agrega {lid}:<season> a LEAGUE_SEASONS)")
            n_skipped += 1
            continue

        tier, priority = _tier_for(lid)

        # Buscar competition_season_id
        try:
            cs_id = _find_competition_season_id(client, lid, season)
        except Exception as exc:
            print(f"  [{lid:>5}] ERROR consultando BD: {exc}")
            n_errors += 1
            continue

        if cs_id is None:
            no_phase_b.append(lid)
            print(f"  [{lid:>5}] season={season}  SIN competition_season — "
                  f"necesita Phase B")
            n_skipped += 1
            continue

        # Upsert tracked_competitions
        try:
            action = _upsert_tracked(
                client, cs_id, tier, priority,
                notes=f"auto:{lid}",
                dry_run=args.dry_run,
            )
        except Exception as exc:
            print(f"  [{lid:>5}] season={season}  ERROR upsert: {exc}")
            n_errors += 1
            continue

        if "created" in action:
            n_created += 1
            marker = "CREADO <-"
        else:
            n_updated += 1
            marker = "actualizado"

        print(f"  [{lid:>5}] season={season}  tier={tier}  p={priority}  {marker}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'-' * 70}")
    print(f"  RESUMEN")
    print(f"  Creados   : {n_created}")
    print(f"  Actualizados: {n_updated}")
    print(f"  Omitidos  : {n_skipped}  "
          f"({'sin competition_season=' + str(len(no_phase_b)) + ', sin season=' + str(n_skipped - len(no_phase_b))})")
    print(f"  Errores   : {n_errors}")

    if no_phase_b:
        print(f"\n  LIGAS SIN PHASE B ({len(no_phase_b)}) — ejecuta sync_reference.py --phase b:")
        for lid in no_phase_b:
            season = env_seas.get(lid, settings.default_season)
            print(f"    liga={lid} season={season}")

    if args.dry_run:
        print(f"\n  [DRY-RUN] Nada fue escrito. Quita --dry-run para aplicar.")
    elif n_created > 0:
        print(f"\n  OK {n_created} ligas nuevas en tracked_competitions.")
        print(f"  Siguiente paso: python scripts/sync_today.py")

    print(f"\n{SEP}\n")

    sys.exit(0 if n_errors == 0 else 1)


if __name__ == "__main__":
    main()
