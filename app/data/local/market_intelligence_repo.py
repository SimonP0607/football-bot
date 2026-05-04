"""Phase 12: Market Intelligence DuckDB repository.

All functions are synchronous. Upserts use ON CONFLICT DO UPDATE with explicit
conflict targets to respect the UNIQUE constraints defined in 012_market_intelligence_schema.sql.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Snapshot ingestion ────────────────────────────────────────────────────────


def upsert_odds_snapshot(conn, rows: list[dict]) -> int:
    """Insert odds snapshot rows into market_odds_history.

    Each row must have: provider_fixture_id, market_key, selection, odds.
    Optional: fixture_id, league_id, season, bookmaker_id, bookmaker_name,
              implied_probability, snapshot_type, snapshot_time,
              minutes_to_kickoff, source, raw_json.

    Returns number of rows inserted.
    """
    if not rows:
        return 0

    inserted = 0
    for row in rows:
        try:
            conn.execute(
                """
                INSERT INTO market_odds_history (
                    id, created_at, provider_fixture_id, fixture_id, league_id,
                    season, bookmaker_id, bookmaker_name, market_key, selection,
                    odds, implied_probability, snapshot_type, snapshot_time,
                    minutes_to_kickoff, source, raw_json
                ) VALUES (
                    nextval('market_odds_history_seq'),
                    current_timestamp,
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    row.get("provider_fixture_id"),
                    row.get("fixture_id"),
                    row.get("league_id"),
                    row.get("season"),
                    row.get("bookmaker_id"),
                    row.get("bookmaker_name"),
                    row.get("market_key"),
                    row.get("selection"),
                    row.get("odds"),
                    row.get("implied_probability"),
                    row.get("snapshot_type", "prematch"),
                    row.get("snapshot_time"),
                    row.get("minutes_to_kickoff"),
                    row.get("source", "api_football"),
                    row.get("raw_json"),
                ],
            )
            inserted += 1
        except Exception as exc:
            logger.warning("upsert_odds_snapshot: error en fila %s — %s", row.get("provider_fixture_id"), exc)

    logger.debug("upsert_odds_snapshot: %d/%d filas insertadas", inserted, len(rows))
    return inserted


def get_odds_history(
    conn,
    provider_fixture_id: int,
    market_key: str | None = None,
    selection: str | None = None,
    bookmaker_id: int | None = None,
    snapshot_type: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Return odds snapshot history for a fixture."""
    sql = """
        SELECT id, created_at, provider_fixture_id, fixture_id, league_id,
               season, bookmaker_id, bookmaker_name, market_key, selection,
               odds, implied_probability, snapshot_type, snapshot_time,
               minutes_to_kickoff, source
        FROM market_odds_history
        WHERE provider_fixture_id = ?
    """
    params: list[Any] = [provider_fixture_id]

    if market_key:
        sql += " AND market_key = ?"
        params.append(market_key)
    if selection:
        sql += " AND selection = ?"
        params.append(selection)
    if bookmaker_id:
        sql += " AND bookmaker_id = ?"
        params.append(bookmaker_id)
    if snapshot_type:
        sql += " AND snapshot_type = ?"
        params.append(snapshot_type)

    sql += " ORDER BY snapshot_time ASC NULLS LAST LIMIT ?"
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
        cols = [
            "id", "created_at", "provider_fixture_id", "fixture_id", "league_id",
            "season", "bookmaker_id", "bookmaker_name", "market_key", "selection",
            "odds", "implied_probability", "snapshot_type", "snapshot_time",
            "minutes_to_kickoff", "source",
        ]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.error("get_odds_history: %s", exc)
        return []


def get_opening_odds(
    conn,
    provider_fixture_id: int,
    market_key: str,
    selection: str,
    bookmaker_id: int | None = None,
) -> dict | None:
    """Return the earliest odds snapshot (opening line) for a market/selection."""
    sql = """
        SELECT odds, implied_probability, snapshot_time, bookmaker_id, bookmaker_name
        FROM market_odds_history
        WHERE provider_fixture_id = ?
          AND market_key = ?
          AND selection = ?
          AND snapshot_type IN ('opening', 'prematch')
    """
    params: list[Any] = [provider_fixture_id, market_key, selection]

    if bookmaker_id:
        sql += " AND bookmaker_id = ?"
        params.append(bookmaker_id)

    sql += " ORDER BY snapshot_time ASC NULLS LAST LIMIT 1"

    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        return {
            "odds": row[0],
            "implied_probability": row[1],
            "snapshot_time": row[2],
            "bookmaker_id": row[3],
            "bookmaker_name": row[4],
        }
    except Exception as exc:
        logger.error("get_opening_odds: %s", exc)
        return None


# ── Closing lines ─────────────────────────────────────────────────────────────


def upsert_closing_line(conn, row: dict) -> bool:
    """Upsert a closing line record into market_closing_lines.

    Required keys: provider_fixture_id, market_key, selection, bookmaker_id.
    Updates all fields on conflict.
    """
    try:
        conn.execute(
            """
            INSERT INTO market_closing_lines (
                id, created_at, updated_at,
                provider_fixture_id, fixture_id, league_id, season,
                bookmaker_id, bookmaker_name,
                market_key, selection,
                opening_odds, best_seen_odds, closing_odds,
                opening_implied, closing_implied,
                odds_delta, implied_delta,
                movement_label, confidence_score, raw_json
            ) VALUES (
                nextval('market_closing_lines_seq'),
                now(), now(),
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (provider_fixture_id, market_key, selection, bookmaker_id)
            DO UPDATE SET
                updated_at      = now(),
                fixture_id      = EXCLUDED.fixture_id,
                league_id       = EXCLUDED.league_id,
                season          = EXCLUDED.season,
                bookmaker_name  = EXCLUDED.bookmaker_name,
                opening_odds    = COALESCE(market_closing_lines.opening_odds, EXCLUDED.opening_odds),
                best_seen_odds  = CASE
                                    WHEN EXCLUDED.best_seen_odds IS NOT NULL AND (
                                        market_closing_lines.best_seen_odds IS NULL OR
                                        EXCLUDED.best_seen_odds > market_closing_lines.best_seen_odds
                                    ) THEN EXCLUDED.best_seen_odds
                                    ELSE market_closing_lines.best_seen_odds
                                  END,
                closing_odds    = COALESCE(EXCLUDED.closing_odds, market_closing_lines.closing_odds),
                opening_implied = COALESCE(market_closing_lines.opening_implied, EXCLUDED.opening_implied),
                closing_implied = COALESCE(EXCLUDED.closing_implied, market_closing_lines.closing_implied),
                odds_delta      = COALESCE(EXCLUDED.odds_delta, market_closing_lines.odds_delta),
                implied_delta   = COALESCE(EXCLUDED.implied_delta, market_closing_lines.implied_delta),
                movement_label  = COALESCE(EXCLUDED.movement_label, market_closing_lines.movement_label),
                confidence_score = COALESCE(EXCLUDED.confidence_score, market_closing_lines.confidence_score),
                raw_json        = COALESCE(EXCLUDED.raw_json, market_closing_lines.raw_json)
            """,
            [
                row.get("provider_fixture_id"),
                row.get("fixture_id"),
                row.get("league_id"),
                row.get("season"),
                row.get("bookmaker_id"),
                row.get("bookmaker_name"),
                row.get("market_key"),
                row.get("selection"),
                row.get("opening_odds"),
                row.get("best_seen_odds"),
                row.get("closing_odds"),
                row.get("opening_implied"),
                row.get("closing_implied"),
                row.get("odds_delta"),
                row.get("implied_delta"),
                row.get("movement_label", "no_data"),
                row.get("confidence_score"),
                row.get("raw_json"),
            ],
        )
        return True
    except Exception as exc:
        logger.error("upsert_closing_line: %s", exc)
        return False


def get_closing_line(
    conn,
    provider_fixture_id: int,
    market_key: str,
    selection: str,
    bookmaker_id: int | None = None,
) -> dict | None:
    """Return the closing line for a fixture/market/selection."""
    sql = """
        SELECT provider_fixture_id, fixture_id, league_id, season,
               bookmaker_id, bookmaker_name, market_key, selection,
               opening_odds, best_seen_odds, closing_odds,
               opening_implied, closing_implied,
               odds_delta, implied_delta, movement_label, confidence_score
        FROM market_closing_lines
        WHERE provider_fixture_id = ?
          AND market_key = ?
          AND selection = ?
    """
    params: list[Any] = [provider_fixture_id, market_key, selection]

    if bookmaker_id:
        sql += " AND bookmaker_id = ?"
        params.append(bookmaker_id)
    else:
        sql += " ORDER BY bookmaker_id LIMIT 1"

    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        cols = [
            "provider_fixture_id", "fixture_id", "league_id", "season",
            "bookmaker_id", "bookmaker_name", "market_key", "selection",
            "opening_odds", "best_seen_odds", "closing_odds",
            "opening_implied", "closing_implied",
            "odds_delta", "implied_delta", "movement_label", "confidence_score",
        ]
        return dict(zip(cols, row))
    except Exception as exc:
        logger.error("get_closing_line: %s", exc)
        return None


def get_closing_lines_for_fixture(conn, provider_fixture_id: int) -> list[dict]:
    """Return all closing lines stored for a fixture."""
    sql = """
        SELECT provider_fixture_id, fixture_id, league_id, season,
               bookmaker_id, bookmaker_name, market_key, selection,
               opening_odds, best_seen_odds, closing_odds,
               opening_implied, closing_implied,
               odds_delta, implied_delta, movement_label, confidence_score
        FROM market_closing_lines
        WHERE provider_fixture_id = ?
        ORDER BY market_key, selection, bookmaker_id
    """
    try:
        rows = conn.execute(sql, [provider_fixture_id]).fetchall()
        cols = [
            "provider_fixture_id", "fixture_id", "league_id", "season",
            "bookmaker_id", "bookmaker_name", "market_key", "selection",
            "opening_odds", "best_seen_odds", "closing_odds",
            "opening_implied", "closing_implied",
            "odds_delta", "implied_delta", "movement_label", "confidence_score",
        ]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.error("get_closing_lines_for_fixture: %s", exc)
        return []


# ── CLV results ───────────────────────────────────────────────────────────────


def upsert_pick_clv(conn, row: dict) -> bool:
    """Upsert a CLV result into pick_clv_results.

    Required: pick_candidate_id, market_key, selection.
    """
    try:
        conn.execute(
            """
            INSERT INTO pick_clv_results (
                id, created_at,
                pick_candidate_id, published_pick_id,
                fixture_id, provider_fixture_id, league_id,
                market_key, selection,
                pick_odds, closing_odds, pick_implied, closing_implied,
                clv_percent, clv_result, beat_closing_line,
                bookmaker_name, metadata_json
            ) VALUES (
                nextval('pick_clv_results_seq'),
                current_timestamp,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (pick_candidate_id, market_key, selection)
            DO UPDATE SET
                published_pick_id = COALESCE(EXCLUDED.published_pick_id, pick_clv_results.published_pick_id),
                fixture_id        = COALESCE(EXCLUDED.fixture_id, pick_clv_results.fixture_id),
                provider_fixture_id = COALESCE(EXCLUDED.provider_fixture_id, pick_clv_results.provider_fixture_id),
                league_id         = COALESCE(EXCLUDED.league_id, pick_clv_results.league_id),
                pick_odds         = COALESCE(EXCLUDED.pick_odds, pick_clv_results.pick_odds),
                closing_odds      = COALESCE(EXCLUDED.closing_odds, pick_clv_results.closing_odds),
                pick_implied      = COALESCE(EXCLUDED.pick_implied, pick_clv_results.pick_implied),
                closing_implied   = COALESCE(EXCLUDED.closing_implied, pick_clv_results.closing_implied),
                clv_percent       = COALESCE(EXCLUDED.clv_percent, pick_clv_results.clv_percent),
                clv_result        = COALESCE(EXCLUDED.clv_result, pick_clv_results.clv_result),
                beat_closing_line = COALESCE(EXCLUDED.beat_closing_line, pick_clv_results.beat_closing_line),
                bookmaker_name    = COALESCE(EXCLUDED.bookmaker_name, pick_clv_results.bookmaker_name),
                metadata_json     = COALESCE(EXCLUDED.metadata_json, pick_clv_results.metadata_json)
            """,
            [
                row.get("pick_candidate_id"),
                row.get("published_pick_id"),
                row.get("fixture_id"),
                row.get("provider_fixture_id"),
                row.get("league_id"),
                row.get("market_key"),
                row.get("selection"),
                row.get("pick_odds"),
                row.get("closing_odds"),
                row.get("pick_implied"),
                row.get("closing_implied"),
                row.get("clv_percent"),
                row.get("clv_result", "no_data"),
                row.get("beat_closing_line", False),
                row.get("bookmaker_name"),
                row.get("metadata_json"),
            ],
        )
        return True
    except Exception as exc:
        logger.error("upsert_pick_clv: %s", exc)
        return False


def get_pick_clv(
    conn,
    pick_candidate_id: int,
    market_key: str | None = None,
    selection: str | None = None,
) -> list[dict]:
    """Return CLV records for a pick candidate."""
    sql = """
        SELECT pick_candidate_id, published_pick_id, fixture_id,
               provider_fixture_id, league_id, market_key, selection,
               pick_odds, closing_odds, pick_implied, closing_implied,
               clv_percent, clv_result, beat_closing_line, bookmaker_name, created_at
        FROM pick_clv_results
        WHERE pick_candidate_id = ?
    """
    params: list[Any] = [pick_candidate_id]

    if market_key:
        sql += " AND market_key = ?"
        params.append(market_key)
    if selection:
        sql += " AND selection = ?"
        params.append(selection)

    try:
        rows = conn.execute(sql, params).fetchall()
        cols = [
            "pick_candidate_id", "published_pick_id", "fixture_id",
            "provider_fixture_id", "league_id", "market_key", "selection",
            "pick_odds", "closing_odds", "pick_implied", "closing_implied",
            "clv_percent", "clv_result", "beat_closing_line", "bookmaker_name", "created_at",
        ]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.error("get_pick_clv: %s", exc)
        return []


def get_clv_summary(
    conn,
    days: int = 30,
    league_id: int | None = None,
    market_key: str | None = None,
) -> dict:
    """Return aggregate CLV metrics over the last N days."""
    sql = """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN beat_closing_line THEN 1 ELSE 0 END) AS beat_count,
            AVG(clv_percent) AS avg_clv,
            SUM(CASE WHEN clv_result = 'positive' THEN 1 ELSE 0 END) AS positive_count,
            SUM(CASE WHEN clv_result = 'neutral'  THEN 1 ELSE 0 END) AS neutral_count,
            SUM(CASE WHEN clv_result = 'negative' THEN 1 ELSE 0 END) AS negative_count,
            SUM(CASE WHEN clv_result = 'no_data'  THEN 1 ELSE 0 END) AS no_data_count
        FROM pick_clv_results
        WHERE created_at >= current_timestamp - INTERVAL (?) DAY
    """
    params: list[Any] = [days]

    if league_id:
        sql += " AND league_id = ?"
        params.append(league_id)
    if market_key:
        sql += " AND market_key = ?"
        params.append(market_key)

    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return {}
        total = row[0] or 0
        beat = row[1] or 0
        return {
            "total": total,
            "beat_count": beat,
            "beat_rate": round(beat / total, 4) if total else 0.0,
            "avg_clv": round(row[2], 4) if row[2] is not None else None,
            "positive_count": row[3] or 0,
            "neutral_count": row[4] or 0,
            "negative_count": row[5] or 0,
            "no_data_count": row[6] or 0,
            "days": days,
        }
    except Exception as exc:
        logger.error("get_clv_summary: %s", exc)
        return {}


# ── Market movement signals ───────────────────────────────────────────────────


def upsert_market_signal(conn, row: dict) -> bool:
    """Upsert a market movement signal.

    Required: provider_fixture_id, market_key, selection, signal_type.
    """
    try:
        conn.execute(
            """
            INSERT INTO market_movement_signals (
                id, created_at,
                fixture_id, provider_fixture_id, league_id,
                market_key, selection,
                signal_type, movement_label,
                opening_odds, current_odds, closing_odds,
                implied_delta, confidence_score,
                reason_text, risk_text, status, metadata_json
            ) VALUES (
                nextval('market_movement_signals_seq'),
                current_timestamp,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (provider_fixture_id, market_key, selection, signal_type)
            DO UPDATE SET
                fixture_id       = COALESCE(EXCLUDED.fixture_id, market_movement_signals.fixture_id),
                league_id        = COALESCE(EXCLUDED.league_id, market_movement_signals.league_id),
                movement_label   = COALESCE(EXCLUDED.movement_label, market_movement_signals.movement_label),
                opening_odds     = COALESCE(EXCLUDED.opening_odds, market_movement_signals.opening_odds),
                current_odds     = COALESCE(EXCLUDED.current_odds, market_movement_signals.current_odds),
                closing_odds     = COALESCE(EXCLUDED.closing_odds, market_movement_signals.closing_odds),
                implied_delta    = COALESCE(EXCLUDED.implied_delta, market_movement_signals.implied_delta),
                confidence_score = COALESCE(EXCLUDED.confidence_score, market_movement_signals.confidence_score),
                reason_text      = COALESCE(EXCLUDED.reason_text, market_movement_signals.reason_text),
                risk_text        = COALESCE(EXCLUDED.risk_text, market_movement_signals.risk_text),
                status           = COALESCE(EXCLUDED.status, market_movement_signals.status),
                metadata_json    = COALESCE(EXCLUDED.metadata_json, market_movement_signals.metadata_json)
            """,
            [
                row.get("fixture_id"),
                row.get("provider_fixture_id"),
                row.get("league_id"),
                row.get("market_key"),
                row.get("selection"),
                row.get("signal_type"),
                row.get("movement_label"),
                row.get("opening_odds"),
                row.get("current_odds"),
                row.get("closing_odds"),
                row.get("implied_delta"),
                row.get("confidence_score"),
                row.get("reason_text"),
                row.get("risk_text"),
                row.get("status", "observed"),
                row.get("metadata_json"),
            ],
        )
        return True
    except Exception as exc:
        logger.error("upsert_market_signal: %s", exc)
        return False


def get_market_signals(
    conn,
    provider_fixture_id: int | None = None,
    fixture_id: int | None = None,
    signal_type: str | None = None,
    market_key: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return market movement signals, filtered by fixture and/or type."""
    sql = """
        SELECT fixture_id, provider_fixture_id, league_id,
               market_key, selection, signal_type, movement_label,
               opening_odds, current_odds, closing_odds,
               implied_delta, confidence_score,
               reason_text, risk_text, status, created_at
        FROM market_movement_signals
        WHERE 1=1
    """
    params: list[Any] = []

    if provider_fixture_id:
        sql += " AND provider_fixture_id = ?"
        params.append(provider_fixture_id)
    if fixture_id:
        sql += " AND fixture_id = ?"
        params.append(fixture_id)
    if signal_type:
        sql += " AND signal_type = ?"
        params.append(signal_type)
    if market_key:
        sql += " AND market_key = ?"
        params.append(market_key)

    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
        cols = [
            "fixture_id", "provider_fixture_id", "league_id",
            "market_key", "selection", "signal_type", "movement_label",
            "opening_odds", "current_odds", "closing_odds",
            "implied_delta", "confidence_score",
            "reason_text", "risk_text", "status", "created_at",
        ]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.error("get_market_signals: %s", exc)
        return []


# ── Aggregated views ──────────────────────────────────────────────────────────


def get_market_intelligence_summary(conn, provider_fixture_id: int) -> dict:
    """Return aggregated market intelligence for a fixture (for /partido enrichment).

    Combines closing lines, movement signals, and CLV data into a single dict.
    """
    closing_lines = get_closing_lines_for_fixture(conn, provider_fixture_id)
    signals = get_market_signals(conn, provider_fixture_id=provider_fixture_id)

    if not closing_lines and not signals:
        return {}

    # Summarise movement labels and signal types
    movement_labels: dict[str, int] = {}
    for cl in closing_lines:
        label = cl.get("movement_label", "no_data")
        movement_labels[label] = movement_labels.get(label, 0) + 1

    signal_types: dict[str, int] = {}
    for sig in signals:
        stype = sig.get("signal_type", "unknown")
        signal_types[stype] = signal_types.get(stype, 0) + 1

    # Best closing line per market_key (first bookmaker found)
    best_by_market: dict[str, dict] = {}
    for cl in closing_lines:
        mk = cl.get("market_key", "")
        sel = cl.get("selection", "")
        key = f"{mk}:{sel}"
        if key not in best_by_market:
            best_by_market[key] = cl

    return {
        "provider_fixture_id": provider_fixture_id,
        "closing_lines": list(best_by_market.values()),
        "movement_labels": movement_labels,
        "signals": signals[:10],
        "signal_types": signal_types,
        "has_steam": "steam" in signal_types,
        "has_reverse_line": "reverse_line" in signal_types,
        "has_sharp_move": "sharp_move" in signal_types,
    }


def get_market_stats(conn) -> dict:
    """Return table counts and data coverage stats for audit."""
    stats: dict[str, Any] = {}
    try:
        stats["odds_history_count"] = conn.execute(
            "SELECT COUNT(*) FROM market_odds_history"
        ).fetchone()[0]
    except Exception:
        stats["odds_history_count"] = 0

    try:
        stats["closing_lines_count"] = conn.execute(
            "SELECT COUNT(*) FROM market_closing_lines"
        ).fetchone()[0]
    except Exception:
        stats["closing_lines_count"] = 0

    try:
        stats["pick_clv_count"] = conn.execute(
            "SELECT COUNT(*) FROM pick_clv_results"
        ).fetchone()[0]
    except Exception:
        stats["pick_clv_count"] = 0

    try:
        stats["signals_count"] = conn.execute(
            "SELECT COUNT(*) FROM market_movement_signals"
        ).fetchone()[0]
    except Exception:
        stats["signals_count"] = 0

    try:
        stats["fixtures_with_odds"] = conn.execute(
            "SELECT COUNT(DISTINCT provider_fixture_id) FROM market_odds_history"
        ).fetchone()[0]
    except Exception:
        stats["fixtures_with_odds"] = 0

    try:
        stats["fixtures_with_closing"] = conn.execute(
            "SELECT COUNT(DISTINCT provider_fixture_id) FROM market_closing_lines"
        ).fetchone()[0]
    except Exception:
        stats["fixtures_with_closing"] = 0

    try:
        row = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN beat_closing_line THEN 1 ELSE 0 END) FROM pick_clv_results"
        ).fetchone()
        total = row[0] or 0
        beat = row[1] or 0
        stats["clv_total"] = total
        stats["clv_beat_rate"] = round(beat / total, 4) if total else 0.0
    except Exception:
        stats["clv_total"] = 0
        stats["clv_beat_rate"] = 0.0

    return stats
