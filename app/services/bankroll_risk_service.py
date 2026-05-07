"""Phase 14: Bankroll, Stake Sizing & Risk Portfolio Engine.

All functions are SYNC (no async). No API calls. DuckDB-only.

Design:
  - BANKROLL_ENGINE_ENABLED=false -> entire module is a no-op.
  - BANKROLL_USE_FOR_SELECTION=false (default) -> metadata only, picks unchanged.
  - If true -> can reduce priority of high-risk picks (never increases them).
  - All recommendations are informational. Never gives financial advice.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_CORR_SAME_FIXTURE = 0.80
_CORR_SAME_TEAM    = 0.40
_CORR_SAME_LEAGUE  = 0.08
_CORR_RELATED_MKT  = 0.30

_RELATED_MARKET_PAIRS: list[tuple] = [
    (("OU25", "Over 2.5"),   ("BTTS", "Yes")),
    (("OU25", "Under 2.5"),  ("BTTS", "No")),
    (("1X2",  "Home"),       ("DC",   "1X")),
    (("1X2",  "Away"),       ("DC",   "X2")),
    (("1X2",  "Home"),       ("1X2",  "Home")),   # same mkt/sel treated as high corr
]


# ── A) Kelly & stake helpers ──────────────────────────────────────────────────

def compute_full_kelly(p_model: float, odds: float) -> float:
    """Full Kelly fraction: (p*b - q) / b where b = odds - 1."""
    if odds <= 1.0 or p_model <= 0.0 or p_model >= 1.0:
        return 0.0
    implied = 1.0 / odds
    if p_model <= implied:
        return 0.0
    b = odds - 1.0
    return (p_model * b - (1.0 - p_model)) / b


def compute_fractional_kelly(full_kelly: float, fraction: float) -> float:
    """Apply fractional Kelly to reduce variance."""
    return max(0.0, min(full_kelly * max(0.0, fraction), 1.0))


def cap_stake_units(units: float, max_pick_risk_units: float) -> float:
    return min(max(0.0, units), max(0.0, max_pick_risk_units))


def stake_label(units: float) -> str:
    if units <= 0.0:
        return "no_stake"
    if units < 0.5:
        return "micro"
    if units < 1.0:
        return "small"
    if units < 1.5:
        return "medium"
    return "large"


# ── B) Risk score ─────────────────────────────────────────────────────────────

def compute_risk_score(pick: dict, profile: dict | None = None) -> float:
    """Risk score 0–100.  Higher = riskier pick."""
    score = 0.0

    # Odds variance contribution
    odds = float(pick.get("odds") or 2.0)
    if odds >= 4.0:
        score += 25.0
    elif odds >= 3.0:
        score += 15.0
    elif odds >= 2.5:
        score += 8.0
    elif odds >= 2.0:
        score += 3.0

    # Low confidence
    conf = float(pick.get("confidence_score") or pick.get("p_model") or 0.5)
    if conf < 0.53:
        score += 20.0
    elif conf < 0.57:
        score += 12.0
    elif conf < 0.62:
        score += 6.0

    # Strategy signal
    strat_rec = pick.get("strategy_recommendation")
    strat_sc  = pick.get("strategy_score")
    if strat_rec == "avoid":
        score += 30.0
    elif strat_rec == "reduce":
        score += 18.0
    elif strat_sc is not None:
        if float(strat_sc) < 35.0:
            score += 12.0
        elif float(strat_sc) < 50.0:
            score += 6.0

    # Insufficient strategy sample
    strat_n = int(pick.get("strategy_sample_size") or 0)
    if strat_n == 0:
        score += 8.0
    elif strat_n < 10:
        score += 5.0
    elif strat_n < 30:
        score += 2.0

    # CLV quality
    clv = pick.get("clv_percent")
    if clv is not None:
        clv_f = float(clv)
        if clv_f < -4.0:
            score += 15.0
        elif clv_f < -1.0:
            score += 8.0
        elif clv_f < 0.0:
            score += 3.0

    # Market variance (BTTS highest, OU25 medium, 1X2 lower)
    mkt = (pick.get("market_key") or "").upper()
    if mkt == "BTTS":
        score += 5.0
    elif mkt not in ("1X2", "OU25", "DC"):
        score += 3.0

    return min(100.0, score)


def risk_label(risk_score: float) -> str:
    if risk_score >= 70.0:
        return "avoid"
    if risk_score >= 50.0:
        return "high"
    if risk_score >= 30.0:
        return "medium"
    return "low"


# ── C) Correlation ────────────────────────────────────────────────────────────

def _are_related_markets(mkt_a: str, sel_a: str, mkt_b: str, sel_b: str) -> bool:
    for (m1, s1), (m2, s2) in _RELATED_MARKET_PAIRS:
        if mkt_a == m1 and sel_a == s1 and mkt_b == m2 and sel_b == s2:
            return True
        if mkt_a == m2 and sel_a == s2 and mkt_b == m1 and sel_b == s1:
            return True
    return False


def compute_pick_correlation(pick_a: dict, pick_b: dict) -> float:
    """Pairwise correlation coefficient 0.0–1.0."""
    fid_a = pick_a.get("provider_fixture_id") or pick_a.get("fixture_id")
    fid_b = pick_b.get("provider_fixture_id") or pick_b.get("fixture_id")
    if fid_a and fid_b and fid_a == fid_b:
        return _CORR_SAME_FIXTURE

    corr = 0.0

    # Same team
    teams_a = {pick_a.get("team_home_id"), pick_a.get("team_away_id")} - {None}
    teams_b = {pick_b.get("team_home_id"), pick_b.get("team_away_id")} - {None}
    if teams_a and teams_b and teams_a & teams_b:
        corr += _CORR_SAME_TEAM

    # Related markets
    mkt_a = pick_a.get("market_key") or ""
    sel_a = pick_a.get("selection") or ""
    mkt_b = pick_b.get("market_key") or ""
    sel_b = pick_b.get("selection") or ""
    if _are_related_markets(mkt_a, sel_a, mkt_b, sel_b):
        corr += _CORR_RELATED_MKT

    # Same league
    lg_a = pick_a.get("league_id")
    lg_b = pick_b.get("league_id")
    if lg_a and lg_b and lg_a == lg_b:
        corr += _CORR_SAME_LEAGUE

    return min(1.0, corr)


def group_correlated_picks(picks: list[dict], threshold: float = 0.40) -> list[list[int]]:
    """Group pick indices that exceed the correlation threshold.

    Returns list of groups (each group is a list of indices into picks).
    """
    n = len(picks)
    if n < 2:
        return []
    visited = [False] * n
    groups: list[list[int]] = []
    for i in range(n):
        if visited[i]:
            continue
        group = [i]
        for j in range(i + 1, n):
            if not visited[j] and compute_pick_correlation(picks[i], picks[j]) >= threshold:
                group.append(j)
                visited[j] = True
        if len(group) > 1:
            visited[i] = True
            groups.append(group)
    return groups


def compute_portfolio_correlation_score(picks: list[dict]) -> float:
    """Average pairwise correlation across all pick pairs. 0.0 = uncorrelated."""
    n = len(picks)
    if n < 2:
        return 0.0
    total = sum(
        compute_pick_correlation(picks[i], picks[j])
        for i in range(n) for j in range(i + 1, n)
    )
    pairs = n * (n - 1) / 2
    return round(total / pairs, 4)


# ── D) Exposure ───────────────────────────────────────────────────────────────

def compute_exposure(recs: list[dict], profile: dict | None = None) -> dict:
    """Compute unit exposure by league, market, team, and fixture."""
    by_league:  dict[str, float] = {}
    by_market:  dict[str, float] = {}
    by_team:    dict[str, float] = {}
    by_fixture: dict[str, float] = {}

    for r in recs:
        units = float(r.get("recommended_units") or 0.0)
        if units <= 0:
            continue
        lg  = str(r.get("league_id") or "unknown")
        mkt = str(r.get("market_key") or "unknown")
        fid = str(r.get("provider_fixture_id") or r.get("fixture_id") or "unknown")
        by_league[lg]   = by_league.get(lg, 0.0) + units
        by_market[mkt]  = by_market.get(mkt, 0.0) + units
        by_fixture[fid] = by_fixture.get(fid, 0.0) + units
        for tid_key in ("team_home_id", "team_away_id"):
            tid = r.get(tid_key)
            if tid:
                ts = str(tid)
                by_team[ts] = by_team.get(ts, 0.0) + units

    return {
        "by_league":  by_league,
        "by_market":  by_market,
        "by_team":    by_team,
        "by_fixture": by_fixture,
    }


def generate_portfolio_warnings(
    recs: list[dict],
    exposure: dict,
    profile: dict | None = None,
) -> list[str]:
    """Return a list of warning codes for the current portfolio."""
    warnings: list[str] = []

    max_lg  = float((profile or {}).get("max_same_league_units") or 3.0)
    max_mkt = float((profile or {}).get("max_same_market_units") or 3.0)
    max_tm  = float((profile or {}).get("max_same_team_units")   or 2.0)
    max_day = float((profile or {}).get("max_daily_risk_units")  or 5.0)

    for lg, units in (exposure.get("by_league") or {}).items():
        if units > max_lg:
            warnings.append(f"too_much_same_league:{lg}:{units:.2f}u")

    for mkt, units in (exposure.get("by_market") or {}).items():
        if units > max_mkt:
            warnings.append(f"too_much_same_market:{mkt}:{units:.2f}u")

    for tm, units in (exposure.get("by_team") or {}).items():
        if units > max_tm:
            warnings.append(f"too_much_same_team:{tm}:{units:.2f}u")

    total_units = sum(
        float(r.get("recommended_units") or 0.0) for r in recs
    )
    if total_units > max_day:
        warnings.append(f"over_daily_risk_limit:{total_units:.2f}u")

    high_risk = [r for r in recs if float(r.get("risk_score") or 0.0) >= 70.0]
    if len(high_risk) >= 2:
        warnings.append(f"too_many_high_risk:{len(high_risk)}")

    corr_groups = group_correlated_picks(
        [r for r in recs if float(r.get("recommended_units") or 0.0) > 0]
    )
    if corr_groups:
        warnings.append(f"correlated_picks:{len(corr_groups)}_groups")

    # Strategy warnings
    low_sample = [
        r for r in recs
        if (r.get("strategy_sample_size") or 0) < 10
        and float(r.get("recommended_units") or 0.0) > 0
    ]
    if low_sample:
        warnings.append(f"low_sample_strategy:{len(low_sample)}")

    neg_clv = [
        r for r in recs
        if r.get("clv_percent") is not None
        and float(r.get("clv_percent")) < -2.0
        and float(r.get("recommended_units") or 0.0) > 0
    ]
    if neg_clv:
        warnings.append(f"negative_clv_strategy:{len(neg_clv)}")

    return warnings


# ── E) Portfolio score ────────────────────────────────────────────────────────

def compute_portfolio_score(
    recs: list[dict],
    exposure: dict,
    warnings: list[str],
    profile: dict | None = None,
) -> tuple[float, str]:
    """Portfolio score 0–100 and risk_level label.

    Higher score = better quality portfolio.
    """
    active = [r for r in recs if float(r.get("recommended_units") or 0.0) > 0]
    if not active:
        return 50.0, "balanced"

    n = len(active)

    # Component: avg edge (normalized to 0-25 points)
    avg_edge = sum(float(r.get("edge") or 0.0) for r in active) / n
    edge_pts = min(25.0, avg_edge * 500.0)

    # Component: avg strategy score (normalized to 0-25)
    strat_scores = [float(r.get("strategy_score") or 50.0) for r in active]
    avg_strat = sum(strat_scores) / len(strat_scores)
    strat_pts = avg_strat * 0.25

    # Component: diversification (0-20) — fewer picks in same league/market = better
    div_score = 20.0
    for units in (exposure.get("by_league") or {}).values():
        if units > (profile or {}).get("max_same_league_units", 3.0):
            div_score -= 5.0
    div_score = max(0.0, div_score)

    # Component: low correlation (0-15)
    corr_sc = compute_portfolio_correlation_score(active)
    corr_pts = max(0.0, 15.0 - corr_sc * 30.0)

    # Component: warnings penalty (0-15)
    warn_pts = max(0.0, 15.0 - len(warnings) * 3.0)

    score = edge_pts + strat_pts + div_score + corr_pts + warn_pts
    score = max(0.0, min(100.0, score))

    if score >= 75.0:
        level = "conservative"
    elif score >= 55.0:
        level = "balanced"
    elif score >= 35.0:
        level = "aggressive"
    else:
        level = "unsafe"

    return round(score, 1), level


# ── Main entry point ──────────────────────────────────────────────────────────

def _default_profile() -> dict:
    """Build a profile dict from settings defaults (used when no DB profile exists)."""
    return {
        "profile_name":             "default",
        "bankroll_units":           settings.bankroll_default_units,
        "base_unit_size":           settings.bankroll_base_unit_size,
        "max_daily_risk_units":     settings.bankroll_max_daily_units,
        "max_pick_risk_units":      settings.bankroll_max_pick_units,
        "max_parlay_risk_units":    settings.bankroll_max_parlay_units,
        "max_same_league_units":    3.0,
        "max_same_market_units":    3.0,
        "max_same_team_units":      2.0,
        "kelly_fraction":           settings.bankroll_kelly_fraction,
        "min_edge_for_stake":       settings.bankroll_min_edge,
        "min_confidence_for_stake": settings.bankroll_min_confidence,
        "reduce_low_sample":        settings.bankroll_reduce_low_sample,
        "block_avoid":              settings.bankroll_block_avoid_strategy,
    }


def compute_stake_recommendation(pick: dict, profile: dict) -> dict:
    """Compute full stake recommendation for a single pick dict.

    pick keys used (all optional-but-useful):
      odds, p_model, confidence_score, edge, ev, ev_adj,
      strategy_score, strategy_recommendation, strategy_sample_size,
      clv_percent, market_key, selection, league_id,
      team_home_id, team_away_id, pick_candidate_id, fixture_id, provider_fixture_id
    """
    odds = float(pick.get("odds") or pick.get("pick_odds") or 0.0)
    p_model = float(pick.get("p_model") or pick.get("model_probability") or 0.0)
    edge = float(pick.get("edge") or pick.get("pick_edge") or 0.0)
    confidence = float(
        pick.get("confidence_score") or pick.get("pick_confidence") or p_model
    )

    min_edge = float(profile.get("min_edge_for_stake") or 0.02)
    min_conf = float(profile.get("min_confidence_for_stake") or 0.52)
    bankroll  = float(profile.get("bankroll_units") or 100.0)
    fraction  = float(profile.get("kelly_fraction") or 0.25)
    max_units = float(profile.get("max_pick_risk_units") or 1.5)
    block_avoid = bool(profile.get("block_avoid") or profile.get("block_avoid_strategy", True))
    reduce_ls = bool(profile.get("reduce_low_sample", True))

    rejection: str | None = None
    kelly_full = 0.0
    kelly_frac = 0.0
    units = 0.0

    # Gate checks
    if odds <= 1.0:
        rejection = "odds_invalid"
    elif p_model <= 0.0:
        rejection = "p_model_missing"
    elif edge < min_edge:
        rejection = "edge_below_threshold"
    elif confidence < min_conf:
        rejection = "confidence_below_threshold"
    elif (pick.get("strategy_recommendation") == "avoid") and block_avoid:
        rejection = "strategy_avoid"
    else:
        kelly_full = compute_full_kelly(p_model, odds)
        if kelly_full <= 0.0:
            rejection = "kelly_zero"
        else:
            kelly_frac = compute_fractional_kelly(kelly_full, fraction)
            mult = 1.0

            # Adjustments (downward only)
            strat_rec = pick.get("strategy_recommendation")
            strat_n   = int(pick.get("strategy_sample_size") or 0)
            clv       = pick.get("clv_percent")

            if strat_rec == "reduce":
                mult *= 0.5
            elif strat_rec == "monitor":
                mult *= 0.85

            if strat_n < 10 and reduce_ls:
                mult *= 0.75

            if clv is not None:
                clv_f = float(clv)
                if clv_f < -3.0:
                    mult *= 0.6
                elif clv_f < 0.0:
                    mult *= 0.8

            units = cap_stake_units(kelly_frac * mult * bankroll, max_units)
            units = round(units, 2)
            if units < 0.01:
                units = 0.0
                rejection = "kelly_too_small"

    risk_sc = compute_risk_score(pick, profile)

    return {
        "pick_candidate_id":       pick.get("pick_candidate_id") or pick.get("id"),
        "published_pick_id":       pick.get("published_pick_id"),
        "fixture_id":              pick.get("fixture_id"),
        "provider_fixture_id":     pick.get("provider_fixture_id"),
        "market_key":              pick.get("market_key"),
        "selection":               pick.get("selection"),
        "league_id":               pick.get("league_id"),
        "team_home_id":            pick.get("team_home_id"),
        "team_away_id":            pick.get("team_away_id"),
        "odds":                    odds if odds > 0 else None,
        "p_model":                 p_model if p_model > 0 else None,
        "edge":                    edge,
        "ev":                      pick.get("ev"),
        "ev_adj":                  pick.get("ev_adj"),
        "strategy_score":          pick.get("strategy_score"),
        "strategy_recommendation": pick.get("strategy_recommendation"),
        "clv_percent":             pick.get("clv_percent"),
        "risk_score":              risk_sc,
        "correlation_score":       0.0,
        "kelly_full":              round(kelly_full, 6),
        "kelly_fractional":        round(kelly_frac, 6),
        "recommended_units":       units,
        "stake_label":             stake_label(units),
        "rejection_reason":        rejection,
    }


def _load_picks_for_day(conn, days: int) -> list[dict]:
    """Load picks eligible for bankroll computation from DuckDB."""
    try:
        rows = conn.execute(
            """
            SELECT
                pla.pick_candidate_id,
                pla.market_key,
                pla.league_id,
                pla.pick_odds               AS odds,
                pla.pick_edge               AS edge,
                pla.pick_confidence         AS confidence_score,
                pla.clv_percent,
                pla.strategy_key,
                sp.strategy_score,
                sp.recommendation           AS strategy_recommendation,
                sp.sample_size              AS strategy_sample_size,
                sp.roi                      AS strategy_roi,
                sp.clv_beat_rate            AS strategy_clv_beat_rate
            FROM pick_learning_annotations pla
            LEFT JOIN strategy_profiles sp ON pla.strategy_key = sp.strategy_key
            WHERE pla.created_at >= current_timestamp - INTERVAL (?) DAY
            ORDER BY pla.created_at DESC
            """,
            [days],
        ).fetchall()
        cols = [
            "pick_candidate_id", "market_key", "league_id", "odds", "edge",
            "confidence_score", "clv_percent", "strategy_key",
            "strategy_score", "strategy_recommendation", "strategy_sample_size",
            "strategy_roi", "strategy_clv_beat_rate",
        ]
        picks = [dict(zip(cols, r)) for r in rows]
        # Derive p_model from odds (implied) + edge
        for p in picks:
            odds_v = float(p.get("odds") or 2.0)
            edge_v = float(p.get("edge") or 0.0)
            if odds_v > 1.0:
                p["p_model"] = round(1.0 / odds_v + edge_v, 4)
        return picks
    except Exception as exc:
        logger.warning("_load_picks_for_day: %s", exc)
        return []


def run_bankroll_risk(
    conn,
    days: int = 1,
    dry_run: bool = True,
    profile_name: str = "default",
) -> dict:
    """Main entry point: compute stake recommendations + portfolio snapshot.

    Returns a summary dict with counts, totals and risk level.
    All functions are SYNC — never use await.
    """
    from app.data.local.bankroll_risk_repo import (
        get_active_bankroll_profile, upsert_stake_recommendation,
        upsert_portfolio_snapshot,
    )
    t_start = time.time()

    profile = get_active_bankroll_profile(conn) or _default_profile()
    picks = _load_picks_for_day(conn, days)

    recs: list[dict] = []
    for p in picks:
        rec = compute_stake_recommendation(p, profile)
        recs.append(rec)

    # Compute portfolio metrics
    exposure = compute_exposure(recs, profile)
    warnings = generate_portfolio_warnings(recs, exposure, profile)
    port_score, risk_level = compute_portfolio_score(recs, exposure, warnings, profile)
    corr_score = compute_portfolio_correlation_score(
        [r for r in recs if float(r.get("recommended_units") or 0.0) > 0]
    )

    # Update correlation_score on each rec
    for rec in recs:
        rec["correlation_score"] = corr_score

    total_units = round(sum(float(r.get("recommended_units") or 0.0) for r in recs), 3)
    with_stake  = [r for r in recs if float(r.get("recommended_units") or 0.0) > 0]

    if not dry_run:
        for rec in recs:
            if rec.get("pick_candidate_id") is not None:
                try:
                    upsert_stake_recommendation(conn, rec)
                except Exception as exc:
                    logger.warning("upsert_stake_recommendation: %s", exc)
        try:
            upsert_portfolio_snapshot(conn, {
                "snapshot_date":         date.today().isoformat(),
                "total_picks":           len(picks),
                "total_recommended_units": total_units,
                "total_daily_risk_units":  total_units,
                "exposure_by_league":    exposure.get("by_league"),
                "exposure_by_market":    exposure.get("by_market"),
                "exposure_by_team":      exposure.get("by_team"),
                "correlated_groups":     group_correlated_picks(with_stake),
                "portfolio_score":       port_score,
                "risk_level":            risk_level,
                "warnings":              warnings,
            })
        except Exception as exc:
            logger.warning("upsert_portfolio_snapshot: %s", exc)

    return {
        "days":          days,
        "dry_run":       dry_run,
        "picks":         len(picks),
        "with_stake":    len(with_stake),
        "rejected":      len(picks) - len(with_stake),
        "total_units":   total_units,
        "portfolio_score": port_score,
        "risk_level":    risk_level,
        "warnings":      warnings,
        "elapsed_s":     round(time.time() - t_start, 2),
    }


# ── VE adapter enrichment helper ──────────────────────────────────────────────

def get_bankroll_meta_for_pick(conn, pick_dict: dict) -> dict:
    """Called from VE adapter: compute bankroll metadata for a single pick.

    Returns dict with bankroll fields (never raises).
    """
    empty = {
        "bankroll_risk_score":    None,
        "bankroll_risk_label":    None,
        "bankroll_recommended_units": None,
        "bankroll_stake_label":   None,
        "bankroll_kelly_full":    None,
        "bankroll_kelly_frac":    None,
        "bankroll_exposure_flags": None,
        "bankroll_warning":       None,
    }
    if not settings.bankroll_engine_enabled:
        return empty
    try:
        from app.data.local.bankroll_risk_repo import get_active_bankroll_profile
        profile = get_active_bankroll_profile(conn) or _default_profile()
        rec = compute_stake_recommendation(pick_dict, profile)
        risk_sc = rec["risk_score"]
        return {
            "bankroll_risk_score":       round(risk_sc, 1),
            "bankroll_risk_label":       risk_label(risk_sc),
            "bankroll_recommended_units": rec["recommended_units"],
            "bankroll_stake_label":      rec["stake_label"],
            "bankroll_kelly_full":       rec["kelly_full"],
            "bankroll_kelly_frac":       rec["kelly_fractional"],
            "bankroll_exposure_flags":   None,
            "bankroll_warning":          rec["rejection_reason"],
        }
    except Exception as exc:
        logger.debug("get_bankroll_meta_for_pick: %s", exc)
        return empty
