"""Phase 5 evaluation helpers: ranking, template instances, void rule, joint-validation controls."""

import numpy as np
import pandas as pd

from edgeforge.evaluation.phase5 import (
    TEMPLATE_META,
    evaluate_instance_outcome,
    joint_validation,
    rank_players,
    template_instances,
)
from edgeforge.sga.engine import SGA, PlayerAGS, TeamResult, validate_sga
from edgeforge.simulation.engine import N_MIN_COLS, MatchInputs


def _inputs() -> MatchInputs:
    p = 8
    z = np.zeros((p, N_MIN_COLS))
    return MatchInputs(
        match_id=1, state="lineups", lam_h=1.4, lam_a=1.1, rho=-0.1, mu_h=12.0, mu_a=10.0,
        player_id=np.arange(10, 10 + p), team=np.repeat([0, 1], 4), omega=np.ones(p),
        g_shot=np.full(p, 0.1), sot_cond=np.full(p, 0.3), p_start=np.ones(p),
        q_app_bench=np.zeros(p), ws=z, gs=z, wb=z, gb=z, sampled_roles=False,
    )  # fmt: skip


def test_ranking_orders_by_probability_and_breaks_ties_by_lower_player_id() -> None:
    inp = _inputs()
    s = np.array([0.2, 0.3, 0.3, 0.1, 0.05, 0.4, np.nan, 0.4])
    rank = rank_players(inp, s)
    assert rank[0] == [11, 12, 10, 13]  # tie between 11 and 12 goes to the lower id
    assert rank[1] == [15, 17, 14]  # nan is excluded; tie 15 vs 17 goes to 15


def test_all_templates_are_built_valid_and_cover_the_registered_ids() -> None:
    inp = _inputs()
    rank = rank_players(inp, np.linspace(0.5, 0.1, 8))
    inst = template_instances(rank)
    team = {int(p): int(t) for p, t in zip(inp.player_id, inp.team, strict=True)}
    assert {t for t, _, _ in inst} == set(TEMPLATE_META)
    for tpl, _, sga in inst:
        validate_sga(sga, team)
        assert len(sga.legs) == TEMPLATE_META[tpl][0]


def test_void_rule_returns_nan_when_a_named_player_did_not_appear() -> None:
    sga = SGA(legs=[TeamResult(result="home"), PlayerAGS(player_id=5)])
    appeared = {5: (True, 3, 2, 1)}
    assert evaluate_instance_outcome(sga, (2, 0), appeared) == 1.0
    assert evaluate_instance_outcome(sga, (0, 1), appeared) == 0.0
    assert np.isnan(evaluate_instance_outcome(sga, (2, 0), {5: (False, 0, 0, 0)}))
    assert np.isnan(evaluate_instance_outcome(sga, (2, 0), {}))


def _instances(sim_equals_naive: bool, n: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for tpl in ("T01", "T03"):
        for state in ("lineups", "opening"):
            truth = rng.uniform(0.1, 0.5, n)
            y = (rng.random(n) < truth).astype(float)
            naive = np.clip(truth * rng.uniform(0.6, 0.9, n), 0.01, 0.99)
            sim = naive if sim_equals_naive else truth
            rows.append(
                pd.DataFrame(
                    {
                        "sb_match_id": np.arange(n) // 2,
                        "state": state,
                        "template": tpl,
                        "n_legs": 2,
                        "relationship": "x",
                        "p_sim": sim,
                        "p_naive": naive,
                        "y": y,
                    }
                )  # fmt: skip
            )
    return pd.concat(rows, ignore_index=True)


def test_joint_validation_null_control_has_no_survivors() -> None:
    res = joint_validation(_instances(True), seed=1)
    assert res["bh_family"]["survivors_better"] == [] == res["bh_family"]["survivors_worse"]


def test_joint_validation_detects_a_genuinely_better_joint_price() -> None:
    res = joint_validation(_instances(False), seed=1)
    assert len(res["bh_family"]["survivors_better"]) == 4
    assert res["bh_family"]["survivors_worse"] == []


def test_canonical_player_order_makes_inputs_independent_of_row_order() -> None:
    from edgeforge.evaluation.phase5_inputs import canonical_order

    pid = np.array([30, 10, 20, 50, 40])
    a = canonical_order(np.array([0, 1, 2, 4]), pid)
    b = canonical_order(np.array([4, 2, 1, 0]), pid)
    assert list(pid[a]) == list(pid[b]) == [10, 20, 30, 40]
