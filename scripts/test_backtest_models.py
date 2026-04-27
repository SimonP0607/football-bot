#!/usr/bin/env python
"""Smoke tests for Elo, Poisson, and Dixon-Coles models.

Runs without any database connection -- pure math validation.
Exit 0 if all tests pass, 1 if any fail.

Usage:
    python scripts/test_backtest_models.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.model import elo, poisson_model

_PASS: list[str] = []
_FAIL: list[str] = []


def check(name: str, condition: bool, msg: str = "") -> None:
    if condition:
        _PASS.append(name)
        print(f"  OK   {name}")
    else:
        _FAIL.append(name)
        detail = f": {msg}" if msg else ""
        print(f"  FAIL {name}{detail}")


# ── Elo ───────────────────────────────────────────────────────────────────────


def test_elo() -> None:
    print("\n[Elo]")

    e = elo.expected_score(1500, 1500)
    check("expected_score equal ratings -> 0.5", abs(e - 0.5) < 1e-6, f"got {e}")

    e_home = elo.expected_score(1500, 1500, elo.HOME_ADVANTAGE)
    check("expected_score home advantage -> > 0.5", e_home > 0.5, f"got {e_home}")

    e_away = elo.expected_score(1500, 1500, -elo.HOME_ADVANTAGE)
    check("expected_score away disadvantage -> < 0.5", e_away < 0.5, f"got {e_away}")

    nh, na = elo.update(1500, 1500, 2, 0)
    check("update home win increases home", nh > 1500, f"nh={nh}")
    check("update home win decreases away", na < 1500, f"na={na}")
    check("update is zero-sum", abs((nh + na) - 3000.0) < 0.01, f"sum={nh+na}")

    nh0, na0 = elo.update(1500, 1500, 0, 2, weight=0.0)
    check("update weight=0 -> no change", nh0 == 1500.0 and na0 == 1500.0,
          f"nh={nh0} na={na0}")

    r0 = elo.seasonal_regression(1600, factor=0.0)
    check("seasonal_regression factor=0 -> no change", r0 == 1600.0, f"got {r0}")

    r1 = elo.seasonal_regression(1600, factor=1.0)
    check("seasonal_regression factor=1 -> resets to mean",
          abs(r1 - elo.INITIAL_RATING) < 0.01, f"got {r1}")

    r01 = elo.seasonal_regression(1600, factor=0.1)
    check("seasonal_regression factor=0.1 -> partial", 1500 < r01 < 1600, f"got {r01}")

    r_low = elo.seasonal_regression(1400, factor=0.1)
    check("seasonal_regression below mean -> increases", r_low > 1400, f"got {r_low}")


# ── Poisson model ─────────────────────────────────────────────────────────────


def test_poisson_model() -> None:
    print("\n[Poisson]")

    p = poisson_model.prob_1x2(1.5, 1.2)
    total = sum(p.values())
    check("prob_1x2 sums to 1.0", abs(total - 1.0) < 1e-4, f"sum={total}")

    p_sym = poisson_model.prob_1x2(1.3, 1.3)
    check("prob_1x2 symmetric -> Home ~= Away",
          abs(p_sym["Home"] - p_sym["Away"]) < 1e-4,
          f"Home={p_sym['Home']} Away={p_sym['Away']}")

    p_high_home = poisson_model.prob_1x2(2.5, 0.5)
    check("prob_1x2 high home lambda -> Home most likely",
          p_high_home["Home"] > p_high_home["Away"], f"{p_high_home}")

    p_dc = poisson_model.prob_1x2(1.0, 0.8, rho=-0.13)
    p_std = poisson_model.prob_1x2(1.0, 0.8, rho=0.0)
    check("prob_1x2 Dixon-Coles rho=-0.13 differs from rho=0",
          p_dc != p_std, f"dc={p_dc} std={p_std}")

    check("prob_1x2 rho=0.0 sums to 1.0 (unchanged)",
          abs(sum(p_std.values()) - 1.0) < 1e-4, f"sum={sum(p_std.values())}")
    check("prob_1x2 rho=-0.13 sums to 1.0",
          abs(sum(p_dc.values()) - 1.0) < 1e-4, f"sum={sum(p_dc.values())}")


def test_double_chance() -> None:
    print("\n[Double Chance]")

    p1x2 = poisson_model.prob_1x2(1.5, 1.2)
    pdc = poisson_model.prob_double_chance(p1x2)

    check("prob_double_chance 1X = Home + Draw",
          abs(pdc["1X"] - (p1x2["Home"] + p1x2["Draw"])) < 1e-5,
          f"1X={pdc['1X']} vs {p1x2['Home'] + p1x2['Draw']:.6f}")
    check("prob_double_chance X2 = Draw + Away",
          abs(pdc["X2"] - (p1x2["Draw"] + p1x2["Away"])) < 1e-5,
          f"X2={pdc['X2']} vs {p1x2['Draw'] + p1x2['Away']:.6f}")
    check("prob_double_chance 12 = Home + Away",
          abs(pdc["12"] - (p1x2["Home"] + p1x2["Away"])) < 1e-5,
          f"12={pdc['12']} vs {p1x2['Home'] + p1x2['Away']:.6f}")
    check("prob_double_chance all > 0.5", all(v > 0.5 for v in pdc.values()),
          f"{pdc}")
    check("prob_double_chance sum ~= 2.0",
          abs(sum(pdc.values()) - 2.0) < 1e-4, f"sum={sum(pdc.values())}")


def test_ou_btts() -> None:
    print("\n[OU25 / BTTS]")

    pou = poisson_model.prob_ou25(1.5, 1.2)
    check("prob_ou25 sums to 1.0", abs(sum(pou.values()) - 1.0) < 1e-6)

    pou_high = poisson_model.prob_ou25(3.0, 3.0)
    check("prob_ou25 high lambdas -> mostly Over", pou_high["Over 2.5"] > 0.9,
          f"Over={pou_high['Over 2.5']}")

    pou_low = poisson_model.prob_ou25(0.3, 0.3)
    check("prob_ou25 low lambdas -> mostly Under", pou_low["Under 2.5"] > 0.9,
          f"Under={pou_low['Under 2.5']}")

    pbtts_low = poisson_model.prob_btts(0.1, 0.1)
    check("prob_btts low lambdas -> mostly No", pbtts_low["No"] > 0.8,
          f"No={pbtts_low['No']}")

    pbtts_high = poisson_model.prob_btts(3.0, 3.0)
    check("prob_btts high lambdas -> mostly Yes", pbtts_high["Yes"] > 0.9,
          f"Yes={pbtts_high['Yes']}")

    check("prob_btts sums to 1.0",
          abs(sum(pbtts_low.values()) - 1.0) < 1e-6)


# ── Main ──────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    print("\n=== Smoke Tests: Elo + Poisson + Dixon-Coles ===")
    test_elo()
    test_poisson_model()
    test_double_chance()
    test_ou_btts()
    print(f"\n{'=' * 44}")
    print(f"  Passed : {len(_PASS)}")
    print(f"  Failed : {len(_FAIL)}")
    if _FAIL:
        print(f"  FAILED : {', '.join(_FAIL)}")
        print(f"{'=' * 44}\n")
        sys.exit(1)
    print("  ALL TESTS PASSED")
    print(f"{'=' * 44}\n")
    sys.exit(0)
