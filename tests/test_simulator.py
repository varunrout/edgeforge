"""Simulator invariants (D-014) and SGA engine behaviour."""

import numpy as np
import pytest

from edgeforge.models.dixon_coles import dc_grid
from edgeforge.sga.engine import (
    BTTS,
    SGA,
    PlayerAGS,
    PlayerShots,
    PlayerSoT,
    SGAError,
    TeamResult,
    Total,
    price_sga,
    validate_sga,
)
from edgeforge.simulation.engine import (
    N_MIN_COLS,
    MatchInputs,
    ShotsGivenGoals,
    simulate,
)

MODEL = ShotsGivenGoals(a=-0.9, b=1.0, c=0.15, d=-0.03, alpha=0.1, pi_og=0.02)
LAM_H, LAM_A, RHO = 1.6, 1.1, -0.1


def _inputs(sampled: bool = False, p_per_team: int = 14) -> MatchInputs:
    rng = np.random.default_rng(3)
    p = 2 * p_per_team
    team = np.repeat([0, 1], p_per_team)
    pos = np.tile(np.arange(p_per_team), 2)
    is_start = pos < 11
    ws = np.zeros((p, N_MIN_COLS))
    ws[:, -1] = 0.8
    ws[:, 12] = 0.2
    gs = np.tile(np.linspace(5, 95, N_MIN_COLS), (p, 1))
    wb = np.zeros((p, N_MIN_COLS))
    wb[:, 5] = 1.0
    gb = np.tile(np.linspace(80, 10, N_MIN_COLS), (p, 1))
    if sampled:
        p_start = np.where(is_start, 0.9, 0.1)
        q = np.where(is_start, 0.0, 0.4)
    else:
        p_start = is_start.astype(float)
        q = np.where(is_start, 0.0, 0.4)
    return MatchInputs(
        match_id=7,
        state="opening" if sampled else "lineups",
        lam_h=LAM_H,
        lam_a=LAM_A,
        rho=RHO,
        mu_h=13.0,
        mu_a=10.0,
        player_id=np.arange(100, 100 + p),
        team=team,
        omega=rng.uniform(0.4, 1.8, p),
        g_shot=rng.uniform(0.04, 0.2, p),
        sot_cond=rng.uniform(0.2, 0.5, p),
        p_start=p_start,
        q_app_bench=q,
        ws=ws,
        gs=gs,
        wb=wb,
        gb=gb,
        sampled_roles=sampled,
    )


@pytest.fixture(scope="module")
def res_lineups():  # type: ignore[no-untyped-def]
    return simulate(_inputs(False), MODEL, 20000, 11)


def test_goal_accounting_player_plus_own_goals_equals_team_goals(res_lineups) -> None:  # type: ignore[no-untyped-def]
    r = res_lineups
    for t in (0, 1):
        idx = np.flatnonzero(r.inputs.team == t)
        assert (r.goals[:, idx].sum(axis=1) + r.own_goals[:, t] == r.team_goals[:, t]).all()


def test_goals_le_sot_le_shots_and_team_shot_totals(res_lineups) -> None:  # type: ignore[no-untyped-def]
    r = res_lineups
    assert (r.goals <= r.sot).all() and (r.sot <= r.shots).all()
    for t in (0, 1):
        idx = np.flatnonzero(r.inputs.team == t)
        assert (r.shots[:, idx].sum(axis=1) == r.team_shots[:, t]).all()
        assert (r.team_shots[:, t] >= r.team_goals[:, t] - r.own_goals[:, t]).all()


def test_off_pitch_players_record_nothing(res_lineups) -> None:  # type: ignore[no-untyped-def]
    r = res_lineups
    off = ~r.on_pitch
    assert off.any()
    assert (r.shots[off] == 0).all() and (r.sot[off] == 0).all() and (r.goals[off] == 0).all()
    assert (r.minutes[off] == 0).all() and (r.minutes[r.on_pitch] > 0).all()


def test_team_marginals_equal_dixon_coles_within_mc_error(res_lineups) -> None:  # type: ignore[no-untyped-def]
    r = res_lineups
    grid = dc_grid(np.array([LAM_H]), np.array([LAM_A]), RHO, 10)[0]
    n = r.n_sims
    cases = {
        "home_win": (np.tril(grid, -1).sum(), r.team_goals[:, 0] > r.team_goals[:, 1]),
        "draw": (np.trace(grid), r.team_goals[:, 0] == r.team_goals[:, 1]),
        "btts": (grid[1:, 1:].sum(), (r.team_goals > 0).all(axis=1)),
        "over25": (
            sum(grid[i, j] for i in range(11) for j in range(11) if i + j > 2),
            r.team_goals.sum(axis=1) > 2.5,
        ),
    }
    for name, (p, ind) in cases.items():
        se = np.sqrt(p * (1 - p) / n)
        assert abs(ind.mean() - p) < 4 * se, name


def test_fixed_lineups_have_eleven_starters_and_sampled_opening_exactly_eleven() -> None:
    r_fix = simulate(_inputs(False), MODEL, 2000, 1)
    r_open = simulate(_inputs(True), MODEL, 2000, 1)
    for r in (r_fix, r_open):
        for t in (0, 1):
            idx = np.flatnonzero(r.inputs.team == t)
            assert (r.started[:, idx].sum(axis=1) == 11).all()
    # opening: some bench players start in some sims
    assert r_open.started[:, 11:14].any()


def test_simulation_is_deterministic_from_seed_match_state() -> None:
    a = simulate(_inputs(True), MODEL, 500, 5)
    b = simulate(_inputs(True), MODEL, 500, 5)
    c = simulate(_inputs(True), MODEL, 500, 6)
    assert (a.goals == b.goals).all() and (a.minutes == b.minutes).all()
    assert not (a.shots == c.shots).all()


def test_scoring_dependence_signs(res_lineups) -> None:  # type: ignore[no-untyped-def]
    r = res_lineups
    pid = int(r.inputs.player_id[0])
    p_win = price_sga(SGA(legs=[TeamResult(result="home"), PlayerAGS(player_id=pid)]), r)
    assert p_win["adjustment_ratio"] > 1.0  # own scorer goes with own win
    opp = int(r.inputs.player_id[20])
    p_opp = price_sga(SGA(legs=[TeamResult(result="home"), PlayerAGS(player_id=opp)]), r)
    assert p_opp["adjustment_ratio"] < 1.0  # an away scorer makes a home win less likely
    both = price_sga(
        SGA(legs=[PlayerAGS(player_id=pid), PlayerAGS(player_id=int(r.inputs.player_id[1]))]), r
    )
    # teammates both share the team's goal count (positive) and compete for its goals (negative);
    # the sign is an empirical question settled by the joint validation, not asserted here
    assert 0.5 < both["adjustment_ratio"] < 1.5


def test_joint_probability_bounds_and_se(res_lineups) -> None:  # type: ignore[no-untyped-def]
    r = res_lineups
    pid = int(r.inputs.player_id[2])
    out = price_sga(SGA(legs=[Total(line=2.5, side="over"), PlayerSoT(player_id=pid, k=1)]), r)
    assert 0 < out["joint"] <= min(out["leg_probabilities"])
    assert out["joint_mc_se"] == pytest.approx(
        np.sqrt(out["joint"] * (1 - out["joint"]) / out["n_effective_sims"])
    )


def test_sga_validation_rejects_impossible_and_contradictory() -> None:
    team = {1: 0, 2: 0, 3: 1}
    bad = [
        [Total(line=0.5, side="under"), PlayerAGS(player_id=1)],
        [BTTS(side="yes"), Total(line=1.5, side="under")],
        [BTTS(side="yes"), BTTS(side="no")],
        [Total(line=2.5, side="over"), Total(line=2.5, side="under")],
        [TeamResult(result="home"), TeamResult(result="away")],
        [PlayerAGS(player_id=1), PlayerAGS(player_id=99)],  # not in squad
        [PlayerAGS(player_id=1), PlayerAGS(player_id=1)],  # duplicate
        [PlayerAGS(player_id=1), PlayerSoT(player_id=1, k=1)],  # redundant
        [PlayerSoT(player_id=1, k=2), PlayerShots(player_id=1, k=2)],  # redundant
        [BTTS(side="no"), PlayerAGS(player_id=1), PlayerAGS(player_id=3)],
        [TeamResult(result="home"), Total(line=0.5, side="under")],
    ]
    for legs in bad:
        with pytest.raises(SGAError):
            validate_sga(SGA(legs=legs), team)
    ok = SGA(legs=[TeamResult(result="home"), PlayerAGS(player_id=1), PlayerSoT(player_id=1, k=2)])
    validate_sga(ok, team)
    with pytest.raises(ValueError):
        Total(line=2.0, side="over")  # integer lines are not supported
