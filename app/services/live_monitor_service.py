"""Phase 7: Live Monitoring, Auto-Settlement & Learning Loop.

Tracks pending picks in real time during match windows, computes live pick
states (winning/losing/open), auto-settles finished fixtures, and generates
Telegram notifications for significant state changes.

Entry point: run_live_monitor()
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings

logger = logging.getLogger(__name__)

_FINISHED = {"FT", "AET", "PEN", "WO"}
_IN_PLAY  = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}

NOTIF_STATE_CHANGE = "state_change"
NOTIF_SETTLED      = "settled"
NOTIF_LIVE_ALERT   = "live_alert"


# ── Live pick state ────────────────────────────────────────────────────────────


def compute_live_pick_state(
    market: str,
    selection: str,
    goals_home: int,
    goals_away: int,
    status_short: str,
) -> str:
    """Return 'winning', 'losing', or 'open' for a pick given the current score.

    During the match:
      1X2 / DC — always winning or losing (score determines it).
      OU25      — winning/losing once threshold crossed; else 'open'.
      BTTS      — winning once both scored; losing once BTTS Yes impossible;
                  otherwise 'open'.
    At FT the same logic applies (is_finished=True), and 'open' can occur only
    for edge cases that would settle as void.
    """
    is_done = status_short in _FINISHED
    total   = (goals_home or 0) + (goals_away or 0)
    gh      = goals_home or 0
    ga      = goals_away or 0

    if market == "1X2":
        if selection == "Home":
            if gh > ga:  return "winning"
            if gh < ga:  return "losing"
            return "open"
        if selection == "Draw":
            if gh == ga: return "winning" if is_done else "open"
            return "losing"
        if selection == "Away":
            if ga > gh:  return "winning"
            if ga < gh:  return "losing"
            return "open"

    elif market == "DC":
        if selection in ("Home/Draw", "1X"):
            return "winning" if gh >= ga else "losing"
        if selection in ("Away/Draw", "X2"):
            return "winning" if ga >= gh else "losing"
        if selection in ("Home/Away", "12"):
            if gh != ga: return "winning"
            return "losing" if is_done else "open"

    elif market == "OU25":
        if "Over" in selection:
            if total >= 3: return "winning"
            return "losing" if is_done else "open"
        if "Under" in selection:
            if total >= 3: return "losing"
            return "winning" if is_done else "open"

    elif market == "BTTS":
        both = gh > 0 and ga > 0
        if selection == "Yes":
            if both: return "winning"
            return "losing" if is_done else "open"
        if selection == "No":
            if both: return "losing"
            return "winning" if is_done else "open"

    return "open"


def live_state_to_outcome(live_state: str) -> str | None:
    """Map a finished live_state to a settlement outcome string."""
    if live_state == "winning": return "win"
    if live_state == "losing":  return "loss"
    return None  # 'open' at FT → treat as void (rare edge case)


def _profit(outcome: str, odd: float) -> float:
    if outcome == "win":  return round(float(odd) - 1.0, 4)
    if outcome == "loss": return -1.0
    return 0.0


# ── Notification helpers ───────────────────────────────────────────────────────


def _state_icon(state: str) -> str:
    return {"winning": "✅", "losing": "❌", "open": "⬜"}.get(state, "❓")


def _build_notification_text(
    *,
    fix: dict,
    pick: dict,
    live_state: str,
    goals_home: int,
    goals_away: int,
    elapsed: int | None,
    notification_type: str,
    home_name: str = "Local",
    away_name: str = "Visitante",
) -> str:
    score = f"{goals_home}-{goals_away}"
    elapsed_str = f"{elapsed}'" if elapsed is not None else "?"
    market = pick.get("market_key", "?")
    sel    = pick.get("selection", "?")
    icon   = _state_icon(live_state)

    if notification_type == NOTIF_SETTLED:
        outcome = live_state_to_outcome(live_state)
        odd = float(pick.get("odd_taken") or 0)
        profit = _profit(outcome or "void", odd)
        outcome_label = {"win": "GANADO ✅", "loss": "PERDIDO ❌", "void": "ANULADO 🔘"}.get(outcome or "void", "?")
        return (
            f"<b>Liquidado — {home_name} vs {away_name}</b>\n"
            f"{market}/{sel}  {icon} <b>{outcome_label}</b>\n"
            f"Resultado final: {score}  ·  Cuota: {odd:.2f}  ·  Profit: {profit:+.4f}u"
        )

    state_label = {"winning": "GANANDO", "losing": "PERDIENDO", "open": "ABIERTO"}.get(live_state, live_state)
    return (
        f"<b>{icon} {state_label} — {home_name} vs {away_name}</b>\n"
        f"Min {elapsed_str}  ·  {score}  ·  {market}/{sel}"
    )


async def _send_telegram_push(text: str) -> bool:
    """Send a message to the configured owner via Bot API (stdlib urllib, no extra deps)."""
    try:
        import json
        import urllib.request
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id":    settings.telegram_allowed_user_id,
            "text":       text,
            "parse_mode": "HTML",
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.warning("Telegram push failed: %s", exc)
        return False


# ── Main service ───────────────────────────────────────────────────────────────


def _get_pending_fixture_ids(hours_before: float, hours_after: float) -> list[dict]:
    """Return pending picks that are within the live monitoring window.

    Returns list of pending pick dicts (from settlement_repo), filtered to fixtures
    whose kickoff is between (now - hours_after) and (now + hours_before).
    """
    from app.data.repositories import settlement_repo

    all_pending = settlement_repo.get_all_pending_with_fixtures()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=hours_after)
    window_end   = now + timedelta(hours=hours_before)

    result: list[dict] = []
    for pick in all_pending:
        fix = pick.get("_fixture", {})
        ko_str = fix.get("kickoff_at")
        if not ko_str:
            continue
        try:
            ko = datetime.fromisoformat(ko_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if window_start <= ko <= window_end:
            result.append(pick)
    return result


def _load_team_names(fix_map: dict[int, dict]) -> dict[int, str]:
    """Bulk-load team names for all team IDs referenced in fix_map."""
    from app.data.repositories.supabase_client import get_supabase

    team_ids: set[int] = set()
    for fix in fix_map.values():
        if fix.get("home_team_id"): team_ids.add(fix["home_team_id"])
        if fix.get("away_team_id"): team_ids.add(fix["away_team_id"])
    if not team_ids:
        return {}
    client = get_supabase()
    rows = (
        client.table("teams").select("id, name").in_("id", list(team_ids)).execute()
    ).data or []
    return {r["id"]: r["name"] for r in rows}


async def run_live_monitor(
    *,
    dry_run: bool = True,
    execute: bool = False,
    settle: bool = True,
    notify: bool = False,
    fixture_filter: int | None = None,
    hours_before: float | None = None,
    hours_after: float | None = None,
    max_requests: int | None = None,
    verbose: bool = False,
) -> dict:
    """Monitor live fixtures, update tracking, auto-settle, generate notifications.

    Args:
        dry_run:        If True (default), read-only — no writes to DuckDB or Supabase.
        execute:        If True, write to DuckDB and Supabase (inverts dry_run default).
        settle:         If True and execute is True, also auto-settle finished picks.
        notify:         If True and execute is True, send Telegram push notifications.
        fixture_filter: If set, only monitor this provider_fixture_id.
        hours_before:   Override LIVE_MONITOR_HOURS_BEFORE setting.
        hours_after:    Override LIVE_MONITOR_HOURS_AFTER setting.
        max_requests:   Override LIVE_MONITOR_MAX_REQUESTS setting.
        verbose:        Extra logging.

    Returns:
        dict with keys: fixtures_checked, fixtures_live, fixtures_finished,
        picks_tracked, picks_settled, picks_winning, picks_losing, picks_open,
        api_calls, notifications, errors.
    """
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local import live_monitor_repo as repo

    _exec    = execute and not dry_run
    _hours_b = hours_before  if hours_before  is not None else settings.live_monitor_hours_before
    _hours_a = hours_after   if hours_after   is not None else settings.live_monitor_hours_after
    _max_req = max_requests  if max_requests  is not None else settings.live_monitor_max_requests

    stats: dict = {
        "fixtures_checked": 0,
        "fixtures_live":    0,
        "fixtures_finished": 0,
        "picks_tracked":    0,
        "picks_settled":    0,
        "picks_winning":    0,
        "picks_losing":     0,
        "picks_open":       0,
        "api_calls":        0,
        "notifications":    [],
        "errors":           [],
    }

    conn = get_local_db()
    init_schema(conn)

    # Load pending picks in window
    pending = _get_pending_fixture_ids(_hours_b, _hours_a)
    if fixture_filter:
        pending = [
            p for p in pending
            if p.get("_fixture", {}).get("provider_fixture_id") == fixture_filter
        ]

    if not pending:
        logger.info("live_monitor: no pending picks in window")
        return stats

    # API import deferred so tests without httpx can still reach the early-return above
    from app.data.api_football.endpoints import fetch_fixture_live_states

    # Group by fixture
    fixture_groups: dict[int, list[dict]] = {}
    fix_map: dict[int, dict] = {}
    for pick in pending:
        fix = pick.get("_fixture", {})
        pfid = fix.get("provider_fixture_id")
        if not pfid:
            continue
        fixture_groups.setdefault(pfid, []).append(pick)
        fix_map[pfid] = fix

    provider_ids = list(fixture_groups.keys())
    if not provider_ids:
        return stats

    # Check budget
    batches_needed = (len(provider_ids) + 19) // 20
    if batches_needed > _max_req:
        provider_ids = provider_ids[: _max_req * 20]
        stats["errors"].append(
            f"Budget cap: monitoring only {len(provider_ids)} fixtures"
        )

    # Fetch live states from API
    logger.info("live_monitor: fetching %d fixtures", len(provider_ids))
    try:
        live_data = await fetch_fixture_live_states(provider_ids)
        stats["api_calls"] += (len(provider_ids) + 19) // 20
    except Exception as exc:
        logger.error("live_monitor: API fetch failed — %s", exc)
        stats["errors"].append(f"API fetch error: {exc}")
        live_data = []

    # Build score map
    score_map: dict[int, dict] = {
        item["provider_fixture_id"]: item
        for item in live_data
        if item.get("provider_fixture_id")
    }

    # Load team names (for notifications)
    team_names: dict[int, str] = {}
    if notify or verbose:
        try:
            team_names = _load_team_names(fix_map)
        except Exception:
            pass

    now = datetime.now(timezone.utc)

    # Process each fixture
    for pfid in provider_ids:
        picks = fixture_groups.get(pfid, [])
        live  = score_map.get(pfid)
        fix   = fix_map.get(pfid, {})

        if not live:
            if verbose:
                logger.info("live_monitor: fixture=%s — no live data", pfid)
            continue

        gh           = live.get("goals_home")  or 0
        ga           = live.get("goals_away")  or 0
        status       = live.get("status_short", "")
        elapsed      = live.get("status_elapsed")
        is_finished  = status in _FINISHED

        stats["fixtures_checked"] += 1
        if status in _IN_PLAY:  stats["fixtures_live"]    += 1
        if is_finished:         stats["fixtures_finished"] += 1

        # Update snapshot
        if _exec:
            repo.upsert_live_snapshot(conn, {
                "provider_fixture_id": pfid,
                "status_short":  status,
                "status_elapsed": elapsed,
                "goals_home":    gh,
                "goals_away":    ga,
                "is_finished":   is_finished,
                "last_seen_at":  now.isoformat(),
            })

        home_name = team_names.get(fix.get("home_team_id"), "Local")
        away_name = team_names.get(fix.get("away_team_id"), "Visitante")

        for pick in picks:
            pick_id = pick.get("pick_candidate_id") or pick.get("id")
            market  = pick.get("market_key", "")
            sel     = pick.get("selection", "")
            odd     = float(pick.get("odd_taken") or 1.0)

            if not pick_id or not market or not sel:
                continue

            live_state = compute_live_pick_state(market, sel, gh, ga, status)
            stats["picks_tracked"] += 1

            if   live_state == "winning": stats["picks_winning"] += 1
            elif live_state == "losing":  stats["picks_losing"]  += 1
            else:                         stats["picks_open"]    += 1

            # Load previous state for change detection
            prev = repo.get_pick_tracking(conn, pick_id) if _exec else None
            prev_state = (prev or {}).get("live_state")

            # Update tracking
            if _exec:
                repo.upsert_pick_tracking(conn, {
                    "pick_candidate_id":   pick_id,
                    "provider_fixture_id": pfid,
                    "market_key":          market,
                    "selection":           sel,
                    "live_state":          live_state,
                    "goals_home":          gh,
                    "goals_away":          ga,
                    "status_elapsed":      elapsed,
                    "status_short":        status,
                })

            # Auto-settle if finished
            if is_finished and settle and _exec:
                outcome = live_state_to_outcome(live_state)
                if outcome:
                    try:
                        from app.data.repositories import settlement_repo
                        settlement_repo.settle(pick_id, outcome, _profit(outcome, odd))
                        stats["picks_settled"] += 1
                        if verbose:
                            logger.info(
                                "live_monitor: settled pick_id=%s → %s (%.4f u)",
                                pick_id, outcome, _profit(outcome, odd),
                            )
                        # Notification for settlement
                        log_id = f"{pick_id}_settled"
                        if not repo.notification_already_sent(conn, log_id):
                            text = _build_notification_text(
                                fix=fix, pick=pick,
                                live_state=live_state,
                                goals_home=gh, goals_away=ga, elapsed=elapsed,
                                notification_type=NOTIF_SETTLED,
                                home_name=home_name, away_name=away_name,
                            )
                            notification = {
                                "type":       NOTIF_SETTLED,
                                "log_id":     log_id,
                                "pick_id":    pick_id,
                                "fixture_id": pfid,
                                "text":       text,
                            }
                            stats["notifications"].append(notification)
                            repo.log_notification(conn, {
                                "log_id":              log_id,
                                "pick_candidate_id":   pick_id,
                                "provider_fixture_id": pfid,
                                "notification_type":   NOTIF_SETTLED,
                                "state":               outcome,
                            })
                            if notify and settings.live_monitor_notify:
                                await _send_telegram_push(text)
                    except Exception as exc:
                        logger.warning("live_monitor: settle failed pick_id=%s — %s", pick_id, exc)
                        stats["errors"].append(f"settle pick_id={pick_id}: {exc}")
                continue  # skip state-change notification for finished picks

            # State-change notification (only during match, meaningful elapsed)
            if (
                _exec
                and notify
                and settings.live_monitor_notify
                and prev_state is not None
                and prev_state != live_state
                and live_state in ("winning", "losing")
                and (elapsed or 0) >= settings.live_monitor_min_elapsed_notify
            ):
                log_id = f"{pick_id}_state_{live_state}"
                if not repo.notification_already_sent(conn, log_id):
                    text = _build_notification_text(
                        fix=fix, pick=pick,
                        live_state=live_state,
                        goals_home=gh, goals_away=ga, elapsed=elapsed,
                        notification_type=NOTIF_STATE_CHANGE,
                        home_name=home_name, away_name=away_name,
                    )
                    notification = {
                        "type":       NOTIF_STATE_CHANGE,
                        "log_id":     log_id,
                        "pick_id":    pick_id,
                        "fixture_id": pfid,
                        "text":       text,
                    }
                    stats["notifications"].append(notification)
                    repo.log_notification(conn, {
                        "log_id":              log_id,
                        "pick_candidate_id":   pick_id,
                        "provider_fixture_id": pfid,
                        "notification_type":   NOTIF_STATE_CHANGE,
                        "state":               live_state,
                    })
                    await _send_telegram_push(text)

    return stats


# ── Read-only helpers for bot handlers ────────────────────────────────────────


def get_live_summary(conn) -> list[dict]:
    """Return all tracked fixtures with their picks for /live command.

    Returns list of fixture dicts, each with a 'picks' key.
    """
    from app.data.local import live_monitor_repo as repo

    snapshots = repo.get_all_live_snapshots(conn)
    result: list[dict] = []
    for snap in snapshots:
        pfid  = snap["provider_fixture_id"]
        picks = repo.get_pick_tracking_for_fixture(conn, pfid)
        result.append({**snap, "picks": picks})
    return result


def get_fixture_live_state(conn, prov_fid: int) -> dict | None:
    """Return snapshot + picks for a single fixture. Used by /partido live section."""
    from app.data.local import live_monitor_repo as repo

    snap = repo.get_live_snapshot(conn, prov_fid)
    if not snap:
        return None
    picks = repo.get_pick_tracking_for_fixture(conn, prov_fid)
    return {**snap, "picks": picks}
