"""Probability calibration service — Phase 6.

Trains and applies histogram-isotonic calibrators from backtest training_samples.
Uses the Pool Adjacent Violators Algorithm (PAVA) to enforce monotonicity.

Hierarchical fallback chain when applying a calibrator:
    1. League-specific  (provider_league_id + market_key + entity_type)
    2. Entity-type global (market_key + entity_type, no league filter)
    3. No calibration   (returns p_raw unchanged, w_rel = 0.1)

All calibrators are persisted to calibration_registry as JSON.
Application uses nearest-bin lookup (no interpolation, robust to sparse data).

Phase 5 constraints:
  - Only training_samples from completed fixtures (model_correct populated)
  - No odds-dependent metrics
  - Calibration improves ECE; log-loss improvement is secondary
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

_N_BINS_DEFAULT    = 10
_MIN_SAMPLES_LEAGUE = 50
_MIN_SAMPLES_GLOBAL = 100


# ── PAVA (isotonic regression) ─────────────────────────────────────────────────


def _pava(values: list[float], weights: list[float]) -> list[float]:
    """Weighted Pool Adjacent Violators — isotonic regression, non-decreasing.

    Args:
        values:  observed rates per bin (y_i)
        weights: sample counts per bin  (w_i, must be >= 1)

    Returns:
        Isotonic-constrained values of the same length as `values`.
    """
    n = len(values)
    if n <= 1:
        return list(values)
    # Each block: [weighted_sum, total_weight, start_idx, end_idx]
    stack: list[list[Any]] = []
    for i in range(n):
        stack.append([values[i] * weights[i], weights[i], i, i])
        while len(stack) > 1:
            prev, cur = stack[-2], stack[-1]
            if (prev[0] / prev[1]) > (cur[0] / cur[1]):
                prev[0] += cur[0]
                prev[1] += cur[1]
                prev[3] = cur[3]
                stack.pop()
            else:
                break
    result = [0.0] * n
    for block in stack:
        mean = block[0] / block[1]
        for idx in range(block[2], block[3] + 1):
            result[idx] = mean
    return result


# ── Calibration metrics ────────────────────────────────────────────────────────


def _compute_ece(samples: list[tuple[float, bool]], n_bins: int = 10) -> float:
    """Expected Calibration Error from (probability, correct) pairs."""
    if not samples:
        return 0.0
    bins: dict[int, list] = {}
    for p, c in samples:
        b = min(int(p * n_bins), n_bins - 1)
        bins.setdefault(b, []).append((p, c))
    total = len(samples)
    ece = 0.0
    for items in bins.values():
        n = len(items)
        mean_p = sum(x[0] for x in items) / n
        mean_c = sum(1 for x in items if x[1]) / n
        ece += (n / total) * abs(mean_p - mean_c)
    return round(ece, 6)


def _compute_logloss(samples: list[tuple[float, bool]]) -> float:
    """Binary log-loss from (probability, correct) pairs."""
    if not samples:
        return 0.0
    eps = 1e-9
    total = 0.0
    for p, c in samples:
        p_c = max(eps, min(1.0 - eps, p))
        total += -math.log(p_c) if c else -math.log(1.0 - p_c)
    return round(total / len(samples), 6)


def _compute_brier(samples: list[tuple[float, bool]]) -> float:
    """Brier score from (probability, correct) pairs."""
    if not samples:
        return 0.0
    return round(sum((p - (1.0 if c else 0.0)) ** 2 for p, c in samples) / len(samples), 6)


# ── Training ───────────────────────────────────────────────────────────────────


def train_calibrator(
    samples: list[tuple[float, bool]],
    *,
    n_bins: int = _N_BINS_DEFAULT,
) -> dict:
    """Train a histogram-isotonic calibrator.

    Args:
        samples: list of (model_probability, model_correct) pairs
        n_bins:  number of equal-width bins in [0, 1]

    Returns:
        dict with bins list, n_train, ece_before/after, logloss_before/after
    """
    ece_before = _compute_ece(samples)
    ll_before  = _compute_logloss(samples)

    edges   = [i / n_bins for i in range(n_bins + 1)]
    centers = [(edges[i] + edges[i + 1]) / 2 for i in range(n_bins)]
    counts  = [0] * n_bins
    hits    = [0] * n_bins

    for p, c in samples:
        b = min(int(p * n_bins), n_bins - 1)
        counts[b] += 1
        if c:
            hits[b] += 1

    raw_rates = [
        hits[b] / counts[b] if counts[b] > 0 else centers[b]
        for b in range(n_bins)
    ]
    weights = [max(1, counts[b]) for b in range(n_bins)]

    iso_rates = _pava(raw_rates, weights)

    def _apply(p: float) -> float:
        b = min(int(p * n_bins), n_bins - 1)
        return iso_rates[b]

    cal_samples = [(_apply(p), c) for p, c in samples]
    ece_after = _compute_ece(cal_samples)
    ll_after  = _compute_logloss(cal_samples)

    bins = [
        {
            "center":     round(centers[b], 6),
            "n":          counts[b],
            "raw_rate":   round(raw_rates[b], 6) if counts[b] > 0 else None,
            "calibrated": round(iso_rates[b], 6),
        }
        for b in range(n_bins)
    ]

    return {
        "bins":           bins,
        "n_train":        len(samples),
        "ece_before":     ece_before,
        "ece_after":      ece_after,
        "logloss_before": ll_before,
        "logloss_after":  ll_after,
    }


# ── Application ────────────────────────────────────────────────────────────────


def apply_calibrator(params: dict | None, p_raw: float) -> float:
    """Apply a stored calibrator dict to a raw probability.

    Uses nearest-bin lookup. Returns p_raw if params is None or empty.
    """
    if not params:
        return p_raw
    bins = params.get("bins", [])
    if not bins:
        return p_raw
    n_bins = len(bins)
    b = min(int(p_raw * n_bins), n_bins - 1)
    return bins[b]["calibrated"]


# ── Reliability weight ─────────────────────────────────────────────────────────


def compute_w_rel(calib_meta: dict | None) -> float:
    """Compute reliability weight [0, 1] from calibrator metadata.

    Higher w_rel = more trust in calibrated probability vs. market odds.
    In Phase 5 (no odds), used as quality indicator and ranking signal.

    Uses val_ece (out-of-sample) when available — more reliable than training
    ece_after which overfits by construction. Falls back to ece_after.

    Formula:
        sample_size_score   = min(1.0, n_train / 500)
        calibration_quality = max(0.0, 1.0 - ece * 8.0)   # ece = val or train
        w_rel = 0.5 * sample_size_score + 0.5 * calibration_quality
    """
    if calib_meta is None:
        return 0.10
    n = calib_meta.get("n_train", 0)
    ece = calib_meta.get("val_ece") or calib_meta.get("ece_after", 0.20)
    sample_score  = min(1.0, n / 500.0)
    calib_quality = max(0.0, 1.0 - ece * 8.0)
    return round(min(1.0, 0.5 * sample_score + 0.5 * calib_quality), 4)


# ── DB helpers ─────────────────────────────────────────────────────────────────


def _apply_bins(bins: list[dict], p: float) -> float:
    """Apply calibrator bin list to a raw probability (nearest-bin lookup)."""
    n = len(bins)
    b = min(int(p * n), n - 1)
    return bins[b]["calibrated"]


def _fetch_training_samples(
    conn,
    market_key: str,
    *,
    provider_league_id: int | None = None,
    entity_type: str = "club",
    run_id: int | None = None,
    exclude_season: int | None = None,
) -> list[tuple[float, bool]]:
    """Load (model_probability, model_correct) pairs from training_samples.

    Joins competition_context to filter by entity_scope.
    Rows with NULL entity_scope default to 'club'.
    exclude_season: omit rows from this season (used for out-of-sample splits).
    """
    filters = ["ts.market_key = ?", "COALESCE(cc.entity_scope, 'club') = ?"]
    params: list = [market_key, entity_type]

    if provider_league_id is not None:
        filters.append("ts.provider_league_id = ?")
        params.append(provider_league_id)
    if run_id is not None:
        filters.append("ts.backtest_run_id = ?")
        params.append(run_id)
    if exclude_season is not None:
        filters.append("ts.season != ?")
        params.append(exclude_season)

    where = " AND ".join(filters)
    rows = conn.execute(
        f"""
        SELECT ts.model_probability, ts.model_correct
        FROM training_samples ts
        LEFT JOIN competition_context cc
               ON cc.provider_league_id = ts.provider_league_id
        WHERE {where}
          AND ts.model_probability IS NOT NULL
          AND ts.model_correct     IS NOT NULL
        """,
        params,
    ).fetchall()
    return [(float(r[0]), bool(r[1])) for r in rows]


def _fetch_samples_by_season(
    conn,
    market_key: str,
    provider_league_id: int,
    *,
    run_id: int | None = None,
) -> dict[int, list[tuple[float, bool]]]:
    """Return {season -> [(prob, correct), ...]} for a league/market."""
    filters = [
        "ts.market_key = ?",
        "ts.provider_league_id = ?",
        "ts.model_probability IS NOT NULL",
        "ts.model_correct IS NOT NULL",
    ]
    params: list = [market_key, provider_league_id]
    if run_id is not None:
        filters.append("ts.backtest_run_id = ?")
        params.append(run_id)

    rows = conn.execute(
        "SELECT ts.season, ts.model_probability, ts.model_correct "
        f"FROM training_samples ts WHERE {' AND '.join(filters)} ORDER BY ts.season",
        params,
    ).fetchall()

    by_season: dict[int, list] = {}
    for season, prob, correct in rows:
        by_season.setdefault(season, []).append((float(prob), bool(correct)))
    return by_season


def _store_calibrator(
    conn,
    result: dict,
    *,
    market_key: str,
    entity_type: str,
    competition_type: str | None,
    provider_league_id: int | None,
    method: str = "histogram_isotonic",
    n_val: int | None = None,
    val_ece: float | None = None,
    val_brier: float | None = None,
    val_logloss: float | None = None,
) -> int:
    """Insert a calibration_registry row. Returns the new id.

    trained_at uses DuckDB DEFAULT current_timestamp — avoids pytz dependency
    when the column is later queried with CAST(trained_at AS VARCHAR).
    val_* fields are populated only when a held-out val_season is provided.
    """
    conn.execute(
        """
        INSERT INTO calibration_registry (
            market_key, entity_type, competition_type,
            provider_league_id, method, n_train,
            ece_before, ece_after, logloss_before, logloss_after, params_json,
            n_val, val_ece, val_brier, val_logloss
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            market_key,
            entity_type,
            competition_type,
            provider_league_id,
            method,
            result["n_train"],
            result["ece_before"],
            result["ece_after"],
            result["logloss_before"],
            result["logloss_after"],
            json.dumps({"bins": result["bins"]}),
            n_val,
            val_ece,
            val_brier,
            val_logloss,
        ],
    )
    row = conn.execute("SELECT currval('seq_calibration')").fetchone()
    return row[0]


def _upsert_market_quality(
    conn,
    *,
    provider_league_id: int,
    market_key: str,
    entity_type: str,
    competition_type: str | None,
    season: int,
    samples: list[tuple[float, bool]],
) -> None:
    """Compute and upsert one market_quality_summary row."""
    n = len(samples)
    eligible = n >= 30
    logloss  = _compute_logloss(samples) if n > 0 else None
    brier    = _compute_brier(samples)   if n > 0 else None
    ece      = _compute_ece(samples)     if n > 0 else None
    sharpness = round(sum(p for p, _ in samples) / n, 6) if n > 0 else None
    hit_rate  = round(sum(1 for _, c in samples if c) / n, 6) if n > 0 else None

    if ece is not None and ece > 0.20:
        eligible = False
    if logloss is not None and logloss > 1.10:
        eligible = False

    conn.execute(
        """
        INSERT OR REPLACE INTO market_quality_summary (
            provider_league_id, market_key, entity_type, competition_type, season,
            n, logloss, brier, ece, sharpness, hit_rate, drift_score, eligible_flag
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        [
            provider_league_id, market_key, entity_type, competition_type, season,
            n, logloss, brier, ece, sharpness, hit_rate, eligible,
        ],
    )


def _get_entity_types_for_leagues(conn, league_ids: list[int]) -> list[str]:
    """Return distinct entity_scope values for given leagues from competition_context."""
    if not league_ids:
        return ["club"]
    placeholders = ", ".join("?" * len(league_ids))
    rows = conn.execute(
        f"SELECT DISTINCT entity_scope FROM competition_context "
        f"WHERE provider_league_id IN ({placeholders})",
        league_ids,
    ).fetchall()
    return [r[0] for r in rows] if rows else ["club"]


# ── Public API ─────────────────────────────────────────────────────────────────


def get_calibrator_for_pick(
    conn,
    *,
    market_key: str,
    entity_type: str,
    competition_type: str | None = None,
    provider_league_id: int | None = None,
) -> tuple[dict | None, dict | None]:
    """Load the best available calibrator using hierarchical fallback.

    Returns (params_dict, meta_dict) where:
        params_dict: JSON bins for apply_calibrator()
        meta_dict:   n_train, ece_before/after, logloss_before/after

    Fallback order: league-specific -> entity-type global -> (None, None)
    """
    def _row_to_meta(row) -> dict:
        return {
            "n_train":        row[1],
            "ece_before":     row[2],
            "ece_after":      row[3],
            "logloss_before": row[4],
            "logloss_after":  row[5],
            "n_val":          row[6],
            "val_ece":        row[7],
            "val_brier":      row[8],
            "val_logloss":    row[9],
        }

    if provider_league_id is not None:
        row = conn.execute(
            """
            SELECT params_json, n_train, ece_before, ece_after,
                   logloss_before, logloss_after,
                   n_val, val_ece, val_brier, val_logloss
            FROM calibration_registry
            WHERE market_key = ? AND entity_type = ? AND provider_league_id = ?
            ORDER BY CAST(trained_at AS VARCHAR) DESC LIMIT 1
            """,
            [market_key, entity_type, provider_league_id],
        ).fetchone()
        if row:
            return json.loads(row[0]), _row_to_meta(row)

    row = conn.execute(
        """
        SELECT params_json, n_train, ece_before, ece_after,
               logloss_before, logloss_after,
               n_val, val_ece, val_brier, val_logloss
        FROM calibration_registry
        WHERE market_key = ? AND entity_type = ?
          AND provider_league_id IS NULL AND competition_type IS NULL
        ORDER BY CAST(trained_at AS VARCHAR) DESC LIMIT 1
        """,
        [market_key, entity_type],
    ).fetchone()
    if row:
        return json.loads(row[0]), _row_to_meta(row)

    return None, None


def _compute_val_metrics(
    bins: list[dict],
    val_samples: list[tuple[float, bool]],
) -> dict:
    """Apply trained bins to val_samples and return out-of-sample metrics."""
    if not val_samples:
        return {}
    cal_val = [(_apply_bins(bins, p), c) for p, c in val_samples]
    return {
        "n_val":      len(val_samples),
        "val_ece":    _compute_ece(cal_val),
        "val_brier":  _compute_brier(cal_val),
        "val_logloss": _compute_logloss(cal_val),
    }


def train_and_store(
    conn,
    *,
    league_ids: list[int] | None = None,
    markets: list[str] | None = None,
    run_id: int | None = None,
    n_bins: int = _N_BINS_DEFAULT,
    min_samples_league: int = _MIN_SAMPLES_LEAGUE,
    min_samples_global: int = _MIN_SAMPLES_GLOBAL,
    val_season: int | None = None,
) -> list[dict]:
    """Train calibrators from training_samples and store in calibration_registry.

    Also populates market_quality_summary per league/market/season.

    Trains two scopes per (entity_type, market_key):
      1. League-specific — if >= min_samples_league samples available
      2. Entity-type global — if >= min_samples_global samples total

    Args:
        conn:                DuckDB connection (schema must already be applied)
        league_ids:          leagues to include; None = all in training_samples
        markets:             markets to calibrate; None = all 4
        run_id:              restrict to one backtest_run_id
        n_bins:              histogram resolution (default 10)
        min_samples_league:  threshold for league-specific calibrator
        min_samples_global:  threshold for entity-type global calibrator
        val_season:          if set, this season is held out for validation;
                             calibrator trains on all OTHER seasons and is
                             evaluated on val_season → real ECE/Brier/logloss

    Returns:
        List of summary dicts — one per trained calibrator, ordered league first.
        When val_season is set, each dict also contains n_val, val_ece,
        val_brier, val_logloss.
    """
    from app.data.local import history_repo
    from app.services.backtest_service import MARKETS

    if markets is None:
        markets = list(MARKETS)
    if league_ids is None:
        league_ids = history_repo.get_leagues_in_history(conn)

    entity_types = _get_entity_types_for_leagues(conn, league_ids)
    summaries: list[dict] = []

    for market_key in markets:
        for entity_type in entity_types:

            # ── League-specific calibrators ────────────────────────────────────
            for league_id in league_ids:
                ctx = history_repo.get_competition_context(conn, league_id)
                competition_type = ctx["competition_type"] if ctx else "domestic_league"

                samples = _fetch_training_samples(
                    conn, market_key,
                    provider_league_id=league_id,
                    entity_type=entity_type,
                    run_id=run_id,
                    exclude_season=val_season,
                )

                # Populate market_quality_summary per season regardless of threshold
                by_season = _fetch_samples_by_season(
                    conn, market_key, league_id, run_id=run_id
                )
                for season, s_samples in by_season.items():
                    _upsert_market_quality(
                        conn,
                        provider_league_id=league_id,
                        market_key=market_key,
                        entity_type=entity_type,
                        competition_type=competition_type,
                        season=season,
                        samples=s_samples,
                    )

                if len(samples) < min_samples_league:
                    logger.debug(
                        "calib: skip league %d / %s (n=%d < %d)",
                        league_id, market_key, len(samples), min_samples_league,
                    )
                    continue

                result = train_calibrator(samples, n_bins=n_bins)

                val_samples = by_season.get(val_season, []) if val_season else []
                val_meta = _compute_val_metrics(result["bins"], val_samples)

                _id = _store_calibrator(
                    conn, result,
                    market_key=market_key,
                    entity_type=entity_type,
                    competition_type=competition_type,
                    provider_league_id=league_id,
                    **val_meta,
                )
                summaries.append({
                    "id":                 _id,
                    "market_key":         market_key,
                    "entity_type":        entity_type,
                    "provider_league_id": league_id,
                    "scope":              "league",
                    "n_train":            result["n_train"],
                    "ece_before":         result["ece_before"],
                    "ece_after":          result["ece_after"],
                    **val_meta,
                })
                logger.info(
                    "calib: league %d / %s / %s  n=%d  ECE %.4f->%.4f%s",
                    league_id, market_key, entity_type,
                    result["n_train"], result["ece_before"], result["ece_after"],
                    f"  val_ECE={val_meta['val_ece']:.4f}" if val_meta else "",
                )

            # ── Entity-type global calibrator ──────────────────────────────────
            global_samples = _fetch_training_samples(
                conn, market_key,
                entity_type=entity_type,
                run_id=run_id,
                exclude_season=val_season,
            )
            if len(global_samples) >= min_samples_global:
                result = train_calibrator(global_samples, n_bins=n_bins)

                # Val samples for global: all leagues' val_season samples
                global_val: list[tuple[float, bool]] = []
                if val_season:
                    for lid in league_ids:
                        by_s = _fetch_samples_by_season(conn, market_key, lid, run_id=run_id)
                        global_val.extend(by_s.get(val_season, []))
                global_val_meta = _compute_val_metrics(result["bins"], global_val)

                _id = _store_calibrator(
                    conn, result,
                    market_key=market_key,
                    entity_type=entity_type,
                    competition_type=None,
                    provider_league_id=None,
                    **global_val_meta,
                )
                summaries.append({
                    "id":                 _id,
                    "market_key":         market_key,
                    "entity_type":        entity_type,
                    "provider_league_id": None,
                    "scope":              "global",
                    "n_train":            result["n_train"],
                    "ece_before":         result["ece_before"],
                    "ece_after":          result["ece_after"],
                    **global_val_meta,
                })
                logger.info(
                    "calib: global %s / %s  n=%d  ECE %.4f->%.4f%s",
                    entity_type, market_key,
                    result["n_train"], result["ece_before"], result["ece_after"],
                    f"  val_ECE={global_val_meta['val_ece']:.4f}" if global_val_meta else "",
                )

    return summaries
