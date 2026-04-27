"""Poisson model for football goal distribution.

Computes 1X2, Double Chance, Over/Under 2.5, and BTTS probabilities from
expected goal rates (lambda_home, lambda_away).

No external dependencies — uses only the math standard library.
All functions are pure (no database I/O).

Typical usage:
    from app.model import poisson_model

    lh, la = compute_lambdas(...)
    p1x2  = poisson_model.prob_1x2(lh, la)              # {'Home': p, 'Draw': p, 'Away': p}
    pdc   = poisson_model.prob_double_chance(p1x2)       # {'1X': p, 'X2': p, '12': p}
    pou   = poisson_model.prob_ou25(lh, la)              # {'Over 2.5': p, 'Under 2.5': p}
    pbtts = poisson_model.prob_btts(lh, la)              # {'Yes': p, 'No': p}

Dixon-Coles correction (optional, rho parameter):
    Pass rho=-0.13 to prob_1x2 to apply a small correction to low-scoring
    cells (0-0, 1-0, 0-1, 1-1). rho=0.0 (default) = standard Poisson.
    Typical value: rho ≈ -0.13.

Goal distribution truncation:
    P(k >= _MAX_GOALS) is negligible for realistic lambdas (< 5).
    _MAX_GOALS = 10 keeps precision high while keeping iteration cheap.
"""

from __future__ import annotations

import math

_MAX_GOALS = 10


def _pmf(k: int, lam: float) -> float:
    """Poisson probability mass function P(X = k; lambda)."""
    if lam <= 0.0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _dc_tau(h: int, a: int, lh: float, la: float, rho: float) -> float:
    """Dixon-Coles correction multiplier for low-score cells.

    Only affects the four cells (0,0), (1,0), (0,1), (1,1).
    At rho=0.0, returns 1.0 for all cells (no correction).
    Typical rho ≈ -0.13 boosts 0-0 and 1-1, reduces 1-0 and 0-1.
    """
    if h == 0 and a == 0:
        return 1.0 - lh * la * rho
    if h == 1 and a == 0:
        return 1.0 + la * rho
    if h == 0 and a == 1:
        return 1.0 + lh * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def prob_1x2(
    lambda_home: float,
    lambda_away: float,
    *,
    rho: float = 0.0,
) -> dict[str, float]:
    """Return {'Home': p, 'Draw': p, 'Away': p}.

    Sums joint Poisson probabilities over all (h, a) goal combinations
    up to _MAX_GOALS. When rho != 0.0, applies Dixon-Coles correction to the
    four low-score cells before summing. Probabilities are renormalized to
    sum exactly to 1.0.
    """
    p_home = p_draw = p_away = 0.0
    for h in range(_MAX_GOALS + 1):
        ph = _pmf(h, lambda_home)
        if ph < 1e-12:
            continue
        for a in range(_MAX_GOALS + 1):
            pa = _pmf(a, lambda_away)
            if pa < 1e-12:
                continue
            p = ph * pa
            if rho != 0.0:
                p *= _dc_tau(h, a, lambda_home, lambda_away, rho)
            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p
    total = p_home + p_draw + p_away
    if total <= 0.0:
        return {"Home": 1 / 3, "Draw": 1 / 3, "Away": 1 / 3}
    return {
        "Home": round(p_home / total, 6),
        "Draw": round(p_draw / total, 6),
        "Away": round(p_away / total, 6),
    }


def prob_double_chance(p_1x2: dict[str, float]) -> dict[str, float]:
    """Derive Double Chance probabilities from a 1X2 dict.

    1X = Home or Draw; X2 = Draw or Away; 12 = Home or Away.
    The three selections are NOT mutually exclusive (sum ≈ 2.0).
    """
    ph = p_1x2["Home"]
    pd = p_1x2["Draw"]
    pa = p_1x2["Away"]
    return {
        "1X": round(ph + pd, 6),
        "X2": round(pd + pa, 6),
        "12": round(ph + pa, 6),
    }


def prob_ou25(lambda_home: float, lambda_away: float) -> dict[str, float]:
    """Return {'Over 2.5': p, 'Under 2.5': p}.

    Under 2.5 = total goals in {0, 1, 2}.
    """
    p_under = 0.0
    for total_goals in range(3):  # 0, 1, 2
        for h in range(total_goals + 1):
            a = total_goals - h
            p_under += _pmf(h, lambda_home) * _pmf(a, lambda_away)
    p_over = max(0.0, 1.0 - p_under)
    return {
        "Over 2.5": round(p_over, 6),
        "Under 2.5": round(p_under, 6),
    }


def prob_btts(lambda_home: float, lambda_away: float) -> dict[str, float]:
    """Return {'Yes': p, 'No': p} for both-teams-to-score."""
    p_home_blank = _pmf(0, lambda_home)
    p_away_blank = _pmf(0, lambda_away)
    p_yes = (1.0 - p_home_blank) * (1.0 - p_away_blank)
    return {
        "Yes": round(p_yes, 6),
        "No": round(max(0.0, 1.0 - p_yes), 6),
    }
