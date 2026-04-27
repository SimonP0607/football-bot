"""Elo rating system for football matches.

All functions are pure (no database I/O). The caller is responsible for
persisting elo_before/elo_after to team_elo_history via history_repo.

Default parameters:
    K               = 32.0  (update magnitude per match)
    HOME_ADVANTAGE  = 65.0  (Elo points added to home team; yields ~60% expected
                             win probability at home for equally-rated teams)
    INITIAL_RATING  = 1500.0 (for teams with no prior history)

Pool separation:
    Clubs and national teams NEVER share ratings. The caller must track
    entity_scope and query/store rows with the correct scope.

Competition weighting:
    The elo_weight parameter from competition_context scales the K-factor.
    League matches use weight=1.0; domestic cups weight=0.4; friendlies ~0.3.
    A weight of 0 means the match is observed but Elo is not updated.
"""

from __future__ import annotations

K: float = 32.0
HOME_ADVANTAGE: float = 65.0
INITIAL_RATING: float = 1500.0


def expected_score(elo_a: float, elo_b: float, home_advantage: float = 0.0) -> float:
    """Expected match result for team A (0.0 = certain loss, 1.0 = certain win).

    home_advantage: Elo-point bonus added to elo_a (use HOME_ADVANTAGE when A
    is the home team, 0.0 for neutral venues or when A is the away team).
    """
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a - home_advantage) / 400.0))


def _result_score(goals_for: int, goals_against: int) -> float:
    if goals_for > goals_against:
        return 1.0
    if goals_for == goals_against:
        return 0.5
    return 0.0


def update(
    elo_home: float,
    elo_away: float,
    goals_home: int,
    goals_away: int,
    *,
    k: float = K,
    home_advantage: float = HOME_ADVANTAGE,
    weight: float = 1.0,
) -> tuple[float, float]:
    """Return (new_elo_home, new_elo_away) after a completed match.

    Args:
        elo_home:       Current Elo of the home team.
        elo_away:       Current Elo of the away team.
        goals_home:     Final goals scored by home team.
        goals_away:     Final goals scored by away team.
        k:              Base K-factor (default K=32).
        home_advantage: Elo bonus for the home team (default HOME_ADVANTAGE=65).
        weight:         Competition weight from competition_context.elo_weight.
                        Scales effective K: eff_k = k * weight.
    """
    e_home = expected_score(elo_home, elo_away, home_advantage)
    s_home = _result_score(goals_home, goals_away)
    eff_k = k * weight
    new_home = elo_home + eff_k * (s_home - e_home)
    new_away = elo_away + eff_k * ((1.0 - s_home) - (1.0 - e_home))
    return round(new_home, 2), round(new_away, 2)


def seasonal_regression(
    elo: float,
    *,
    mean: float = INITIAL_RATING,
    factor: float = 0.1,
) -> float:
    """Pull elo toward mean at a season boundary.

    Handles promoted/relegated teams and prevents extreme rating divergence.
    factor=0.0 disables regression (no change). Typical value: 0.1.
    """
    return round(elo + factor * (mean - elo), 2)
