"""Match context scorer — augments odds consensus with performance signals.

Reads fixtures.context_json (populated during daily sync) and produces:
  - reasons: human-readable strings explaining why the pick makes sense
  - main_risk: the single most important concern
  - signals: structured list for auditability
  - probability_adjustment: small float added to the odds-consensus model_probability

Design constraints:
  - Adjustment range is intentionally capped at ±0.05 (5 percentage points).
    The bookmaker odds consensus is well-calibrated; context signals are used
    to build confidence narrative, not to override market pricing.
  - Every signal is optional — missing data is silently skipped without error.
  - Provider predictions are advisory (weight ≈ 0.01), not authoritative.

Context JSON structure (fixtures.context_json):
    {
        "home_standing": {"rank": 3, "points": 45, "goals_for": 48, "goals_against": 22, ...},
        "away_standing": {"rank": 9, "points": 28, ...},
        "home_stats": {"form": "WWDWL", "fixtures": {...}, "goals": {...},
                       "clean_sheet": {...}, "failed_to_score": {...}},
        "away_stats": {"form": "LDWDL", ...},
        "injuries": [{"player_name": "X", "team_id": 33, "type": "Injured"}, ...],
        "provider_prediction": {"advice": "...", "percent": {"home": "60%", ...},
                                "under_over": "+2.5", "goals_home": 1.8, ...},
        "home_team_provider_id": int,
        "away_team_provider_id": int,
    }
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ADJUSTMENT = 0.05


# ── Public interface ──────────────────────────────────────────────────────────


def score_market(
    market: str,
    selection: str,
    context: dict,
) -> dict:
    """Return context signals for a market/selection given match context.

    Args:
        market: "1X2" | "OU25" | "BTTS"
        selection: e.g. "Home", "Away", "Draw", "Over 2.5", "Under 2.5", "Yes", "No"
        context: fixtures.context_json blob (may be empty)

    Returns:
        {
            "reasons": list[str],           # human-readable pick reasons
            "main_risk": str,               # key concern
            "signals": list[dict],          # structured for argument_json
            "probability_adjustment": float # capped at ±MAX_ADJUSTMENT
        }
    """
    if not context:
        return _empty_result("Sin datos de contexto disponibles")

    if market == "1X2":
        return _score_1x2(selection, context)
    if market == "OU25":
        return _score_ou25(selection, context)
    if market == "BTTS":
        return _score_btts(selection, context)

    return _empty_result("Mercado no reconocido para scoring de contexto")


# ── 1X2 scorer ────────────────────────────────────────────────────────────────


def _score_1x2(selection: str, ctx: dict) -> dict:
    reasons: list[str] = []
    risks: list[str] = []
    signals: list[dict] = []
    adj = 0.0

    home_std = ctx.get("home_standing", {})
    away_std = ctx.get("away_standing", {})
    home_st = ctx.get("home_stats", {})
    away_st = ctx.get("away_stats", {})
    provider = ctx.get("provider_prediction", {})
    injuries = ctx.get("injuries", [])
    home_pid = ctx.get("home_team_provider_id")
    away_pid = ctx.get("away_team_provider_id")

    # ── Standings position signal ─────────────────────────────────────────────
    home_rank = home_std.get("rank", 0)
    away_rank = away_std.get("rank", 0)
    if home_rank and away_rank:
        pos_diff = away_rank - home_rank  # positive = home is higher ranked
        if pos_diff >= 5 and selection == "Home":
            reasons.append(f"Local mejor clasificado (#{home_rank} vs #{away_rank})")
            adj += 0.015
            signals.append({"name": "standing_advantage", "direction": "home", "value": pos_diff})
        elif pos_diff <= -5 and selection == "Away":
            reasons.append(f"Visitante mejor clasificado (#{away_rank} vs #{home_rank})")
            adj += 0.015
            signals.append({"name": "standing_advantage", "direction": "away", "value": abs(pos_diff)})
        elif pos_diff >= 5 and selection == "Away":
            risks.append(f"Visitante peor clasificado (#{away_rank}) que el local (#{home_rank})")
            adj -= 0.01
        elif pos_diff <= -5 and selection == "Home":
            risks.append(f"Visitante mejor clasificado (#{away_rank}) que el local (#{home_rank})")
            adj -= 0.01

    # ── Form signal (last 5 matches) ──────────────────────────────────────────
    home_form = _form_score(home_st.get("form", ""))
    away_form = _form_score(away_st.get("form", ""))
    form_diff = home_form - away_form

    if form_diff >= 2 and selection == "Home":
        reasons.append(
            f"Local en mejor forma reciente ({_form_label(home_st.get('form',''))} "
            f"vs {_form_label(away_st.get('form',''))})"
        )
        adj += 0.01
        signals.append({"name": "form_advantage", "direction": "home", "value": round(form_diff, 1)})
    elif form_diff <= -2 and selection == "Away":
        reasons.append(
            f"Visitante en mejor forma reciente ({_form_label(away_st.get('form',''))} "
            f"vs {_form_label(home_st.get('form',''))})"
        )
        adj += 0.01
        signals.append({"name": "form_advantage", "direction": "away", "value": round(abs(form_diff), 1)})
    elif form_diff >= 2 and selection == "Away":
        risks.append(f"Local en mejor forma que el visitante")
        adj -= 0.008
    elif form_diff <= -2 and selection == "Home":
        risks.append(f"Visitante en mejor forma que el local")
        adj -= 0.008

    # ── Home/away split ───────────────────────────────────────────────────────
    home_home_fix = home_st.get("fixtures", {}).get("wins", {}).get("home", 0)
    home_home_played = home_st.get("fixtures", {}).get("played", {}).get("home", 0)
    away_away_fix = away_st.get("fixtures", {}).get("wins", {}).get("away", 0)
    away_away_played = away_st.get("fixtures", {}).get("played", {}).get("away", 0)

    if home_home_played >= 4 and selection == "Home":
        home_win_pct = home_home_fix / home_home_played
        if home_win_pct >= 0.60:
            reasons.append(
                f"Local fuerte en casa ({home_home_fix}V en {home_home_played} partidos como local)"
            )
            adj += 0.01
            signals.append({"name": "home_strength", "value": round(home_win_pct, 2)})
        elif home_win_pct <= 0.30:
            risks.append(f"Local flojo en casa ({home_home_fix}V en {home_home_played} como local)")
            adj -= 0.01

    if away_away_played >= 4 and selection == "Away":
        away_win_pct = away_away_fix / away_away_played
        if away_win_pct >= 0.50:
            reasons.append(
                f"Visitante efectivo fuera ({away_away_fix}V en {away_away_played} como visitante)"
            )
            adj += 0.01
            signals.append({"name": "away_strength", "value": round(away_win_pct, 2)})

    # ── Clean sheets (defensive strength) ────────────────────────────────────
    home_cs = home_st.get("clean_sheet", {}).get("home", 0)
    if home_home_played and home_cs and selection == "Home":
        cs_ratio = home_cs / max(home_home_played, 1)
        if cs_ratio >= 0.4:
            reasons.append(f"Defensa sólida del local ({home_cs} porterías a cero en casa)")
            signals.append({"name": "home_clean_sheet_rate", "value": round(cs_ratio, 2)})

    # ── Key injuries signal ───────────────────────────────────────────────────
    home_injured = [i for i in injuries if i.get("team_id") == home_pid]
    away_injured = [i for i in injuries if i.get("team_id") == away_pid]
    if len(home_injured) >= 3 and selection == "Away":
        risks.append(f"Local con {len(home_injured)} bajas relevantes")
        adj += 0.008
    if len(away_injured) >= 3 and selection == "Home":
        reasons.append(f"Visitante con {len(away_injured)} bajas relevantes")
        adj += 0.005

    # ── Provider prediction (advisory only) ───────────────────────────────────
    advice = provider.get("advice", "")
    winner_id = provider.get("winner_id")
    if advice and winner_id:
        if winner_id == home_pid and selection == "Home":
            reasons.append(f'Predicción del proveedor: "{advice}"')
            adj += 0.008
            signals.append({"name": "provider_confirms", "direction": "home"})
        elif winner_id == away_pid and selection == "Away":
            reasons.append(f'Predicción del proveedor: "{advice}"')
            adj += 0.008
            signals.append({"name": "provider_confirms", "direction": "away"})
        elif winner_id == home_pid and selection == "Away":
            risks.append(f'Proveedor favorece al local: "{advice}"')
            signals.append({"name": "provider_contradicts", "direction": "home"})
        elif winner_id == away_pid and selection == "Home":
            risks.append(f'Proveedor favorece al visitante: "{advice}"')
            signals.append({"name": "provider_contradicts", "direction": "away"})

    main_risk = risks[0] if risks else "Mercado equilibrado — alta varianza esperada"
    if not reasons:
        reasons.append("Señales de contexto neutras — basado principalmente en cuotas")

    return _build_result(reasons, main_risk, signals, adj)


# ── Over/Under 2.5 scorer ─────────────────────────────────────────────────────


def _score_ou25(selection: str, ctx: dict) -> dict:
    reasons: list[str] = []
    risks: list[str] = []
    signals: list[dict] = []
    adj = 0.0

    home_st = ctx.get("home_stats", {})
    away_st = ctx.get("away_stats", {})
    provider = ctx.get("provider_prediction", {})

    is_over = selection == "Over 2.5"

    # ── Goals per match averages ──────────────────────────────────────────────
    home_gf_avg = _parse_avg(home_st, "goals", "for", "total")
    home_ga_avg = _parse_avg(home_st, "goals", "against", "total")
    away_gf_avg = _parse_avg(away_st, "goals", "for", "total")
    away_ga_avg = _parse_avg(away_st, "goals", "against", "total")

    expected_goals = home_gf_avg + away_gf_avg  # naive sum as proxy

    if expected_goals and is_over:
        if expected_goals >= 2.8:
            reasons.append(
                f"Promedio combinado de goles alto ({home_gf_avg:.1f} + {away_gf_avg:.1f} "
                f"= {expected_goals:.1f} g/p)"
            )
            adj += 0.015
            signals.append({"name": "expected_goals", "value": round(expected_goals, 2)})
        elif expected_goals <= 1.8:
            risks.append(f"Promedio combinado de goles bajo ({expected_goals:.1f} g/p)")
            adj -= 0.01
    elif expected_goals and not is_over:
        if expected_goals <= 2.0:
            reasons.append(
                f"Promedio combinado de goles bajo ({expected_goals:.1f} g/p — favorece Under)"
            )
            adj += 0.015
            signals.append({"name": "expected_goals", "value": round(expected_goals, 2)})
        elif expected_goals >= 3.0:
            risks.append(f"Promedio de goles alto ({expected_goals:.1f}) — mayor riesgo para Under")
            adj -= 0.01

    # ── Clean sheet vs failed to score ───────────────────────────────────────
    home_fts = home_st.get("failed_to_score", {}).get("total", 0)
    away_fts = away_st.get("failed_to_score", {}).get("total", 0)
    home_played_total = home_st.get("fixtures", {}).get("played", {}).get("total", 1) or 1
    away_played_total = away_st.get("fixtures", {}).get("played", {}).get("total", 1) or 1

    if home_fts and not is_over:
        fts_rate = home_fts / home_played_total
        if fts_rate >= 0.35:
            reasons.append(
                f"Local no anotó en {home_fts}/{home_played_total} partidos (favorece Under/No BTTS)"
            )
            adj += 0.01
    if away_fts and not is_over:
        fts_rate = away_fts / away_played_total
        if fts_rate >= 0.35:
            reasons.append(
                f"Visitante no anotó en {away_fts}/{away_played_total} partidos"
            )
            adj += 0.008

    # ── Provider under/over signal ────────────────────────────────────────────
    under_over = provider.get("under_over", "")
    if under_over:
        provider_is_over = under_over.startswith("+")
        if provider_is_over == is_over:
            reasons.append(
                f"Proveedor coincide: {'+' if provider_is_over else '-'}2.5 goles esperados"
            )
            adj += 0.008
            signals.append({"name": "provider_under_over", "direction": "confirms"})
        else:
            risks.append(
                f"Proveedor discrepa: espera {under_over} goles"
            )
            signals.append({"name": "provider_under_over", "direction": "contradicts"})

    main_risk = risks[0] if risks else "Alta varianza en totales de goles"
    if not reasons:
        reasons.append("Señales de goles neutras — basado principalmente en cuotas")

    return _build_result(reasons, main_risk, signals, adj)


# ── BTTS scorer ───────────────────────────────────────────────────────────────


def _score_btts(selection: str, ctx: dict) -> dict:
    reasons: list[str] = []
    risks: list[str] = []
    signals: list[dict] = []
    adj = 0.0

    home_st = ctx.get("home_stats", {})
    away_st = ctx.get("away_stats", {})
    provider = ctx.get("provider_prediction", {})

    is_yes = selection == "Yes"

    # ── Attack strength of both teams ─────────────────────────────────────────
    home_gf_avg = _parse_avg(home_st, "goals", "for", "home")  # home goals at home
    away_gf_avg = _parse_avg(away_st, "goals", "for", "away")  # away goals when away

    if home_gf_avg and away_gf_avg:
        both_score = home_gf_avg >= 1.0 and away_gf_avg >= 0.8
        if both_score and is_yes:
            reasons.append(
                f"Ambos anotan con regularidad (local {home_gf_avg:.1f} g/p en casa, "
                f"visitante {away_gf_avg:.1f} g/p fuera)"
            )
            adj += 0.015
            signals.append({"name": "both_attack", "home_avg": home_gf_avg, "away_avg": away_gf_avg})
        elif not both_score and not is_yes:
            reasons.append(
                f"Ataque débil de alguno: local {home_gf_avg:.1f} g/p, "
                f"visitante {away_gf_avg:.1f} g/p (favorece No BTTS)"
            )
            adj += 0.01

    # ── Failed to score rates ─────────────────────────────────────────────────
    home_fts = home_st.get("failed_to_score", {}).get("home", 0)
    away_fts = away_st.get("failed_to_score", {}).get("away", 0)
    home_home_played = home_st.get("fixtures", {}).get("played", {}).get("home", 1) or 1
    away_away_played = away_st.get("fixtures", {}).get("played", {}).get("away", 1) or 1

    if home_fts:
        home_fts_rate = home_fts / home_home_played
        if home_fts_rate >= 0.35 and is_yes:
            risks.append(f"Local no anotó en casa en {home_fts_rate*100:.0f}% de partidos")
            adj -= 0.01
        elif home_fts_rate >= 0.35 and not is_yes:
            reasons.append(
                f"Local frecuentemente no anota en casa ({home_fts_rate*100:.0f}%)"
            )
            adj += 0.01

    if away_fts:
        away_fts_rate = away_fts / away_away_played
        if away_fts_rate >= 0.40 and is_yes:
            risks.append(f"Visitante no anotó fuera en {away_fts_rate*100:.0f}% de partidos")
            adj -= 0.01
        elif away_fts_rate >= 0.40 and not is_yes:
            reasons.append(
                f"Visitante frecuentemente no anota fuera ({away_fts_rate*100:.0f}%)"
            )
            adj += 0.01

    # ── Provider goals_home / goals_away ──────────────────────────────────────
    goals_home = provider.get("goals_home")
    goals_away = provider.get("goals_away")
    if goals_home is not None and goals_away is not None:
        try:
            gh = float(goals_home)
            ga = float(goals_away)
            provider_btts = gh >= 0.8 and ga >= 0.8
            if provider_btts == is_yes:
                reasons.append(
                    f"Proveedor espera goles de ambos ({gh:.1f} local, {ga:.1f} visitante)"
                )
                adj += 0.008
                signals.append({"name": "provider_goals_confirm"})
            else:
                risks.append(
                    f"Proveedor espera un equipo sin anotar ({gh:.1f} local, {ga:.1f} visitante)"
                )
        except (TypeError, ValueError):
            pass

    main_risk = risks[0] if risks else "Rendimiento ofensivo de los equipos es la clave"
    if not reasons:
        reasons.append("Señales BTTS neutras — basado principalmente en cuotas")

    return _build_result(reasons, main_risk, signals, adj)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _form_score(form: str) -> float:
    """Score the most recent 5 matches: W=1, D=0.5, L=0. Returns 0–5."""
    if not form:
        return 2.5  # neutral if unknown
    recent = form[-5:]
    return sum(1.0 if c == "W" else 0.5 if c == "D" else 0.0 for c in recent)


def _form_label(form: str) -> str:
    """Return the last 5 chars of the form string, or '-' if empty."""
    if not form:
        return "-"
    return form[-5:] if len(form) >= 5 else form


def _parse_avg(stats: dict, *keys: str) -> float:
    """Navigate nested stats dicts to find an 'average' string, return as float."""
    node: Any = stats
    for k in keys:
        if not isinstance(node, dict):
            return 0.0
        node = node.get(k, {})
    if isinstance(node, dict):
        avg = node.get("average", node.get("total", 0))
    else:
        avg = node
    try:
        return float(avg or 0)
    except (TypeError, ValueError):
        return 0.0


def _build_result(
    reasons: list[str],
    main_risk: str,
    signals: list[dict],
    adjustment: float,
) -> dict:
    clamped = max(-_MAX_ADJUSTMENT, min(_MAX_ADJUSTMENT, adjustment))
    return {
        "reasons": reasons,
        "main_risk": main_risk,
        "signals": signals,
        "probability_adjustment": round(clamped, 4),
    }


def _empty_result(main_risk: str) -> dict:
    return {
        "reasons": [],
        "main_risk": main_risk,
        "signals": [],
        "probability_adjustment": 0.0,
    }
