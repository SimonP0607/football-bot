"""Phase 13: CLV Learning Loop & Strategy Scoring service.

Annotates resolved picks with learning labels, groups them into strategy profiles,
and generates actionable recommendations. Does NOT modify pick selection unless
STRATEGY_LEARNING_USE_FOR_SELECTION=true.

Sync/async contract:
    run_strategy_learning()        — SYNC entry point (called from CLI/scheduler)
    annotate_picks()               — SYNC
    compute_strategy_profiles()    — SYNC
    generate_strategy_adjustments()— SYNC
    get_strategy_for_pick()        — SYNC (fast, used by VE live adapter)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MIN_SAMPLE_FOR_RECOMMENDATION = 10
MIN_SAMPLE_FULL_SCORE = 50

# ── Bucket helpers ─────────────────────────────────────────────────────────────


def odds_bucket(odds: float | None) -> str:
    if odds is None:
        return "odds_unknown"
    if odds < 1.50:
        return "odds_lt1.50"
    if odds < 1.90:
        return "odds_1.50_1.90"
    if odds < 2.50:
        return "odds_1.90_2.50"
    if odds < 3.50:
        return "odds_2.50_3.50"
    return "odds_gt3.50"


def edge_bucket(edge: float | None) -> str:
    if edge is None:
        return "edge_unknown"
    pct = edge * 100
    if pct < 0:
        return "edge_neg"
    if pct < 2:
        return "edge_0_2"
    if pct < 5:
        return "edge_2_5"
    if pct < 10:
        return "edge_5_10"
    return "edge_gt10"


def confidence_bucket(confidence: float | None) -> str:
    if confidence is None:
        return "conf_unknown"
    pct = confidence * 100
    if pct < 50:
        return "conf_lt50"
    if pct < 55:
        return "conf_50_55"
    if pct < 60:
        return "conf_55_60"
    if pct < 70:
        return "conf_60_70"
    return "conf_gt70"


def clv_bucket(clv_percent: float | None) -> str:
    if clv_percent is None:
        return "clv_unknown"
    if clv_percent < -3:
        return "clv_negative"
    if clv_percent < 0:
        return "clv_slightly_neg"
    if clv_percent < 2:
        return "clv_neutral"
    if clv_percent < 5:
        return "clv_small_pos"
    return "clv_strong_pos"


def roi_bucket(roi: float | None) -> str:
    if roi is None:
        return "roi_unknown"
    pct = roi * 100
    if pct < -20:
        return "roi_very_neg"
    if pct < 0:
        return "roi_neg"
    if pct < 5:
        return "roi_flat"
    if pct < 15:
        return "roi_good"
    return "roi_excellent"


def sample_bucket(sample_size: int | None) -> str:
    n = sample_size or 0
    if n < 10:
        return "sample_tiny"
    if n < 30:
        return "sample_small"
    if n < 100:
        return "sample_medium"
    return "sample_large"


# ── Strategy key builder ───────────────────────────────────────────────────────


def build_strategy_key(
    market_key: str | None,
    league_id: int | None,
    odds_val: float | None,
    edge_val: float | None,
    confidence_val: float | None,
    clv_percent_val: float | None,
    movement_label: str | None = None,
    availability_level: str | None = None,
    prematch_risk: str | None = None,
) -> str:
    """Build a deterministic strategy key from pick dimensions.

    Example: OU25|league_140|odds_1.90_2.50|edge_2_5|conf_55_60|clv_small_pos
    """
    scope = f"league_{league_id}" if league_id else "global"
    parts = [
        (market_key or "unknown").strip(),
        scope,
        odds_bucket(odds_val),
        edge_bucket(edge_val),
        confidence_bucket(confidence_val),
        clv_bucket(clv_percent_val),
    ]
    if movement_label and movement_label not in ("no_data", "unknown", "stable"):
        parts.append(f"mv_{movement_label}")
    if availability_level and availability_level not in ("unknown", "none", "low"):
        parts.append(f"avail_{availability_level}")
    if prematch_risk and prematch_risk not in ("unknown", "none"):
        parts.append(f"pm_{prematch_risk}")
    return "|".join(parts)


# ── Learning label ─────────────────────────────────────────────────────────────


def _learning_label(
    result_status: str | None,
    profit: float | None,
    clv_percent: float | None,
    beat_closing_line: bool | None,
) -> str:
    """Compute learning_label for a single pick annotation.

    strong_positive: settled + profit > 0 + CLV positive
    positive:        settled with at least one positive signal (profit or CLV)
    neutral:         settled but flat, or no CLV data
    weak:            settled loss but CLV positive (beat line but still lost)
    negative:        settled loss + CLV negative
    no_data:         unsettled or no meaningful data
    """
    if result_status not in ("win", "loss", "void"):
        return "no_data"
    if result_status == "void":
        return "neutral"

    has_profit  = profit is not None and profit > 0
    has_loss    = profit is not None and profit < 0
    clv_pos     = clv_percent is not None and clv_percent > 0.5
    clv_neg     = clv_percent is not None and clv_percent < -0.5
    beat_cl     = bool(beat_closing_line)

    if has_profit and (clv_pos or beat_cl):
        return "strong_positive"
    if has_profit and not clv_neg:
        return "positive"
    if has_loss and clv_pos:
        return "weak"          # beat the line but still lost (variance)
    if has_loss and clv_neg:
        return "negative"
    if has_profit:
        return "positive"      # profit but no CLV data
    if has_loss:
        return "weak"
    return "neutral"


# ── Strategy score ─────────────────────────────────────────────────────────────


def _stability_score(
    sample_size: int,
    roi: float | None,
    clv_beat_rate: float | None,
) -> float:
    """Measure consistency of ROI and CLV signals.

    High stability = both signals agree on direction.
    """
    if sample_size < 5:
        return 0.3
    roi_pos = (roi or 0.0) > 0.0
    clv_pos = (clv_beat_rate or 0.5) > 0.5
    agreement = 1.0 if roi_pos == clv_pos else 0.35
    sample_conf = min(1.0, sample_size / 100.0)
    return round(agreement * (0.5 + 0.5 * sample_conf), 3)


def _compute_strategy_score(
    sample_size: int,
    roi: float | None,
    clv_beat_rate: float | None,
    avg_clv_percent: float | None,
    hit_rate: float | None,
    stability_score_val: float | None,
) -> float:
    """Compute strategy_score on 0-100 scale.

    Weights:
        35% ROI normalized
        25% CLV beat rate
        15% avg CLV percent (normalized)
        10% hit rate
        10% stability score
         5% sample size weight
    """
    if sample_size == 0:
        return 0.0

    # ROI: normalize [-50%, +50%] to [0, 1]
    roi_val  = roi or 0.0
    roi_norm = max(0.0, min(1.0, (roi_val + 0.50) / 1.00))

    # CLV beat rate: already [0, 1]
    clv_beat = max(0.0, min(1.0, clv_beat_rate or 0.5))

    # Avg CLV %: normalize [-5, +5] to [0, 1]
    clv_avg  = avg_clv_percent or 0.0
    clv_norm = max(0.0, min(1.0, (clv_avg + 5.0) / 10.0))

    # Hit rate: [0, 1]
    hit = max(0.0, min(1.0, hit_rate or 0.5))

    # Stability: [0, 1]
    stab = max(0.0, min(1.0, stability_score_val or 0.3))

    # Sample weight
    if sample_size >= 100:
        sample_w = 1.0
    elif sample_size >= 50:
        sample_w = 0.8
    elif sample_size >= 20:
        sample_w = 0.55
    elif sample_size >= 10:
        sample_w = 0.35
    else:
        sample_w = 0.15

    raw = (
        0.35 * roi_norm +
        0.25 * clv_beat +
        0.15 * clv_norm +
        0.10 * hit +
        0.10 * stab +
        0.05 * sample_w
    )
    score = raw * 100.0

    # Sample size caps — avoid over-trusting tiny samples
    if sample_size < 20:
        score = min(score, 60.0)
    elif sample_size < 50:
        score = min(score, 75.0)

    # Penalties
    if (avg_clv_percent or 0.0) < -1.5:
        score -= 12.0   # consistent CLV negative = structural disadvantage
    if (roi or 0.0) < -0.15 and (clv_beat_rate or 0.5) < 0.4:
        score -= 8.0    # ROI negative + CLV underperformance compounded

    return max(0.0, min(100.0, round(score, 1)))


def _compute_recommendation(
    strategy_score: float,
    sample_size: int,
    roi: float | None,
    avg_clv_percent: float | None,
) -> str:
    """Derive recommendation label from score and key metrics."""
    from app.core.config import settings
    min_sample = getattr(settings, "strategy_learning_min_sample", 50)

    if sample_size < MIN_SAMPLE_FOR_RECOMMENDATION:
        return "insufficient_sample"
    if strategy_score >= getattr(settings, "strategy_learning_score_promote", 75.0):
        return "promote" if sample_size >= min_sample else "monitor"
    if strategy_score >= 55.0:
        return "monitor"
    if strategy_score >= 45.0:
        return "neutral"
    if strategy_score >= getattr(settings, "strategy_learning_score_reduce", 40.0):
        return "reduce"
    # Check for avoid: both ROI and CLV clearly negative with sufficient sample
    if (
        sample_size >= min_sample
        and (roi or 0.0) < -0.10
        and (avg_clv_percent or 0.0) < -1.0
    ):
        return "avoid"
    return "reduce"


# ── Annotation builder ─────────────────────────────────────────────────────────


def _get_clv_picks(conn: "duckdb.DuckDBPyConnection", days: int) -> list[dict]:
    """Load recent picks from pick_clv_results DuckDB table."""
    try:
        rows = conn.execute(
            """
            SELECT pick_candidate_id, published_pick_id, fixture_id,
                   league_id, market_key, clv_percent, beat_closing_line,
                   pick_odds
            FROM pick_clv_results
            WHERE created_at >= current_timestamp - INTERVAL (?) DAY
            """,
            [days],
        ).fetchall()
        cols = [
            "pick_candidate_id", "published_pick_id", "fixture_id",
            "league_id", "market_key", "clv_percent", "beat_closing_line",
            "pick_odds",
        ]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.warning("_get_clv_picks failed: %s", exc)
        return []


def _load_settlement_data(days: int) -> dict:
    """Try to load pick settlement data from Supabase. Returns {} on failure."""
    try:
        from app.data.supabase_reader import get_pick_results_recent
        results = get_pick_results_recent(days=days)
        return {r.get("pick_candidate_id"): r for r in results if r.get("pick_candidate_id")}
    except Exception as exc:
        logger.debug("_load_settlement_data from Supabase failed: %s", exc)
        return {}


def _load_pick_meta(days: int) -> dict:
    """Try to load pick candidate metadata from Supabase. Returns {} on failure."""
    try:
        from app.data.supabase_reader import get_pick_candidates_recent
        candidates = get_pick_candidates_recent(days=days)
        return {c.get("id"): c for c in candidates if c.get("id")}
    except Exception as exc:
        logger.debug("_load_pick_meta from Supabase failed: %s", exc)
        return {}


def _load_market_signals(conn: "duckdb.DuckDBPyConnection", fixture_ids: list[int]) -> dict:
    """Load market movement signals from DuckDB for a set of fixtures."""
    if not fixture_ids:
        return {}
    try:
        placeholders = ",".join("?" * len(fixture_ids))
        rows = conn.execute(
            f"""
            SELECT provider_fixture_id, market_key, signal_type, movement_label
            FROM market_movement_signals
            WHERE provider_fixture_id IN ({placeholders})
            """,
            fixture_ids,
        ).fetchall()
        result: dict[int, dict] = {}
        for prov_fid, mkt, sig_type, mv_label in rows:
            if prov_fid not in result:
                result[prov_fid] = {}
            result[prov_fid][mkt] = {"signal_type": sig_type, "movement_label": mv_label}
        return result
    except Exception as exc:
        logger.debug("_load_market_signals failed: %s", exc)
        return {}


def annotate_picks(
    conn: "duckdb.DuckDBPyConnection",
    days: int = 30,
) -> list[dict]:
    """Build pick_learning_annotation rows from available DuckDB + Supabase data.

    Always returns a list (possibly empty). Never raises.
    """
    clv_rows = _get_clv_picks(conn, days)
    if not clv_rows:
        logger.info("annotate_picks: no CLV picks found for last %d days", days)
        return []

    settlement_map = _load_settlement_data(days)
    pick_meta_map  = _load_pick_meta(days)
    fixture_ids    = list({r.get("fixture_id") for r in clv_rows if r.get("fixture_id")})
    signal_map     = _load_market_signals(conn, fixture_ids)

    annotations: list[dict] = []
    for row in clv_rows:
        pick_id    = row.get("pick_candidate_id")
        pub_id     = row.get("published_pick_id")
        fixture_id = row.get("fixture_id")
        league_id  = row.get("league_id")
        market_key = row.get("market_key")
        clv_pct    = row.get("clv_percent")
        beat_cl    = row.get("beat_closing_line", False)
        pick_odds_val = row.get("pick_odds")

        # Settlement
        settle = settlement_map.get(pick_id) or {}
        result_status = settle.get("result_status") or settle.get("status")
        profit        = settle.get("profit")

        # Pick meta
        meta = pick_meta_map.get(pick_id) or {}
        edge_val  = meta.get("edge")
        conf_val  = meta.get("confidence_score") or meta.get("confidence")
        qual_val  = meta.get("quality_score") or meta.get("value_engine_quality_score")
        if pick_odds_val is None:
            pick_odds_val = meta.get("offered_odds") or meta.get("odds")

        # Market signal
        sig = (signal_map.get(fixture_id) or {}).get(market_key) or {}
        mv_label   = sig.get("movement_label")

        # Availability (from meta JSON if available)
        avail_level = None
        try:
            meta_json = meta.get("value_engine_meta") or meta.get("meta_json")
            if isinstance(meta_json, str):
                meta_json = json.loads(meta_json)
            if isinstance(meta_json, dict):
                avail_block = meta_json.get("availability") or {}
                avail_level = avail_block.get("modeled_impact")
        except Exception:
            pass

        # Prematch risk
        pm_risk = None
        try:
            meta_json = meta.get("value_engine_meta") or meta.get("meta_json")
            if isinstance(meta_json, str):
                meta_json = json.loads(meta_json)
            if isinstance(meta_json, dict):
                pm_block = meta_json.get("prematch") or {}
                if pm_block.get("warning"):
                    pm_risk = "drift_high" if "high" in str(pm_block.get("warning")) else "drift_medium"
        except Exception:
            pass

        # Build strategy key
        strat_key = build_strategy_key(
            market_key, league_id,
            pick_odds_val, edge_val, conf_val, clv_pct,
            mv_label, avail_level, pm_risk,
        )

        # Compute learning label
        label = _learning_label(result_status, profit, clv_pct, beat_cl)

        annotations.append({
            "pick_candidate_id":  pick_id,
            "published_pick_id":  pub_id,
            "fixture_id":         fixture_id,
            "strategy_key":       strat_key,
            "market_key":         market_key,
            "league_id":          league_id,
            "odds_bucket":        odds_bucket(pick_odds_val),
            "confidence_bucket":  confidence_bucket(conf_val),
            "edge_bucket":        edge_bucket(edge_val),
            "clv_bucket":         clv_bucket(clv_pct),
            "result_status":      result_status,
            "profit":             profit,
            "clv_percent":        clv_pct,
            "beat_closing_line":  beat_cl,
            "learning_label":     label,
            "metadata": {
                "avg_quality_score": qual_val,
                "movement_label":    mv_label,
                "avail_level":       avail_level,
                "prematch_risk":     pm_risk,
            },
        })

    logger.info("annotate_picks: %d annotations built", len(annotations))
    return annotations


# ── Strategy profile computation ───────────────────────────────────────────────


def compute_strategy_profiles(
    annotations: list[dict],
) -> list[dict]:
    """Group annotations by strategy_key and compute aggregate metrics.

    Returns a list of strategy_profile dicts ready for upsert.
    """
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for ann in annotations:
        key = ann.get("strategy_key") or "unknown"
        groups[key].append(ann)

    profiles: list[dict] = []
    for strat_key, anns in groups.items():
        settled = [a for a in anns if a.get("result_status") in ("win", "loss", "void")]
        with_clv = [a for a in anns if a.get("clv_percent") is not None]

        wins   = sum(1 for a in settled if a.get("result_status") == "win")
        losses = sum(1 for a in settled if a.get("result_status") == "loss")
        voids  = sum(1 for a in settled if a.get("result_status") == "void")
        n_settled = wins + losses + voids
        n_decided = wins + losses  # excluding voids for rate calculations

        hit_rate = round(wins / n_decided, 4) if n_decided > 0 else None

        profits     = [a["profit"] for a in settled if a.get("profit") is not None]
        roi_val     = round(sum(profits) / len(profits), 4) if profits else None
        avg_profit  = round(sum(profits) / len(profits), 4) if profits else None

        clv_vals      = [a["clv_percent"] for a in with_clv]
        avg_clv       = round(sum(clv_vals) / len(clv_vals), 4) if clv_vals else None
        beat_cl_count = sum(1 for a in with_clv if a.get("beat_closing_line"))
        clv_beat_rate_val = round(beat_cl_count / len(with_clv), 4) if with_clv else None

        qual_vals = [
            (a.get("metadata") or {}).get("avg_quality_score")
            for a in anns
            if (a.get("metadata") or {}).get("avg_quality_score") is not None
        ]
        avg_qual = round(sum(qual_vals) / len(qual_vals), 4) if qual_vals else None

        # Derive per-pick edge and confidence from buckets (first annotation)
        first = anns[0]
        sample_size = len(anns)

        stab = _stability_score(sample_size, roi_val, clv_beat_rate_val)
        score = _compute_strategy_score(
            sample_size, roi_val, clv_beat_rate_val, avg_clv, hit_rate, stab
        )
        rec = _compute_recommendation(score, sample_size, roi_val, avg_clv)

        # Decode scope and league info from strategy_key
        parts = strat_key.split("|")
        market_part = parts[0] if parts else "unknown"
        scope_part  = parts[1] if len(parts) > 1 else "global"
        league_id_val = None
        if scope_part.startswith("league_"):
            try:
                league_id_val = int(scope_part[7:])
            except ValueError:
                pass

        profiles.append({
            "strategy_key":     strat_key,
            "market_key":       market_part,
            "league_id":        league_id_val,
            "league_name":      None,
            "scope":            scope_part,
            "odds_bucket":      first.get("odds_bucket"),
            "confidence_bucket":first.get("confidence_bucket"),
            "edge_bucket":      first.get("edge_bucket"),
            "clv_bucket":       first.get("clv_bucket"),
            "sample_size":      sample_size,
            "wins":             wins,
            "losses":           losses,
            "voids":            voids,
            "hit_rate":         hit_rate,
            "roi":              roi_val,
            "avg_profit":       avg_profit,
            "avg_clv_percent":  avg_clv,
            "clv_beat_rate":    clv_beat_rate_val,
            "avg_edge":         None,
            "avg_quality_score":avg_qual,
            "avg_confidence":   None,
            "stability_score":  stab,
            "strategy_score":   score,
            "recommendation":   rec,
            "metadata": {
                "n_settled": n_settled,
                "n_with_clv": len(with_clv),
            },
        })

    return profiles


# ── Strategy adjustments ───────────────────────────────────────────────────────


def generate_strategy_adjustments(
    profiles: list[dict],
) -> list[dict]:
    """Derive adjustment recommendations from computed strategy profiles.

    Does NOT apply adjustments — only generates recommendations.
    """
    from app.core.config import settings
    min_sample = getattr(settings, "strategy_learning_min_sample", 50)
    score_promote = getattr(settings, "strategy_learning_score_promote", 75.0)
    score_reduce  = getattr(settings, "strategy_learning_score_reduce", 40.0)

    adjustments: list[dict] = []
    for p in profiles:
        key    = p.get("strategy_key")
        score  = p.get("strategy_score") or 0.0
        sample = p.get("sample_size") or 0
        roi    = p.get("roi")
        clv    = p.get("avg_clv_percent")
        rec    = p.get("recommendation")
        market = p.get("market_key")
        league = p.get("league_id")

        if sample < MIN_SAMPLE_FOR_RECOMMENDATION:
            adjustments.append({
                "strategy_key":        key,
                "market_key":          market,
                "league_id":           league,
                "adjustment_type":     "request_sample",
                "adjustment_value":    float(min_sample - sample),
                "reason":              f"Sample {sample} < minimum {min_sample} for reliable signal",
                "evidence_sample_size": sample,
                "evidence_roi":        roi,
                "evidence_clv":        clv,
                "status":              "pending",
            })
            continue

        if score >= score_promote and sample >= min_sample:
            adjustments.append({
                "strategy_key":        key,
                "market_key":          market,
                "league_id":           league,
                "adjustment_type":     "promote",
                "adjustment_value":    getattr(settings, "strategy_learning_max_boost", 0.05),
                "reason":              f"score={score:.1f} roi={_pct(roi)} clv_avg={_pct(clv)}",
                "evidence_sample_size": sample,
                "evidence_roi":        roi,
                "evidence_clv":        clv,
                "status":              "pending",
            })

        elif rec == "avoid" and sample >= min_sample:
            adjustments.append({
                "strategy_key":        key,
                "market_key":          market,
                "league_id":           league,
                "adjustment_type":     "avoid",
                "adjustment_value":    getattr(settings, "strategy_learning_max_penalty", 0.08),
                "reason":              f"score={score:.1f} roi={_pct(roi)} clv_avg={_pct(clv)} — both negative with n={sample}",
                "evidence_sample_size": sample,
                "evidence_roi":        roi,
                "evidence_clv":        clv,
                "status":              "pending",
            })

        elif score <= score_reduce:
            adjustments.append({
                "strategy_key":        key,
                "market_key":          market,
                "league_id":           league,
                "adjustment_type":     "reduce",
                "adjustment_value":    getattr(settings, "strategy_learning_max_penalty", 0.08) * 0.5,
                "reason":              f"score={score:.1f} below reduce threshold",
                "evidence_sample_size": sample,
                "evidence_roi":        roi,
                "evidence_clv":        clv,
                "status":              "pending",
            })

        # Parlay exposure reduction for weak strategies
        if rec in ("reduce", "avoid") and market and "1X2" in market:
            adjustments.append({
                "strategy_key":        key,
                "market_key":          market,
                "league_id":           league,
                "adjustment_type":     "reduce_parlay",
                "adjustment_value":    0.5,
                "reason":              f"Weak 1X2 strategy — reduce parlay exposure",
                "evidence_sample_size": sample,
                "evidence_roi":        roi,
                "evidence_clv":        clv,
                "status":              "pending",
            })

    return adjustments


def _pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%"


# ── Main entry point ───────────────────────────────────────────────────────────


def run_strategy_learning(
    conn: "duckdb.DuckDBPyConnection",
    days: int = 30,
    dry_run: bool = True,
    market_key: str | None = None,
    league_id: int | None = None,
    min_sample: int | None = None,
) -> dict:
    """Execute the full strategy learning pipeline.

    Args:
        conn:       DuckDB connection
        days:       How many days of data to process
        dry_run:    If True, compute but do not write to DuckDB
        market_key: Optional filter
        league_id:  Optional filter
        min_sample: Override minimum sample threshold

    Returns:
        Summary dict with counts and top/bottom strategy keys.
    """
    from app.data.local.strategy_learning_repo import (
        upsert_pick_learning_annotation,
        upsert_strategy_profile,
        upsert_strategy_adjustment,
        upsert_strategy_learning_run,
    )

    start = datetime.now(timezone.utc)
    run_key = f"sl_{start.strftime('%Y%m%d_%H%M%S')}"

    annotations = annotate_picks(conn, days=days)

    # Optional filters
    if market_key:
        annotations = [a for a in annotations if a.get("market_key") == market_key]
    if league_id is not None:
        annotations = [a for a in annotations if a.get("league_id") == league_id]

    profiles    = compute_strategy_profiles(annotations)
    adjustments = generate_strategy_adjustments(profiles)

    if min_sample is not None:
        profiles_out = [p for p in profiles if (p.get("sample_size") or 0) >= min_sample]
    else:
        profiles_out = profiles

    # Sort for reporting
    sorted_profiles = sorted(
        profiles_out, key=lambda p: p.get("strategy_score") or 0.0, reverse=True
    )
    best_key  = sorted_profiles[0]["strategy_key"]  if sorted_profiles else None
    worst_key = sorted_profiles[-1]["strategy_key"] if sorted_profiles else None

    if not dry_run:
        written_ann  = 0
        written_prof = 0
        for ann in annotations:
            if upsert_pick_learning_annotation(conn, ann):
                written_ann += 1
        for prof in profiles:
            if upsert_strategy_profile(conn, prof):
                written_prof += 1
        for adj in adjustments:
            upsert_strategy_adjustment(conn, adj)
        upsert_strategy_learning_run(conn, {
            "run_key":          run_key,
            "days":             days,
            "total_picks":      len(annotations),
            "profiles_created": written_prof,
            "profiles_updated": written_prof,
            "best_strategy_key":  best_key,
            "worst_strategy_key": worst_key,
            "notes": {
                "dry_run": False,
                "adjustments": len(adjustments),
            },
        })

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info(
        "run_strategy_learning: n=%d annotations, %d profiles, %d adjustments in %.1fs %s",
        len(annotations), len(profiles), len(adjustments), elapsed,
        "[dry-run]" if dry_run else "[saved]",
    )

    return {
        "run_key":      run_key,
        "days":         days,
        "dry_run":      dry_run,
        "annotations":  len(annotations),
        "profiles":     len(profiles),
        "adjustments":  len(adjustments),
        "best_key":     best_key,
        "worst_key":    worst_key,
        "elapsed_s":    round(elapsed, 2),
    }


# ── VE integration: look up strategy for a pick ───────────────────────────────


def get_strategy_for_pick(
    conn: "duckdb.DuckDBPyConnection",
    market_key: str,
    league_id: int | None,
    pick_odds_val: float | None,
    edge_val: float | None,
    confidence_val: float | None,
    clv_percent_val: float | None = None,
) -> dict | None:
    """Return strategy metadata for a pick candidate (for VE enrichment).

    Returns None if STRATEGY_LEARNING_ENABLED=false or no matching profile.
    """
    try:
        from app.core.config import settings
        if not getattr(settings, "strategy_learning_enabled", False):
            return None

        from app.data.local.strategy_learning_repo import get_strategy_by_key

        strat_key = build_strategy_key(
            market_key, league_id,
            pick_odds_val, edge_val, confidence_val, clv_percent_val,
        )
        return get_strategy_by_key(conn, strat_key)
    except Exception as exc:
        logger.debug("get_strategy_for_pick failed: %s", exc)
        return None
