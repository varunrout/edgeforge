"""Pillar C helpers (D-051): bands, informed-bettor rule, neutralising margin."""

import numpy as np

from edgeforge.evaluation.phase6_pillar_c import (
    band_of,
    bets,
    margin_grid,
    neutralising_margin,
    shock_stats,
)


def test_bands_follow_d051_edges() -> None:
    edges = [0.5, 0.85]
    assert band_of(0.49, edges) == "rotation"
    assert band_of(0.50, edges) == "likely" and band_of(0.85, edges) == "likely"
    assert band_of(0.851, edges) == "nailed"


def test_informed_bettor_bets_only_above_break_even_and_payoffs_are_right() -> None:
    p_o = np.array([0.40, 0.40, 0.40])
    p_l = np.array([0.50, 0.43, 0.30])
    y = np.array([1.0, 0.0, 1.0])
    b = bets(p_o, p_l, y, 0.05, two_way=True)  # break-even for yes is 0.42
    assert list(b["n_bets"]) == [1, 1, 1]  # third: no side, 1-0.30=0.70 > 0.60*1.05=0.63
    assert np.isclose(b["real"][0], 1 / 0.42 - 1)  # yes wins
    assert np.isclose(b["real"][1], -1.0)  # yes loses (p_l 0.43 > 0.42)
    assert np.isclose(b["real"][2], 1 / 0.63 * 0.0 - 1)  # no side loses because y = 1
    assert (b["exp"] > 0).all()  # every placed bet has positive expected profit under p_l
    one_way = bets(p_o, p_l, y, 0.05, two_way=False)
    assert list(one_way["n_bets"]) == [1, 1, 0]  # SGAs: yes side only


def test_no_bets_when_margin_exceeds_the_shock_and_profit_falls_with_margin() -> None:
    rng = np.random.default_rng(0)
    p_o = rng.uniform(0.1, 0.6, 5000)
    p_l = np.clip(p_o * np.exp(rng.normal(0, 0.15, 5000)), 0.01, 0.99)
    y = (rng.random(5000) < p_l).astype(float)
    prof = [bets(p_o, p_l, y, m, True)["exp"].sum() / 5000 for m in (0.0, 0.1, 0.3, 0.8)]
    assert prof[0] > prof[1] > prof[2] >= prof[3] == 0.0
    # realised profit of an informed bettor whose probabilities are calibrated is positive at m = 0
    assert bets(p_o, p_l, y, 0.0, True)["real"].sum() > 0


def test_neutralising_margin_is_the_first_grid_point_at_or_below_epsilon() -> None:
    curve = [
        {"margin": m, "informed_expected_per_offered": v}
        for m, v in zip([0, 0.1, 0.2, 0.3], [0.05, 0.01, 0.0009, 0.0], strict=True)
    ]
    assert neutralising_margin(curve, 0.001) == 0.2
    assert np.isnan(neutralising_margin(curve[:2], 0.001))
    g = margin_grid({"margin_step": 0.01, "margin_max": 0.6})
    assert g[0] == 0.0 and g[-1] == 0.6 and len(g) == 61


def test_shock_shares_use_relative_change_in_fair_odds() -> None:
    import pandas as pd

    d = pd.DataFrame({"p_o": [0.20, 0.20, 0.20, 0.20], "p_l": [0.20, 0.25, 0.10, 0.02]})
    s = shock_stats(d)
    # relative odds change = p_o/p_l - 1: 0, -0.2, +1.0, +9.0
    assert s["share_abs_odds_change_gt_10pct"] == 0.75
    assert s["share_abs_odds_change_gt_25pct"] == 0.5
    assert s["share_abs_odds_change_gt_50pct"] == 0.5
