"""Phase 8: Smart Parlay Engine.

Loads today's eligible picks, generates 2/3/4-leg combinations,
scores by EV / risk / correlation, and stores top candidates in DuckDB.

Entrypoint: run_parlay_engine()
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from itertools import combinations

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Correlation constants ──────────────────────────────────────────────────────

_CORR_SAME_FIXTURE    = 0.80
_CORR_SAME_TEAM       = 0.40
_CORR_OVER_BTTS_YES   = 0.30   # Over 2.5 + BTTS Yes same fixture
_CORR_UNDER_BTTS_NO   = 0.30   # Under 2.5 + BTTS No same fixture
_CORR_HOME_OVER       = 0.20   # Home win + Over same fixture
_CORR_SAME_LEAGUE     = 0.08   # per extra pick beyond first
_CORR_SAME_MARKET     = 0.08   # per extra pick beyond second
_CORR_AWAY_HEAVY      = 0.10   # >50% Away selections

# ── Probability & EV formulas ─────────────────────────────────────────────────


def compute_joint_probability(legs: list[dict]) -> float:
    """Product of calibrated (or model) probabilities for all legs."""
    prob = 1.0
    for leg in legs:
        p = float(leg.get("p_cal") or leg.get("p_model") or 0.5)
        prob *= p
    return prob


def compute_total_odds(legs: list[dict]) -> float:
    """Product of best odds for all legs."""
    odds = 1.0
    for leg in legs:
        o = float(leg.get("odds") or 1.0)
        odds *= o
    return odds


def compute_parlay_ev(joint_prob: float, total_odds: float) -> float:
    """EV = joint_probability * total_odds - 1."""
    return joint_prob * total_odds - 1.0


# ── Intra-fixture market correlation helpers ───────────────────────────────────


def _is_over_btts_yes_pair(m1: str, s1: str, m2: str, s2: str) -> bool:
    return (
        (m1 == "OU25" and "Over" in s1 and m2 == "BTTS" and s2 == "Yes") or
        (m2 == "OU25" and "Over" in s2 and m1 == "BTTS" and s1 == "Yes")
    )


def _is_under_btts_no_pair(m1: str, s1: str, m2: str, s2: str) -> bool:
    return (
        (m1 == "OU25" and "Under" in s1 and m2 == "BTTS" and s2 == "No") or
        (m2 == "OU25" and "Under" in s2 and m1 == "BTTS" and s1 == "No")
    )


def _is_home_over_pair(m1: str, s1: str, m2: str, s2: str) -> bool:
    return (
        (m1 == "1X2" and s1 == "Home" and m2 == "OU25" and "Over" in s2) or
        (m2 == "1X2" and s2 == "Home" and m1 == "OU25" and "Over" in s1)
    )


# ── Scoring ────────────────────────────────────────────────────────────────────


def compute_correlation_score(legs: list[dict]) -> float:
    """Return correlation score in [0, 1]. Higher = more correlated (worse)."""
    score     = 0.0
    fixtures: dict[object, list[dict]] = {}
    teams:    set[object] = set()
    leagues:  dict[object, int] = {}
    markets:  dict[str, int] = {}

    for leg in legs:
        pfid = leg.get("provider_fixture_id")
        home = leg.get("home_team_id")
        away = leg.get("away_team_id")
        lid  = leg.get("league_id")
        mkt  = leg.get("market_key", "")
        sel  = leg.get("selection", "")

        # Same fixture
        if pfid is not None:
            if pfid in fixtures:
                score += _CORR_SAME_FIXTURE
                for prev in fixtures[pfid]:
                    pm, ps = prev.get("market_key", ""), prev.get("selection", "")
                    if _is_over_btts_yes_pair(mkt, sel, pm, ps):
                        score += _CORR_OVER_BTTS_YES
                    if _is_under_btts_no_pair(mkt, sel, pm, ps):
                        score += _CORR_UNDER_BTTS_NO
                    if _is_home_over_pair(mkt, sel, pm, ps):
                        score += _CORR_HOME_OVER
                fixtures[pfid].append(leg)
            else:
                fixtures[pfid] = [leg]

        # Same team
        for tid in filter(None, [home, away]):
            if tid in teams:
                score += _CORR_SAME_TEAM
            else:
                teams.add(tid)

        # Same league (penalty from 2nd pick onwards)
        if lid is not None:
            leagues[lid] = leagues.get(lid, 0) + 1
            if leagues[lid] > 1:
                score += _CORR_SAME_LEAGUE

        # Same market (penalty from 3rd pick onwards)
        markets[mkt] = markets.get(mkt, 0) + 1
        if markets[mkt] > 2:
            score += _CORR_SAME_MARKET

    # Away-heavy penalty
    away_count = sum(1 for l in legs if l.get("selection") == "Away")
    if away_count > len(legs) / 2:
        score += _CORR_AWAY_HEAVY

    return min(1.0, score)


def compute_risk_score(legs: list[dict], correlation_score: float) -> float:
    """Return risk score in [0, 1]. Higher = riskier."""
    risk = correlation_score * 0.45

    # More legs = more variance
    risk += (len(legs) - 2) * 0.07

    # Availability penalties from value engine meta
    for leg in legs:
        meta  = (leg.get("value_engine_meta") or {})
        avail = (meta.get("availability") or {}) if isinstance(meta, dict) else {}
        impact = avail.get("modeled_impact", "none")
        if impact == "high":
            risk += 0.15
        elif impact == "medium":
            risk += 0.07

    # Prematch drift penalties
    for leg in legs:
        meta = (leg.get("value_engine_meta") or {})
        pm   = (meta.get("prematch") or {}) if isinstance(meta, dict) else {}
        if pm.get("movement_direction") == "drifting":
            s = pm.get("movement_strength", "none")
            if s == "high":
                risk += 0.12
            elif s == "medium":
                risk += 0.06

    return min(1.0, risk)


def compute_confidence_score(legs: list[dict], joint_prob: float, edge: float) -> float:
    """Return confidence score in [0, 1]."""
    qs_vals  = [float(l.get("quality_score") or 0.5) for l in legs]
    avg_qs   = sum(qs_vals) / len(qs_vals) if qs_vals else 0.5
    pub_frac = sum(1 for l in legs if l.get("is_publishable")) / len(legs)
    edge_contrib = min(1.0, max(0.0, edge / 0.15))
    return min(1.0, avg_qs * 0.5 + pub_frac * 0.3 + edge_contrib * 0.2)


# ── Validation rules ───────────────────────────────────────────────────────────


def validate_parlay_rules(legs: list[dict]) -> tuple[bool, str]:
    """Return (is_valid, rejection_reason). rejection_reason='OK' means valid."""
    # 1. Same fixture check
    if not settings.parlay_allow_same_fixture:
        pfids = [l.get("provider_fixture_id") for l in legs if l.get("provider_fixture_id") is not None]
        if len(pfids) != len(set(pfids)):
            return False, "rejected_same_fixture"

    # 2. Same team check
    teams: set = set()
    for leg in legs:
        for tid in filter(None, [leg.get("home_team_id"), leg.get("away_team_id")]):
            if tid in teams:
                return False, "rejected_same_team"
            teams.add(tid)

    # 3. Max same league
    leagues: dict = {}
    for leg in legs:
        lid = leg.get("league_id")
        if lid is not None:
            leagues[lid] = leagues.get(lid, 0) + 1
            if leagues[lid] > settings.parlay_max_same_league:
                return False, "rejected_max_same_league"

    # 4. Positive edge per leg
    for leg in legs:
        if (leg.get("edge") or 0) <= 0:
            return False, "rejected_non_positive_edge"

    # 5. Valid odds per leg
    for leg in legs:
        if not leg.get("odds") or float(leg["odds"] or 0) <= 1.0:
            return False, "rejected_no_odds"

    return True, "OK"


def _determine_recommendation_status(
    ev: float,
    edge: float,
    risk: float,
    correlation: float,
    confidence: float,
) -> str:
    cfg = settings
    if ev < cfg.parlay_min_ev or edge < cfg.parlay_min_edge:
        return "rejected_low_ev"
    if correlation > cfg.parlay_max_correlation:
        return "rejected_high_correlation"
    if risk > cfg.parlay_max_risk:
        return "rejected_high_risk"
    if confidence < cfg.parlay_min_confidence:
        return "rejected_low_quality"
    return "recommended"


def _parlay_key(date_str: str, legs: list[dict]) -> str:
    pick_ids = sorted(str(l["pick_candidate_id"]) for l in legs)
    return f"{date_str}_{'_'.join(pick_ids)}"


# ── Pick loading ───────────────────────────────────────────────────────────────


def _load_picks_from_supabase(include_observed: bool = False) -> list[dict]:
    """Load and normalize picks eligible for parlay construction."""
    from app.data.repositories.fixture_repo import get_fixtures_today
    from app.data.repositories.prediction_repo import get_candidates_for_fixtures

    fixtures = get_fixtures_today()
    if not fixtures:
        return []

    fix_map  = {f["id"]: f for f in fixtures}
    fix_ids  = list(fix_map.keys())
    candidates = get_candidates_for_fixtures(fix_ids)
    now = datetime.now(timezone.utc)
    result: list[dict] = []

    for pick in candidates:
        fix = fix_map.get(pick.get("fixture_id"))
        if not fix:
            continue

        is_pub = pick.get("is_publishable", False)
        if not include_observed and not is_pub:
            continue

        ko_str = fix.get("kickoff_at")
        if ko_str:
            try:
                ko = datetime.fromisoformat(ko_str.replace("Z", "+00:00"))
                if ko <= now:
                    continue  # match has already started
            except ValueError:
                continue

        # Resolve odds from argument_json fallback
        arg  = pick.get("argument_json") or {}
        best_odd = pick.get("best_odd") or arg.get("best_odd")
        if not best_odd:
            imp = pick.get("implied_probability") or 0
            best_odd = (1.0 / imp) if imp > 0 else None

        edge     = pick.get("value_edge")     if pick.get("value_edge") is not None else pick.get("edge") or 0
        p_cal    = pick.get("value_p_cal")    if pick.get("value_p_cal") is not None else pick.get("model_probability") or 0
        quality  = pick.get("value_quality_score") if pick.get("value_quality_score") is not None else pick.get("confidence_score") or 0

        result.append({
            "pick_candidate_id":   pick["id"],
            "fixture_id":          pick["fixture_id"],
            "provider_fixture_id": fix.get("provider_fixture_id"),
            "league_id":           fix.get("league_id"),
            "home_team_id":        fix.get("home_team_id"),
            "away_team_id":        fix.get("away_team_id"),
            "kickoff_at":          ko_str,
            "market_key":          pick.get("market_key", ""),
            "selection":           pick.get("selection", ""),
            "odds":                best_odd,
            "p_model":             pick.get("model_probability") or 0,
            "p_cal":               p_cal,
            "edge":                edge,
            "quality_score":       quality,
            "is_publishable":      is_pub,
            "value_engine_meta":   pick.get("value_engine_meta") or {},
        })

    return result


def _filter_eligible(picks: list[dict]) -> list[dict]:
    """Per-pick eligibility filter."""
    eligible = []
    for pick in picks:
        if not pick.get("odds") or float(pick.get("odds") or 0) <= 1.0:
            continue
        if (pick.get("edge") or 0) <= 0:
            continue
        if (pick.get("quality_score") or 0) < settings.parlay_min_confidence:
            continue
        meta  = (pick.get("value_engine_meta") or {})
        avail = (meta.get("availability") or {}) if isinstance(meta, dict) else {}
        if avail.get("modeled_impact") == "high":
            continue
        pm = (meta.get("prematch") or {}) if isinstance(meta, dict) else {}
        if pm.get("movement_direction") == "drifting" and pm.get("movement_strength") == "high":
            continue
        eligible.append(pick)
    return eligible


# ── Combination generation ────────────────────────────────────────────────────


def generate_parlay_candidates(
    picks: list[dict],
    n_legs_list: list[int],
    date_str: str,
    max_per_size: int = 100,
) -> list[dict]:
    """Generate, score, and rank all valid parlay combinations.

    Returns list of parlay dicts (unsaved). Each has a 'legs' key with the
    list of normalized pick dicts.
    """
    _TYPE_MAP = {2: "conservadora", 3: "balanceada", 4: "agresiva"}
    all_parlays: list[dict] = []

    for n in n_legs_list:
        if n < 2 or n > settings.parlay_max_legs:
            continue
        scored: list[dict] = []

        for combo in combinations(picks, n):
            legs = list(combo)

            is_valid, reason = validate_parlay_rules(legs)
            if not is_valid:
                continue

            corr   = compute_correlation_score(legs)
            joint  = compute_joint_probability(legs)
            odds   = compute_total_odds(legs)
            impl   = 1.0 / odds if odds > 0 else 1.0
            edge   = joint - impl
            ev     = compute_parlay_ev(joint, odds)
            risk   = compute_risk_score(legs, corr)
            conf   = compute_confidence_score(legs, joint, edge)
            status = _determine_recommendation_status(ev, edge, risk, corr, conf)
            pkey   = _parlay_key(date_str, legs)

            scored.append({
                "parlay_key":             pkey,
                "date":                   date_str,
                "parlay_type":            _TYPE_MAP.get(n, f"{n}_legs"),
                "legs_count":             n,
                "total_odds":             round(odds, 4),
                "joint_probability":      round(joint, 4),
                "implied_probability":    round(impl, 4),
                "edge":                   round(edge, 4),
                "ev":                     round(ev, 4),
                "risk_score":             round(risk, 4),
                "correlation_score":      round(corr, 4),
                "confidence_score":       round(conf, 4),
                "recommendation_status":  status,
                "rejection_reason":       status if "rejected" in status else None,
                "legs":                   legs,
            })

        scored.sort(key=lambda x: (-x["ev"], -x["confidence_score"]))
        all_parlays.extend(scored[:max_per_size])

    return all_parlays


# ── Settlement ────────────────────────────────────────────────────────────────


def settle_parlays(conn, dry_run: bool = True) -> dict:
    """Settle all parlay_candidates that have all legs resolved in pick_results.

    Returns stats dict.
    """
    from app.data.local import parlay_repo as repo
    from app.data.repositories.supabase_client import get_supabase

    stats = {"checked": 0, "settled": 0, "pending": 0, "already_settled": 0, "errors": []}

    # Load unsettled parlays
    unsettled = conn.execute(
        """
        SELECT pc.parlay_key, pc.legs_count, pc.total_odds
        FROM parlay_candidates pc
        LEFT JOIN parlay_results pr ON pc.parlay_key = pr.parlay_key
        WHERE pr.parlay_key IS NULL
           OR pr.result_status = 'pending'
        """
    ).fetchall()

    if not unsettled:
        return stats

    client = get_supabase()

    for parlay_key, legs_count, total_odds in unsettled:
        stats["checked"] += 1
        try:
            legs = repo.get_parlay_legs(conn, parlay_key)
            if not legs:
                continue

            pick_ids = [l["pick_candidate_id"] for l in legs if l.get("pick_candidate_id")]
            if not pick_ids:
                continue

            # Query pick_results for each leg
            resp = (
                client.table("pick_results")
                .select("pick_candidate_id, result_status, profit_units, odd_taken")
                .in_("pick_candidate_id", pick_ids)
                .execute()
            )
            result_map = {r["pick_candidate_id"]: r for r in (resp.data or [])}

            # Check if all legs are settled
            settled_legs = [result_map.get(pid) for pid in pick_ids]
            if any(r is None or r["result_status"] == "pending" for r in settled_legs):
                stats["pending"] += 1
                continue

            # Determine parlay outcome
            legs_won  = sum(1 for r in settled_legs if r and r["result_status"] == "win")
            legs_lost = sum(1 for r in settled_legs if r and r["result_status"] == "loss")
            legs_void = sum(1 for r in settled_legs if r and r["result_status"] == "void")

            if legs_lost > 0:
                result_status = "loss"
                effective_odds = total_odds or 1.0
                profit = settings.parlay_default_stake_units * (0 - 1)
            elif legs_void > 0 and legs_lost == 0:
                # Recalculate without void legs
                non_void = [l for l, r in zip(legs, settled_legs)
                            if r and r["result_status"] != "void"]
                if not non_void:
                    result_status = "void"
                    profit = 0.0
                    effective_odds = 1.0
                else:
                    effective_odds = 1.0
                    for l in non_void:
                        effective_odds *= float(l.get("odds") or 1.0)
                    result_status = "win"
                    profit = settings.parlay_default_stake_units * (effective_odds - 1)
            else:
                result_status = "win"
                effective_odds = total_odds or 1.0
                profit = settings.parlay_default_stake_units * (effective_odds - 1)

            roi = profit / settings.parlay_default_stake_units * 100 if settings.parlay_default_stake_units else 0.0

            if not dry_run:
                repo.upsert_parlay_result(conn, {
                    "parlay_key":    parlay_key,
                    "result_status": result_status,
                    "legs_won":      legs_won,
                    "legs_lost":     legs_lost,
                    "legs_void":     legs_void,
                    "stake_units":   settings.parlay_default_stake_units,
                    "profit_units":  round(profit, 4),
                    "roi":           round(roi, 2),
                })

            stats["settled"] += 1
        except Exception as exc:
            logger.warning("settle_parlays: error for %s — %s", parlay_key, exc)
            stats["errors"].append(str(exc))

    return stats


# ── Main engine entry point ────────────────────────────────────────────────────


def run_parlay_engine(
    date_str: str | None = None,
    n_legs_list: list[int] | None = None,
    include_observed: bool = False,
    dry_run: bool = True,
    execute: bool = False,
    limit: int = 20,
    verbose: bool = False,
) -> dict:
    """Generate parlay candidates from today's eligible picks.

    Returns stats dict with keys: picks_loaded, picks_eligible,
    parlays_generated, parlays_recommended, parlays_saved, errors.
    """
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local import parlay_repo as repo

    _exec = execute and not dry_run
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if n_legs_list is None:
        n_legs_list = [2, 3, 4]

    stats: dict = {
        "date":                date_str,
        "picks_loaded":        0,
        "picks_eligible":      0,
        "parlays_generated":   0,
        "parlays_recommended": 0,
        "parlays_observed":    0,
        "parlays_rejected":    0,
        "parlays_saved":       0,
        "errors":              [],
    }

    try:
        picks = _load_picks_from_supabase(include_observed)
        stats["picks_loaded"] = len(picks)
    except Exception as exc:
        logger.error("run_parlay_engine: failed to load picks — %s", exc)
        stats["errors"].append(str(exc))
        return stats

    eligible = _filter_eligible(picks)
    stats["picks_eligible"] = len(eligible)

    if len(eligible) < 2:
        logger.info("run_parlay_engine: not enough eligible picks (%d)", len(eligible))
        return stats

    parlays = generate_parlay_candidates(eligible, n_legs_list, date_str, max_per_size=100)
    stats["parlays_generated"]   = len(parlays)
    stats["parlays_recommended"] = sum(1 for p in parlays if p["recommendation_status"] == "recommended")
    stats["parlays_observed"]    = sum(1 for p in parlays if p["recommendation_status"] == "observed")
    stats["parlays_rejected"]    = sum(1 for p in parlays if "rejected" in p["recommendation_status"])

    if _exec:
        conn = get_local_db()
        init_schema(conn)
        top = [p for p in parlays if p["recommendation_status"] in ("recommended", "observed")]
        top = top[:limit]
        rejected_sample = [p for p in parlays if "rejected" in p["recommendation_status"]][:5]
        to_save = top + rejected_sample

        for parlay in to_save:
            try:
                repo.insert_parlay_candidate(conn, parlay)
                repo.insert_parlay_legs(conn, parlay["parlay_key"], parlay["legs"])
                stats["parlays_saved"] += 1
            except Exception as exc:
                logger.warning("parlay save failed %s — %s", parlay["parlay_key"], exc)

    return stats


# ── Bot-facing read helpers ───────────────────────────────────────────────────


def get_best_parlays_today(conn, n_legs: int | None = None) -> list[dict]:
    """Return top recommended parlays for today, optionally by leg count."""
    from app.data.local import parlay_repo as repo

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    candidates = repo.get_parlay_candidates(conn, date=today, status="recommended", limit=20)

    if n_legs:
        candidates = [c for c in candidates if c.get("legs_count") == n_legs]

    result = []
    for cand in candidates[:5]:
        cand["legs"] = repo.get_parlay_legs(conn, cand["parlay_key"])
        result.append(cand)
    return result


def get_parlay_engine_status(conn) -> dict:
    """Return status dict for /estado integration."""
    from app.data.local import parlay_repo as repo

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        counts = repo.parlay_counts(conn)
        today_recs = len(repo.get_parlay_candidates(conn, date=today, status="recommended", limit=100))
        today_total = len(repo.get_parlay_candidates(conn, date=today, limit=100))
        summary = repo.get_recent_parlay_summary(conn)
        return {
            "enabled":       settings.parlay_engine_enabled,
            "today_total":   today_total,
            "today_recommended": today_recs,
            "table_counts":  counts,
            "performance":   summary,
        }
    except Exception as exc:
        return {"enabled": settings.parlay_engine_enabled, "error": str(exc)}
