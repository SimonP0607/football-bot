"""Phase 12: Market Intelligence Service.

Four sections:
  A. Snapshot ingestion  — fetch odds from API and persist snapshots
  B. Closing line build  — aggregate history into opening/best/closing
  C. CLV computation     — compare pick odds to closing line
  D. Signal detection    — detect steam, drift, reverse line movement

All top-level entry-points (sync_market_odds, build_closing_lines,
compute_pick_clv, detect_market_signals) are ASYNC because they depend on
the API-Football async client. The helper computation routines are SYNC.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Utilities ─────────────────────────────────────────────────────────────────


def _implied(odds: float | None) -> float | None:
    """Convert decimal odds to implied probability."""
    if odds is None or odds <= 1.0:
        return None
    return round(1.0 / odds, 6)


def _movement_label(implied_delta: float | None, min_movement: float) -> str:
    """Classify odds movement from implied probability change."""
    if implied_delta is None:
        return "no_data"
    if abs(implied_delta) < min_movement:
        return "stable"
    if implied_delta > 0:
        return "steam_towards_selection"
    return "drift_against_selection"


def _clv_result(clv_percent: float | None, neutral_band: float) -> str:
    """Classify CLV into positive/neutral/negative."""
    if clv_percent is None:
        return "no_data"
    threshold = neutral_band * 100
    if clv_percent > threshold:
        return "positive"
    if clv_percent < -threshold:
        return "negative"
    return "neutral"


# ── Section A: Snapshot ingestion ─────────────────────────────────────────────


def _parse_odds_response_item(
    item: dict,
    snapshot_type: str,
    snapshot_time: datetime | None = None,
    minutes_to_kickoff: float | None = None,
) -> list[dict]:
    """Flatten one /odds response item into market_odds_history rows."""
    rows: list[dict] = []

    fix_info = item.get("fixture", {})
    provider_fixture_id = fix_info.get("id")
    if not provider_fixture_id:
        return rows

    league_info = item.get("league", {})
    league_id = league_info.get("id")
    season = league_info.get("season")

    snap_time_str = (snapshot_time or datetime.now(timezone.utc)).isoformat()

    for bk in item.get("bookmakers", []):
        bookmaker_id = bk.get("id")
        bookmaker_name = bk.get("name", "")
        for bet in bk.get("bets", []):
            market_key = bet.get("name", "")
            for val in bet.get("values", []):
                selection = val.get("value", "")
                try:
                    odds = float(val.get("odd", 0))
                except (TypeError, ValueError):
                    continue
                if odds <= 1.0:
                    continue
                rows.append({
                    "provider_fixture_id": provider_fixture_id,
                    "fixture_id": None,
                    "league_id": league_id,
                    "season": season,
                    "bookmaker_id": bookmaker_id,
                    "bookmaker_name": bookmaker_name,
                    "market_key": market_key,
                    "selection": selection,
                    "odds": odds,
                    "implied_probability": _implied(odds),
                    "snapshot_type": snapshot_type,
                    "snapshot_time": snap_time_str,
                    "minutes_to_kickoff": minutes_to_kickoff,
                    "source": "api_football",
                    "raw_json": json.dumps({"bet_id": bet.get("id"), "bk_id": bookmaker_id}),
                })
    return rows


async def sync_market_odds(
    conn,
    provider_fixture_ids: list[int],
    snapshot_type: str = "prematch",
    bookmaker_id: int | None = None,
    minutes_to_kickoff: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Fetch current odds for given fixtures and store snapshots.

    Returns {"api_calls": int, "rows_written": int, "fixtures": int}.
    """
    from app.data.api_football.endpoints import fetch_odds_by_fixture
    from app.data.local.market_intelligence_repo import upsert_odds_snapshot

    api_calls = 0
    rows_written = 0
    now = datetime.now(timezone.utc)

    for provider_fixture_id in provider_fixture_ids:
        try:
            items = await fetch_odds_by_fixture(provider_fixture_id, bookmaker=bookmaker_id)
            api_calls += 1
            for item in items:
                # Inject fixture id so _parse sees it
                if "fixture" not in item:
                    item = {"fixture": {"id": provider_fixture_id}, **item}
                parsed = _parse_odds_response_item(
                    item, snapshot_type, now, minutes_to_kickoff
                )
                if not dry_run:
                    rows_written += upsert_odds_snapshot(conn, parsed)
                else:
                    rows_written += len(parsed)
        except Exception as exc:
            logger.warning("sync_market_odds: fixture=%s error — %s", provider_fixture_id, exc)

    logger.info(
        "sync_market_odds: %d fixtures, %d api_calls, %d rows%s",
        len(provider_fixture_ids), api_calls, rows_written, " [dry_run]" if dry_run else "",
    )
    return {"api_calls": api_calls, "rows_written": rows_written, "fixtures": len(provider_fixture_ids)}


async def sync_market_odds_by_date(
    conn,
    league_id: int,
    season: int,
    date: str,
    snapshot_type: str = "prematch",
    bookmaker_id: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Fetch odds for all fixtures of a league on a date and store snapshots."""
    from app.data.api_football.endpoints import fetch_odds_by_league_date
    from app.data.local.market_intelligence_repo import upsert_odds_snapshot

    now = datetime.now(timezone.utc)
    items = await fetch_odds_by_league_date(league_id, season, date, bookmaker=bookmaker_id)

    rows: list[dict] = []
    for item in items:
        rows.extend(_parse_odds_response_item(item, snapshot_type, now, None))

    rows_written = 0
    if not dry_run:
        rows_written = upsert_odds_snapshot(conn, rows)
    else:
        rows_written = len(rows)

    logger.info(
        "sync_market_odds_by_date: league=%s date=%s → %d items → %d rows%s",
        league_id, date, len(items), rows_written, " [dry_run]" if dry_run else "",
    )
    return {"api_calls": 1, "rows_written": rows_written, "fixtures": len(items)}


# ── Section B: Closing line computation ───────────────────────────────────────


def _compute_closing_line_from_history(history: list[dict], min_movement: float) -> dict | None:
    """Compute opening/best/closing metrics from an ordered odds history list."""
    if not history:
        return None

    opening = history[0]
    closing = history[-1]

    opening_odds = opening.get("odds")
    closing_odds = closing.get("odds")
    opening_implied = opening.get("implied_probability") or _implied(opening_odds)
    closing_implied = closing.get("implied_probability") or _implied(closing_odds)

    best_seen = max((r.get("odds", 0.0) for r in history), default=None)

    odds_delta = None
    implied_delta = None
    if opening_odds and closing_odds:
        odds_delta = round(closing_odds - opening_odds, 4)
    if opening_implied is not None and closing_implied is not None:
        implied_delta = round(closing_implied - opening_implied, 6)

    label = _movement_label(implied_delta, min_movement)

    return {
        "provider_fixture_id": opening.get("provider_fixture_id"),
        "fixture_id": opening.get("fixture_id"),
        "league_id": opening.get("league_id"),
        "season": opening.get("season"),
        "bookmaker_id": opening.get("bookmaker_id"),
        "bookmaker_name": opening.get("bookmaker_name"),
        "market_key": opening.get("market_key"),
        "selection": opening.get("selection"),
        "opening_odds": opening_odds,
        "best_seen_odds": best_seen,
        "closing_odds": closing_odds,
        "opening_implied": opening_implied,
        "closing_implied": closing_implied,
        "odds_delta": odds_delta,
        "implied_delta": implied_delta,
        "movement_label": label,
        "confidence_score": min(len(history) / 10.0, 1.0),
    }


def build_closing_lines(
    conn,
    provider_fixture_ids: list[int],
    min_movement: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Compute and persist closing lines from stored snapshot history.

    Returns {"processed": int, "updated": int, "skipped": int}.
    """
    from app.core.config import settings
    from app.data.local.market_intelligence_repo import (
        get_odds_history,
        upsert_closing_line,
    )

    if min_movement is None:
        min_movement = settings.market_intelligence_min_movement

    processed = updated = skipped = 0

    for pfid in provider_fixture_ids:
        history = get_odds_history(conn, pfid, limit=1000)
        if not history:
            skipped += 1
            continue

        # Group by (market_key, selection, bookmaker_id)
        groups: dict[tuple, list[dict]] = {}
        for row in history:
            key = (row["market_key"], row["selection"], row["bookmaker_id"])
            groups.setdefault(key, []).append(row)

        for (market_key, selection, bookmaker_id), group in groups.items():
            group_sorted = sorted(group, key=lambda r: r.get("snapshot_time") or "")
            cl = _compute_closing_line_from_history(group_sorted, min_movement)
            if cl:
                processed += 1
                if not dry_run:
                    if upsert_closing_line(conn, cl):
                        updated += 1
                else:
                    updated += 1

    logger.info(
        "build_closing_lines: %d fixtures → %d computed, %d updated, %d skipped%s",
        len(provider_fixture_ids), processed, updated, skipped,
        " [dry_run]" if dry_run else "",
    )
    return {"processed": processed, "updated": updated, "skipped": skipped}


# ── Section C: CLV computation ────────────────────────────────────────────────


def compute_pick_clv(
    conn,
    pick_rows: list[dict],
    neutral_band: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Compute CLV for a list of picks against stored closing lines.

    Each pick_row must have: pick_candidate_id, provider_fixture_id,
    market_key, selection, pick_odds.

    Optional: published_pick_id, fixture_id, league_id, bookmaker_id.

    Returns {"total": int, "computed": int, "no_closing_line": int}.
    """
    from app.core.config import settings
    from app.data.local.market_intelligence_repo import get_closing_line, upsert_pick_clv

    if neutral_band is None:
        neutral_band = settings.market_intelligence_clv_neutral_band

    total = len(pick_rows)
    computed = 0
    no_closing_line = 0

    for pick in pick_rows:
        pick_candidate_id = pick.get("pick_candidate_id")
        provider_fixture_id = pick.get("provider_fixture_id")
        market_key = pick.get("market_key", "")
        selection = pick.get("selection", "")
        pick_odds = pick.get("pick_odds")
        bookmaker_id = pick.get("bookmaker_id")

        if not pick_odds or pick_odds <= 1.0:
            no_closing_line += 1
            continue

        cl = get_closing_line(conn, provider_fixture_id, market_key, selection, bookmaker_id)
        if not cl or cl.get("closing_odds") is None:
            no_closing_line += 1
            continue

        closing_odds = cl["closing_odds"]
        pick_implied = _implied(pick_odds)
        closing_implied = _implied(closing_odds)

        clv_percent = None
        if pick_implied is not None and closing_implied is not None:
            clv_percent = round((closing_implied - pick_implied) * 100, 4)

        beat = pick_odds > closing_odds
        result = _clv_result(clv_percent, neutral_band)

        clv_row = {
            "pick_candidate_id": pick_candidate_id,
            "published_pick_id": pick.get("published_pick_id"),
            "fixture_id": pick.get("fixture_id"),
            "provider_fixture_id": provider_fixture_id,
            "league_id": pick.get("league_id"),
            "market_key": market_key,
            "selection": selection,
            "pick_odds": pick_odds,
            "closing_odds": closing_odds,
            "pick_implied": pick_implied,
            "closing_implied": closing_implied,
            "clv_percent": clv_percent,
            "clv_result": result,
            "beat_closing_line": beat,
            "bookmaker_name": cl.get("bookmaker_name"),
        }

        computed += 1
        if not dry_run:
            upsert_pick_clv(conn, clv_row)

    logger.info(
        "compute_pick_clv: %d picks, %d computed, %d sin closing line%s",
        total, computed, no_closing_line, " [dry_run]" if dry_run else "",
    )
    return {"total": total, "computed": computed, "no_closing_line": no_closing_line}


# ── Section D: Signal detection ───────────────────────────────────────────────


def _detect_signal_type(
    implied_delta: float | None,
    min_movement: float,
    n_snapshots: int,
) -> str:
    """Classify movement into a signal type."""
    if implied_delta is None or n_snapshots < 2:
        return "stable"
    if abs(implied_delta) < min_movement:
        return "stable"
    speed = abs(implied_delta) / max(n_snapshots, 1)
    if implied_delta > 0:
        if speed > 0.015 or abs(implied_delta) > 0.08:
            return "steam"
        return "sharp_move"
    return "drift"


def detect_market_signals(
    conn,
    provider_fixture_ids: list[int],
    min_movement: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Detect and persist market movement signals for the given fixtures.

    Returns {"processed": int, "signals_created": int, "skipped": int}.
    """
    from app.core.config import settings
    from app.data.local.market_intelligence_repo import (
        get_odds_history,
        upsert_market_signal,
    )

    if min_movement is None:
        min_movement = settings.market_intelligence_min_movement

    processed = signals_created = skipped = 0

    for pfid in provider_fixture_ids:
        history = get_odds_history(conn, pfid, limit=1000)
        if len(history) < 2:
            skipped += 1
            continue

        # Group by (market_key, selection, bookmaker_id)
        groups: dict[tuple, list[dict]] = {}
        for row in history:
            key = (row["market_key"], row["selection"], row["bookmaker_id"])
            groups.setdefault(key, []).append(row)

        for (market_key, selection, bookmaker_id), group in groups.items():
            if len(group) < 2:
                continue

            group_sorted = sorted(group, key=lambda r: r.get("snapshot_time") or "")
            opening = group_sorted[0]
            current = group_sorted[-1]

            open_implied = opening.get("implied_probability") or _implied(opening.get("odds"))
            curr_implied = current.get("implied_probability") or _implied(current.get("odds"))

            if open_implied is None or curr_implied is None:
                continue

            implied_delta = round(curr_implied - open_implied, 6)
            if abs(implied_delta) < min_movement / 2:
                continue

            signal_type = _detect_signal_type(implied_delta, min_movement, len(group))
            label = _movement_label(implied_delta, min_movement)

            if implied_delta > 0:
                reason = f"Cuota bajó de {opening.get('odds'):.2f} a {current.get('odds'):.2f} (+{implied_delta*100:.1f}pp implied)"
                risk = "Mercado movido en contra — vigilar si es steam o ruido."
            else:
                reason = f"Cuota subió de {opening.get('odds'):.2f} a {current.get('odds'):.2f} ({implied_delta*100:.1f}pp implied)"
                risk = "Drift — la selección se ha devaluado en el mercado."

            signal_row = {
                "fixture_id": opening.get("fixture_id"),
                "provider_fixture_id": pfid,
                "league_id": opening.get("league_id"),
                "market_key": market_key,
                "selection": selection,
                "signal_type": signal_type,
                "movement_label": label,
                "opening_odds": opening.get("odds"),
                "current_odds": current.get("odds"),
                "closing_odds": None,
                "implied_delta": implied_delta,
                "confidence_score": min(len(group) / 10.0, 1.0),
                "reason_text": reason,
                "risk_text": risk,
                "status": "observed",
            }

            processed += 1
            if not dry_run:
                if upsert_market_signal(conn, signal_row):
                    signals_created += 1
            else:
                signals_created += 1

    logger.info(
        "detect_market_signals: %d fixtures → %d processed, %d signals, %d skipped%s",
        len(provider_fixture_ids), processed, signals_created, skipped,
        " [dry_run]" if dry_run else "",
    )
    return {"processed": processed, "signals_created": signals_created, "skipped": skipped}


# ── Public read API ───────────────────────────────────────────────────────────


def get_fixture_market_summary(conn, provider_fixture_id: int) -> dict:
    """Return market intelligence summary for a fixture (best-effort)."""
    from app.data.local.market_intelligence_repo import get_market_intelligence_summary
    return get_market_intelligence_summary(conn, provider_fixture_id)


def get_clv_report(conn, days: int = 30, league_id: int | None = None) -> dict:
    """Return CLV performance report."""
    from app.data.local.market_intelligence_repo import get_clv_summary
    return get_clv_summary(conn, days=days, league_id=league_id)
