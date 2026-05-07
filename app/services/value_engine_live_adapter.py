"""Live adapter: Value Engine integration with the production pick pipeline.

Connects PredictionService's PredictionCandidates (from Supabase odds) to the
offline DuckDB value engine (Poisson+Elo+calibration). Returns enriched value
metrics without modifying production state unless explicitly requested.

Three modes (from settings.value_engine_mode):
    off    - adapter is a no-op; no DuckDB access; production flow unchanged
    shadow - evaluates all candidates, stores metrics; does NOT change selection
    assist - evaluates, then filters/reorders candidates by value metrics;
             falls back to original list if VALUE_ENGINE_FALLBACK_TO_CURRENT=true

Design constraints:
    - Never raises an exception toward production callers.
    - If DuckDB is missing or locked: logs warning, returns empty/fallback results.
    - If Supabase mapping call fails: logs warning, marks affected fixtures as error.
    - Caches cs_map, team_map, predict_fixture per fixture_id per adapter instance.
    - Singleton instance at module level: value_engine_adapter.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from app.model.predictors.odds_predictor import PredictionCandidate

logger = logging.getLogger(__name__)

# ── Status constants ──────────────────────────────────────────────────────────

STATUS_SELECTED              = "value_selected"
STATUS_LOW_QUALITY           = "value_rejected_low_quality"
STATUS_LOW_EDGE              = "value_rejected_low_edge"
STATUS_MISSING_ODDS          = "value_rejected_missing_odds"
STATUS_MISSING_HISTORY       = "value_rejected_missing_history"
STATUS_MISSING_CALIBRATOR    = "value_rejected_missing_calibrator"
STATUS_OFF                   = "value_skipped_engine_off"
STATUS_ERROR                 = "value_error_fallback"
STATUS_REJECTED_AVAILABILITY = "value_rejected_high_availability_risk"
STATUS_REJECTED_PREMATCH     = "value_rejected_high_prematch_drift"

# Impact levels ordered by severity (used for max() comparisons)
_IMPACT_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "unknown": -1}
_STRENGTH_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}

_EMPTY: dict = {
    "value_engine_status":          STATUS_OFF,
    "p_raw":                        None,
    "p_cal":                        None,
    "fair_odds":                    None,
    "p_mkt":                        None,
    "edge":                         None,
    "ev":                           None,
    "ev_adj":                       None,
    "w_rel":                        None,
    "quality_score":                None,
    "rejection_reason":             None,
    "model_scope":                  None,
    "calibrator_scope":             None,
    "historical_sample_size":       None,
    "value_rank_score":             None,
    # Phase 5: availability enrichment (None = not evaluated)
    "quality_before_availability":  None,
    "availability_penalty":         None,
    "availability_boost":           None,
    "availability_coverage":        None,
    "availability_modeled_impact":  None,
    "availability_opponent_impact": None,
    "availability_warning":         None,
    # Phase 6
    "prematch_signal":              None,
    "provider_league_id":           None,
    # Phase 13: strategy learning (None = not evaluated or feature off)
    "strategy_key":                 None,
    "strategy_score":               None,
    "strategy_recommendation":      None,
    "strategy_sample_size":         None,
    "strategy_roi":                 None,
    "strategy_clv_beat_rate":       None,
    # Phase 14: bankroll & risk (None = engine off or not evaluated)
    "bankroll_risk_score":          None,
    "bankroll_risk_label":          None,
    "bankroll_recommended_units":   None,
    "bankroll_stake_label":         None,
    "bankroll_kelly_full":          None,
    "bankroll_kelly_frac":          None,
    "bankroll_exposure_flags":      None,
    "bankroll_warning":             None,
}


def _err(reason: str) -> dict:
    r = dict(_EMPTY)
    r["value_engine_status"] = STATUS_ERROR
    r["rejection_reason"] = reason[:120]
    return r


def _skip(status: str, reason: str | None = None) -> dict:
    r = dict(_EMPTY)
    r["value_engine_status"] = status
    r["rejection_reason"] = reason
    return r


# ── Adapter class ─────────────────────────────────────────────────────────────


class ValueEngineLiveAdapter:
    """Evaluates production pick candidates against the offline DuckDB value engine.

    Maintains a lazy DuckDB connection and per-run caches for Supabase ID
    mappings. Safe to instantiate once at module level and reuse across
    run_for_today() calls — caches are cleared between runs via reset_caches().

    Thread-safety: designed for single-process use (standard bot deployment).
    """

    def __init__(self) -> None:
        self._conn = None         # lazy DuckDB connection
        self._conn_failed = False  # True once we know DuckDB is unavailable
        self._cs_map: dict | None = None      # {cs_id: {provider_league_id, league_name}}
        self._team_map: dict[int, dict] = {}  # {team_id: {provider_team_id, name}}
        self._avail_cache: dict[int, dict | None] = {}  # {provider_fixture_id: avail_data|None}
        self._bypass_mode_check = False       # set True by test scripts to skip settings check

    # ── Public checks ─────────────────────────────────────────────────────────

    def is_mode_active(self) -> bool:
        """True when the adapter should process candidates (mode != off and enabled)."""
        if self._bypass_mode_check:
            return True
        return (
            settings.value_engine_enabled
            and settings.value_engine_mode in ("shadow", "assist")
        )

    def is_value_engine_available(self) -> bool:
        """True when mode is active AND DuckDB connection succeeds."""
        return self.is_mode_active() and self._get_conn() is not None

    def reset_caches(self) -> None:
        """Clear per-run caches (call between daily runs if needed)."""
        self._cs_map = None
        self._team_map = {}
        self._avail_cache = {}

    # ── DuckDB connection ─────────────────────────────────────────────────────

    def _get_conn(self):
        if self._conn_failed:
            return None
        if self._conn is not None:
            return self._conn
        try:
            db_path = settings.value_engine_local_db_path
            if not Path(db_path).exists():
                logger.warning(
                    "ValueEngine: DuckDB no encontrado en '%s'. "
                    "Continuando con flujo actual (sin value engine).",
                    db_path,
                )
                self._conn_failed = True
                return None
            from app.data.local.duckdb_client import get_local_db
            self._conn = get_local_db(db_path)
            logger.info("ValueEngine: DuckDB conectado desde '%s'", db_path)
            return self._conn
        except Exception as exc:
            logger.warning("ValueEngine: error conectando DuckDB: %s", exc)
            self._conn_failed = True
            return None

    # ── Supabase ID mappings ──────────────────────────────────────────────────

    def _load_cs_map(self) -> dict:
        if self._cs_map is not None:
            return self._cs_map
        try:
            from app.data import supabase_reader
            self._cs_map = supabase_reader.get_competition_season_map()
            logger.debug("ValueEngine: cs_map cargado (%d ligas)", len(self._cs_map))
        except Exception as exc:
            logger.warning("ValueEngine: error cargando cs_map desde Supabase: %s", exc)
            self._cs_map = {}
        return self._cs_map

    def _load_team_map(self, team_ids: list[int]) -> None:
        uncached = [t for t in team_ids if t not in self._team_map]
        if not uncached:
            return
        try:
            from app.data import supabase_reader
            new_entries = supabase_reader.get_team_provider_map(uncached)
            self._team_map.update(new_entries)
        except Exception as exc:
            logger.warning("ValueEngine: error cargando team_map desde Supabase: %s", exc)

    # ── Phase 6: Prematch enrichment ─────────────────────────────────────────

    def _load_prematch_meta(
        self,
        conn,
        provider_fixture_id: int,
        market: str,
        selection: str,
    ) -> dict:
        """Read prematch odds movement and alerts for a (fixture, market, selection).

        Returns a flat dict with: movement_direction, movement_strength,
        implied_delta, odds_delta, alerts (list of high/medium severity titles).
        Returns empty dict if no prematch data exists.
        """
        try:
            from app.data.local.prematch_repo import (
                get_odds_movement_by_provider_fixture,
                get_alerts_for_fixture,
            )
            all_mv = get_odds_movement_by_provider_fixture(conn, provider_fixture_id)
            mv = next(
                (
                    r for r in all_mv
                    if r["market_key"] == market and r["selection"] == selection
                ),
                None,
            )
            alerts = [
                a["title"] for a in get_alerts_for_fixture(conn, provider_fixture_id)
                if a.get("severity") in ("high", "medium")
                and a.get("related_market") == market
            ]
            if mv is None:
                return {"alerts": alerts} if alerts else {}
            return {
                "movement_direction": mv.get("movement_direction", "stable"),
                "movement_strength":  mv.get("movement_strength", "none"),
                "implied_delta":      mv.get("implied_delta", 0.0),
                "odds_delta":         mv.get("odds_delta", 0.0),
                "alerts":             alerts,
            }
        except Exception as exc:
            logger.debug("VE: _load_prematch_meta fid=%s failed: %s", provider_fixture_id, exc)
            return {}

    def _compute_prematch_adjustment(
        self,
        prematch_meta: dict,
        quality_before: float,
    ) -> dict:
        """Compute penalty/boost from prematch odds signal.

        Returns: {penalty, boost, quality_after, warning}
        """
        if not prematch_meta:
            return {"penalty": 0.0, "boost": 0.0, "quality_after": quality_before, "warning": None}

        direction = prematch_meta.get("movement_direction", "stable")
        strength  = prematch_meta.get("movement_strength", "none")
        s_order   = _STRENGTH_ORDER.get(strength, 0)
        cfg       = settings

        penalty = 0.0
        boost   = 0.0
        warning = None

        if direction == "drifting":
            if s_order >= 3:   # high
                penalty = cfg.prematch_penalty_high_drift
                warning = f"prematch_drift_high"
            elif s_order >= 2: # medium
                penalty = cfg.prematch_penalty_medium_drift
                warning = f"prematch_drift_medium"
        elif direction == "shortening" and s_order >= 1:
            boost = cfg.prematch_boost_supporting_move

        net = max(0.0, penalty - boost)
        quality_after = round(max(0.0, min(1.0, quality_before - net)), 4)

        return {
            "penalty":       round(penalty, 4),
            "boost":         round(boost, 4),
            "quality_after": quality_after,
            "warning":       warning,
        }

    def _enrich_with_prematch(
        self,
        result: dict,
        conn,
        fix: dict,
        candidate: "PredictionCandidate",
    ) -> None:
        """Mutate result in-place with prematch signal fields. Never raises."""
        if not settings.prematch_intelligence_enabled:
            return

        provider_fixture_id = fix.get("provider_fixture_id")
        if not provider_fixture_id:
            return

        try:
            prematch_meta = self._load_prematch_meta(
                conn, provider_fixture_id, candidate.market, candidate.selection
            )
            if not prematch_meta:
                result["prematch_signal"] = None
                return

            quality_before = result.get("quality_score") or 0.0
            adj = self._compute_prematch_adjustment(prematch_meta, quality_before)

            result["prematch_signal"] = {
                "movement_direction": prematch_meta.get("movement_direction"),
                "movement_strength":  prematch_meta.get("movement_strength"),
                "implied_delta":      prematch_meta.get("implied_delta"),
                "prematch_penalty":   adj["penalty"],
                "prematch_boost":     adj["boost"],
                "alerts":             prematch_meta.get("alerts", []),
                "warning":            adj["warning"],
            }

            if adj["penalty"] > 0 or adj["boost"] > 0:
                result["quality_score"] = adj["quality_after"]

            if (
                settings.prematch_reject_high_risk
                and prematch_meta.get("movement_direction") == "drifting"
                and prematch_meta.get("movement_strength") == "high"
                and adj["quality_after"] < settings.value_engine_min_quality
                and result.get("value_engine_status") == STATUS_SELECTED
            ):
                result["value_engine_status"] = STATUS_REJECTED_PREMATCH
                result["rejection_reason"] = (
                    f"prematch_high_drift: quality_after={adj['quality_after']:.3f}"
                )

        except Exception as exc:
            logger.debug(
                "VE: _enrich_with_prematch fid=%s failed: %s", fix.get("id"), exc
            )

    # ── Phase 13: Strategy Learning integration ───────────────────────────────

    def _enrich_with_strategy(
        self,
        result: dict,
        conn,
        candidate: "PredictionCandidate",
        pick_r: dict,
    ) -> None:
        """Mutate result in-place with strategy learning metadata. Never raises.

        If STRATEGY_LEARNING_ENABLED=false (default): no-op.
        If STRATEGY_LEARNING_USE_FOR_SELECTION=false (default): metadata only, no score change.
        If STRATEGY_LEARNING_USE_FOR_SELECTION=true: may adjust quality_score within caps.
        """
        try:
            if not settings.strategy_learning_enabled:
                return

            from app.services.strategy_learning_service import get_strategy_for_pick
            strat = get_strategy_for_pick(
                conn,
                market_key=candidate.market,
                league_id=result.get("provider_league_id"),
                pick_odds_val=pick_r.get("offered_odds"),
                edge_val=pick_r.get("edge"),
                confidence_val=pick_r.get("p_cal"),
            )
            if strat is None:
                return

            result["strategy_key"]            = strat.get("strategy_key")
            result["strategy_score"]          = strat.get("strategy_score")
            result["strategy_recommendation"] = strat.get("recommendation")
            result["strategy_sample_size"]    = strat.get("sample_size")
            result["strategy_roi"]            = strat.get("roi")
            result["strategy_clv_beat_rate"]  = strat.get("clv_beat_rate")

            if not settings.strategy_learning_use_for_selection:
                return  # metadata only — do NOT change quality_score

            # Selection-affecting logic (only when explicitly enabled)
            rec    = strat.get("recommendation")
            sample = strat.get("sample_size") or 0
            score  = strat.get("strategy_score") or 0.0
            min_s  = settings.strategy_learning_min_sample

            if sample < min_s:
                return  # not enough data to trust

            current_quality = result.get("quality_score") or 0.0

            if rec == "promote" and score >= settings.strategy_learning_score_promote:
                boost = min(
                    settings.strategy_learning_max_boost,
                    (score - 70.0) / 100.0,
                )
                result["quality_score"] = round(
                    max(0.0, min(1.0, current_quality + boost)), 4
                )

            elif rec in ("reduce", "avoid") and score <= settings.strategy_learning_score_reduce:
                penalty = min(
                    settings.strategy_learning_max_penalty,
                    (50.0 - score) / 100.0,
                )
                result["quality_score"] = round(
                    max(0.0, min(1.0, current_quality - penalty)), 4
                )

                if (
                    rec == "avoid"
                    and sample >= min_s
                    and (strat.get("roi") or 0.0) < -0.10
                    and (strat.get("avg_clv_percent") or 0.0) < -1.0
                    and result.get("quality_score", 1.0) < settings.value_engine_min_quality
                    and result.get("value_engine_status") == STATUS_SELECTED
                ):
                    result["value_engine_status"] = "value_rejected_strategy"
                    result["rejection_reason"] = (
                        f"strategy_avoid: score={score:.1f} sample={sample}"
                    )

        except Exception as exc:
            logger.debug("VE: _enrich_with_strategy failed: %s", exc)

    # ── Phase 5: Availability integration ────────────────────────────────────

    def _load_fixture_availability(
        self,
        conn,
        provider_fixture_id: int,
        home_provider_id: int | None,
        away_provider_id: int | None,
    ) -> dict | None:
        """Load and cache DuckDB availability data for a fixture."""
        if provider_fixture_id in self._avail_cache:
            return self._avail_cache[provider_fixture_id]
        try:
            from app.data.local.availability_repo import get_availability_for_fixture
            data = get_availability_for_fixture(conn, provider_fixture_id)
            self._avail_cache[provider_fixture_id] = data
            return data
        except Exception as exc:
            logger.debug(
                "ValueEngine: availability lookup prov_fid=%s failed: %s",
                provider_fixture_id, exc,
            )
            self._avail_cache[provider_fixture_id] = None
            return None

    def _get_candidate_availability_context(
        self,
        avail_data: dict | None,
        market: str,
        selection: str,
        home_provider_id: int | None,
        away_provider_id: int | None,
    ) -> dict:
        """Determine modeled/opponent impact and coverage for a (market, selection) pair.

        Returns:
            {coverage, modeled_impact, opponent_impact, direction}
        """
        unknown = {
            "coverage": "unknown",
            "modeled_impact": "unknown",
            "opponent_impact": "unknown",
            "direction": "none",
        }
        if not avail_data:
            return unknown

        summaries = avail_data.get("summaries", {})
        if not summaries:
            return unknown

        any_summary = next(iter(summaries.values()), {})
        coverage = any_summary.get("coverage_status", "unknown")

        if market == "1X2":
            if selection == "Home":
                direction = "home"
            elif selection == "Away":
                direction = "away"
            else:
                direction = "none"
        else:
            direction = "both"

        home_summary = summaries.get(home_provider_id, {}) if home_provider_id else {}
        away_summary = summaries.get(away_provider_id, {}) if away_provider_id else {}
        home_impact  = home_summary.get("impact_label", "unknown")
        away_impact  = away_summary.get("impact_label", "unknown")

        if direction == "home":
            modeled_impact  = home_impact
            opponent_impact = away_impact
        elif direction == "away":
            modeled_impact  = away_impact
            opponent_impact = home_impact
        elif direction == "both":
            ord_h = _IMPACT_ORDER.get(home_impact, -1)
            ord_a = _IMPACT_ORDER.get(away_impact, -1)
            modeled_impact  = home_impact if ord_h >= ord_a else away_impact
            opponent_impact = "none"
        else:
            modeled_impact  = "none"
            opponent_impact = "none"

        return {
            "coverage":        coverage,
            "modeled_impact":  modeled_impact,
            "opponent_impact": opponent_impact,
            "direction":       direction,
        }

    def _compute_availability_adjustment(
        self,
        avail_ctx: dict,
        quality_before: float,
    ) -> dict:
        """Compute penalty/boost and adjusted quality_score.

        Returns:
            {penalty, boost, quality_after, warning}
        """
        coverage       = avail_ctx.get("coverage", "unknown")
        modeled_impact = avail_ctx.get("modeled_impact", "unknown")
        opponent_impact= avail_ctx.get("opponent_impact", "unknown")

        if coverage != "data":
            return {"penalty": 0.0, "boost": 0.0, "quality_after": quality_before, "warning": None}

        cfg = settings

        if modeled_impact == "high":
            penalty = cfg.value_engine_availability_high_penalty
        elif modeled_impact == "medium":
            penalty = cfg.value_engine_availability_medium_penalty
        elif modeled_impact == "low":
            penalty = cfg.value_engine_availability_low_penalty
        else:
            penalty = 0.0

        if opponent_impact == "high":
            boost = cfg.value_engine_availability_opponent_high_boost
        elif opponent_impact == "medium":
            boost = cfg.value_engine_availability_opponent_medium_boost
        else:
            boost = 0.0

        net = min(max(0.0, penalty - boost), cfg.value_engine_availability_max_penalty)
        quality_after = round(max(0.0, min(1.0, quality_before - net)), 4)

        warning = None
        if modeled_impact in ("high", "medium") and net > 0.0:
            warning = f"availability_{modeled_impact}_net={net:.3f}"

        return {
            "penalty":       round(penalty, 4),
            "boost":         round(boost, 4),
            "quality_after": quality_after,
            "warning":       warning,
        }

    def _enrich_with_availability(
        self,
        result: dict,
        conn,
        fix: dict,
        info: dict,
        candidate: "PredictionCandidate",
    ) -> None:
        """Mutate result in-place with availability penalty/boost fields.

        Never raises. If data is unavailable all availability fields stay None.
        """
        if not settings.value_engine_use_availability:
            return

        provider_fixture_id = fix.get("provider_fixture_id")
        if not provider_fixture_id:
            return

        home_pid = info.get("provider_home_team_id")
        away_pid = info.get("provider_away_team_id")

        try:
            avail_data = self._load_fixture_availability(
                conn, provider_fixture_id, home_pid, away_pid
            )
            avail_ctx = self._get_candidate_availability_context(
                avail_data, candidate.market, candidate.selection, home_pid, away_pid
            )

            quality_before = result.get("quality_score") or 0.0
            adj = self._compute_availability_adjustment(avail_ctx, quality_before)

            result["quality_before_availability"]  = quality_before
            result["availability_penalty"]         = adj["penalty"]
            result["availability_boost"]           = adj["boost"]
            result["availability_coverage"]        = avail_ctx["coverage"]
            result["availability_modeled_impact"]  = avail_ctx["modeled_impact"]
            result["availability_opponent_impact"] = avail_ctx["opponent_impact"]
            result["availability_warning"]         = adj["warning"]
            result["quality_score"]                = adj["quality_after"]

            if (
                settings.value_engine_reject_high_availability_risk
                and avail_ctx["coverage"] == "data"
                and avail_ctx["modeled_impact"] == "high"
                and adj["quality_after"] < settings.value_engine_min_quality
                and result.get("value_engine_status") == STATUS_SELECTED
            ):
                result["value_engine_status"] = STATUS_REJECTED_AVAILABILITY
                result["rejection_reason"] = (
                    f"high_availability_risk: quality_after={adj['quality_after']:.3f}"
                )

        except Exception as exc:
            logger.debug(
                "ValueEngine: _enrich_with_availability fid=%s failed: %s",
                fix.get("id"), exc,
            )

    # ── Main enrichment ───────────────────────────────────────────────────────

    def enrich_pick_candidates_with_value(
        self,
        all_enriched: list["PredictionCandidate"],
        fixture_map: dict[int, dict],
    ) -> dict[tuple, dict]:
        """Evaluate all candidates against the value engine.

        Args:
            all_enriched:  List of PredictionCandidate objects from OddsPredictor.
            fixture_map:   {fixtures.id (Supabase bigserial): fixture_dict}
                           with keys: id, provider_fixture_id, league_id,
                           home_team_id, away_team_id, kickoff_at, etc.

        Returns:
            {(fixture_id, market_key, selection): value_result_dict}
            Returns {} if mode is off or engine unavailable.
        """
        if not self.is_mode_active():
            return {}

        if not all_enriched:
            return {}

        # ── Bulk-load Supabase mappings ────────────────────────────────────
        cs_map = self._load_cs_map()
        all_team_ids: list[int] = []
        for fix in fixture_map.values():
            if fix.get("home_team_id"):
                all_team_ids.append(fix["home_team_id"])
            if fix.get("away_team_id"):
                all_team_ids.append(fix["away_team_id"])
        self._load_team_map(all_team_ids)

        # ── Per-fixture caches ─────────────────────────────────────────────
        provider_info: dict[int, dict] = {}   # {fid: resolved IDs}
        shadow_cache: dict[int, dict]  = {}   # {fid: predict_fixture result}
        results: dict[tuple, dict]     = {}

        for candidate in all_enriched:
            fid = candidate.fixture_id
            key = (fid, candidate.market, candidate.selection)

            fix = fixture_map.get(fid)
            if fix is None:
                results[key] = _err("fixture_not_in_map")
                continue

            # ── Resolve provider IDs (once per fixture) ────────────────────
            if fid not in provider_info:
                cs_info   = cs_map.get(fix.get("league_id")) or {}
                prov_lid  = cs_info.get("provider_league_id")
                lg_name   = cs_info.get("league_name") or ""
                home_info = self._team_map.get(fix.get("home_team_id")) or {}
                away_info = self._team_map.get(fix.get("away_team_id")) or {}
                prov_home = home_info.get("provider_team_id")
                prov_away = away_info.get("provider_team_id")

                entity_type      = "club"
                competition_type = None
                if prov_lid is not None:
                    conn = self._get_conn()
                    if conn is not None:
                        try:
                            from app.data.local.history_repo import get_competition_context
                            ctx = get_competition_context(conn, prov_lid)
                            if ctx:
                                entity_type      = ctx["entity_scope"]
                                competition_type = ctx["competition_type"]
                        except Exception as exc:
                            logger.debug(
                                "ValueEngine: ctx lookup falló liga=%s: %s", prov_lid, exc
                            )

                provider_info[fid] = {
                    "provider_league_id":    prov_lid,
                    "league_name":           lg_name,
                    "provider_home_team_id": prov_home,
                    "provider_away_team_id": prov_away,
                    "entity_type":           entity_type,
                    "competition_type":      competition_type,
                }

            info     = provider_info[fid]
            prov_lid = info["provider_league_id"]

            # ── Run predict_fixture (once per fixture) ─────────────────────
            if fid not in shadow_cache:
                if prov_lid is None:
                    shadow_cache[fid] = {"error": "no_provider_league_id"}
                elif info["provider_home_team_id"] is None or info["provider_away_team_id"] is None:
                    shadow_cache[fid] = {"error": "missing_team_provider_id"}
                else:
                    conn = self._get_conn()
                    if conn is None:
                        shadow_cache[fid] = {"error": "duckdb_unavailable"}
                    else:
                        try:
                            from app.services.shadow_service import predict_fixture
                            ko_date = (fix.get("kickoff_at") or "")[:10] or None
                            shadow_cache[fid] = predict_fixture(
                                conn,
                                league_id=prov_lid,
                                home_team_id=info["provider_home_team_id"],
                                away_team_id=info["provider_away_team_id"],
                                fixture_date=ko_date,
                            )
                        except Exception as exc:
                            logger.warning(
                                "ValueEngine: predict_fixture falló fid=%s: %s", fid, exc
                            )
                            shadow_cache[fid] = {"error": str(exc)[:80]}

            shadow = shadow_cache[fid]
            if "error" in shadow:
                err = shadow["error"]
                if "no_provider" in err or "missing_team" in err:
                    results[key] = _skip(STATUS_MISSING_HISTORY, err)
                else:
                    results[key] = _err(err)
                continue

            # ── Get p_raw for this specific (market, selection) ────────────
            mkt_data  = shadow["picks"].get(candidate.market, {})
            all_probs = mkt_data.get("all_probs", {})
            p_raw     = all_probs.get(candidate.selection)

            if p_raw is None:
                results[key] = _skip(
                    STATUS_MISSING_HISTORY,
                    f"no_p_raw_{candidate.market}_{candidate.selection}",
                )
                continue

            # ── Evaluate with calibrator ───────────────────────────────────
            conn = self._get_conn()
            if conn is None:
                results[key] = _err("duckdb_unavailable_at_eval")
                continue

            offered = candidate.best_odd if candidate.best_odd > 1.0 else None

            try:
                from app.services.value_betting_service import _evaluate_pick
                pick_r = _evaluate_pick(
                    conn,
                    market_key=candidate.market,
                    selection=candidate.selection,
                    p_raw=p_raw,
                    entity_type=info["entity_type"],
                    competition_type=info["competition_type"],
                    provider_league_id=prov_lid,
                    offered_odds=offered,
                )
            except Exception as exc:
                logger.warning(
                    "ValueEngine: _evaluate_pick falló fid=%s %s/%s: %s",
                    fid, candidate.market, candidate.selection, exc,
                )
                results[key] = _err(str(exc)[:80])
                continue

            # ── Calibrator scope ───────────────────────────────────────────
            if not pick_r["_calibrated"]:
                calibrator_scope = "none"
            else:
                try:
                    specific = conn.execute(
                        "SELECT 1 FROM calibration_registry "
                        "WHERE market_key=? AND entity_type=? AND provider_league_id=? LIMIT 1",
                        [candidate.market, info["entity_type"], prov_lid],
                    ).fetchone()
                    calibrator_scope = "league_specific" if specific else "global"
                except Exception:
                    calibrator_scope = "global"

            # ── Status determination ───────────────────────────────────────
            rejection_reason: str | None = None
            if not pick_r["_calibrated"]:
                status           = STATUS_MISSING_CALIBRATOR
                rejection_reason = "no_calibrator"
            elif pick_r["quality_score"] < settings.value_engine_min_quality:
                status           = STATUS_LOW_QUALITY
                rejection_reason = (
                    f"quality={pick_r['quality_score']:.3f}"
                    f"<{settings.value_engine_min_quality}"
                )
            elif settings.value_engine_require_odds and offered is None:
                status           = STATUS_MISSING_ODDS
                rejection_reason = "no_offered_odds"
            elif (
                settings.value_engine_require_odds
                and pick_r.get("edge") is not None
                and pick_r["edge"] < settings.value_engine_min_edge
            ):
                status           = STATUS_LOW_EDGE
                rejection_reason = (
                    f"edge={pick_r['edge']:.4f}<{settings.value_engine_min_edge}"
                )
            elif (
                settings.value_engine_require_odds
                and pick_r.get("ev_adj") is not None
                and pick_r["ev_adj"] < settings.value_engine_min_ev_adj
            ):
                status           = STATUS_LOW_EDGE
                rejection_reason = (
                    f"ev_adj={pick_r['ev_adj']:.4f}<{settings.value_engine_min_ev_adj}"
                )
            else:
                status = STATUS_SELECTED

            meta       = pick_r.get("_meta") or {}
            rank_score = (
                pick_r.get("ev_adj")
                or pick_r.get("ev")
                or pick_r.get("quality_score")
                or 0.0
            )

            results[key] = {
                "value_engine_status":    status,
                "p_raw":                  pick_r["p_raw"],
                "p_cal":                  pick_r["p_cal"],
                "fair_odds":              pick_r["fair_odds"],
                "p_mkt":                  pick_r["p_mkt"],
                "edge":                   pick_r["edge"],
                "ev":                     pick_r["ev"],
                "ev_adj":                 pick_r["ev_adj"],
                "w_rel":                  pick_r["w_rel"],
                "quality_score":          pick_r["quality_score"],
                "rejection_reason":       rejection_reason,
                "model_scope":            shadow.get("entity_scope", "club"),
                "calibrator_scope":       calibrator_scope,
                "historical_sample_size": meta.get("n_train"),
                "value_rank_score":       rank_score,
                "provider_league_id":     prov_lid,
                # Phase 5: filled by _enrich_with_availability (None until then)
                "quality_before_availability":  None,
                "availability_penalty":         None,
                "availability_boost":           None,
                "availability_coverage":        None,
                "availability_modeled_impact":  None,
                "availability_opponent_impact": None,
                "availability_warning":         None,
                # Phase 6: filled by _enrich_with_prematch (None until then)
                "prematch_signal":              None,
                # Phase 13: filled by _enrich_with_strategy (None until then)
                "strategy_key":                 None,
                "strategy_score":               None,
                "strategy_recommendation":      None,
                "strategy_sample_size":         None,
                "strategy_roi":                 None,
                "strategy_clv_beat_rate":       None,
            }

            conn = self._get_conn()
            if conn is not None:
                self._enrich_with_availability(results[key], conn, fix, info, candidate)
                self._enrich_with_prematch(results[key], conn, fix, candidate)
                self._enrich_with_strategy(results[key], conn, candidate, pick_r)

        n_ok  = sum(1 for v in results.values() if v.get("value_engine_status") == STATUS_SELECTED)
        n_err = sum(1 for v in results.values() if v.get("value_engine_status") == STATUS_ERROR)
        logger.info(
            "ValueEngine [%s]: %d candidatos evaluados → %d value_selected, %d errors",
            settings.value_engine_mode, len(results), n_ok, n_err,
        )
        return results

    # ── Assist mode: filter + rank ────────────────────────────────────────────

    def rank_candidates_with_value(
        self,
        candidates: list["PredictionCandidate"],
        value_results: dict[tuple, dict],
    ) -> list["PredictionCandidate"]:
        """Return value_selected candidates sorted by value_rank_score desc.

        Applies VALUE_ENGINE_MAX_PICKS_PER_LEAGUE cap.
        Returns [] if no candidates pass (caller decides whether to fall back).
        """
        selected_triples: list[tuple] = []
        for c in candidates:
            key = (c.fixture_id, c.market, c.selection)
            vr  = value_results.get(key, {})
            if vr.get("value_engine_status") == STATUS_SELECTED:
                rank = vr.get("value_rank_score") or 0.0
                selected_triples.append((rank, c, vr))

        if not selected_triples:
            return []

        selected_triples.sort(key=lambda x: x[0], reverse=True)

        max_per_league = settings.value_engine_max_picks_per_league
        league_counts: dict[int | None, int] = {}
        result: list["PredictionCandidate"] = []

        for _rank, c, vr in selected_triples:
            lid   = vr.get("provider_league_id")
            count = league_counts.get(lid, 0)
            if max_per_league > 0 and count >= max_per_league:
                logger.debug(
                    "ValueEngine: cap per_league=%d alcanzado para liga=%s, "
                    "omitiendo fixture=%s %s/%s",
                    max_per_league, lid, c.fixture_id, c.market, c.selection,
                )
                continue
            league_counts[lid] = count + 1
            result.append(c)

        return result

    # ── Shadow mode: comparison logging ──────────────────────────────────────

    def log_shadow_comparison(
        self,
        all_enriched: list["PredictionCandidate"],
        value_results: dict[tuple, dict],
    ) -> None:
        """Log old-system vs value-engine comparison for every evaluated candidate."""
        for c in all_enriched:
            key = (c.fixture_id, c.market, c.selection)
            vr  = value_results.get(key)
            if vr is None:
                continue
            ev_adj_str = (
                f"{vr['ev_adj']:+.4f}" if vr.get("ev_adj") is not None else "N/A"
            )
            logger.info(
                "VE_shadow fid=%s %s/%s | "
                "current: edge=%.4f conf=%.4f | "
                "value: status=%s p_cal=%.4f q=%.4f ev_adj=%s",
                c.fixture_id, c.market, c.selection,
                c.edge, c.confidence_score,
                vr["value_engine_status"],
                vr.get("p_cal") or 0.0,
                vr.get("quality_score") or 0.0,
                ev_adj_str,
            )


# ── Singleton ─────────────────────────────────────────────────────────────────

value_engine_adapter = ValueEngineLiveAdapter()
