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

_EMPTY: dict = {
    "value_engine_status":    STATUS_OFF,
    "p_raw":                  None,
    "p_cal":                  None,
    "fair_odds":              None,
    "p_mkt":                  None,
    "edge":                   None,
    "ev":                     None,
    "ev_adj":                 None,
    "w_rel":                  None,
    "quality_score":          None,
    "rejection_reason":       None,
    "model_scope":            None,
    "calibrator_scope":       None,
    "historical_sample_size": None,
    "value_rank_score":       None,
    "provider_league_id":     None,
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
        """Clear per-run Supabase mapping caches (call between daily runs if needed)."""
        self._cs_map = None
        self._team_map = {}

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
            }

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
