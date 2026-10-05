"""Unit tests for the Phase 4 participation and shot models and the D-040 ordering rule."""

import inspect

import numpy as np
import pandas as pd
import pytest

from edgeforge.evaluation import phase4, phase4_props
from edgeforge.evaluation.tuning import GridEdgeError, Tuned
from edgeforge.models import participation as P
from edgeforge.models import shots_model as S


def test_event_bins_and_expansion_cover_the_risk_set() -> None:
    kind = pd.Series(["substitution", None, "red_card"])
    exit_s = pd.Series([62 * 60.0, None, 3 * 60.0])
    ev = P.starter_event_bins(kind, exit_s)
    assert list(ev) == [12, P.N_BINS, 0]
    h = P.StarterHazard(1.0)
    idx, bins, y = h._expand(pd.DataFrame(index=range(3)), ev)
    assert len(idx) == (12 + 1) + P.N_BINS + 1  # risk set per row
    assert y.sum() == 2  # one event per row that had one
    assert y[np.cumsum([13, P.N_BINS, 1])[[0, 2]] - 1].tolist() == [1.0, 1.0]


def test_bench_event_bins_only_for_appearances() -> None:
    ev = P.bench_event_bins(pd.Series([True, False]), pd.Series([70 * 60.0, None]))
    assert list(ev) == [14, P.N_BINS]


def test_minutes_grids_have_one_entry_per_pmf_column() -> None:
    assert len(P.starter_minutes_grid(95.0)) == P.N_BINS + 1
    assert len(P.bench_minutes_grid(95.0)) == P.N_BINS
    assert P.starter_minutes_grid(95.0)[-1] == 95.0
    assert (P.bench_minutes_grid(95.0) >= 1.0).all()


def test_nb_pmf_is_a_distribution_and_alpha_zero_is_poisson() -> None:
    mu = np.array([0.2, 1.0, 2.5])
    for alpha in (0.0, 0.3):
        pmf = S.nb_pmf(mu, alpha)
        # support is truncated at K_MAX; consumers renormalise by the covered mass
        assert np.allclose(pmf.sum(axis=1), 1.0, atol=1e-3)
        assert np.allclose((pmf * np.arange(S.K_MAX + 1)).sum(axis=1), mu, rtol=1e-3)
    assert np.allclose(S.nb_pmf(mu, 1e-12), S.nb_pmf(mu, 0.0))
    assert (
        S.nb_pmf(mu, 0.5)[:, 8:].sum(axis=1) > S.nb_pmf(mu, 0.0)[:, 8:].sum(axis=1)
    ).all()  # fatter tail


def test_mixture_with_point_mass_equals_plugin() -> None:
    rate = np.array([0.02, 0.05])
    grid = np.array([30.0, 60.0, 90.0])
    w = np.array([[0, 1, 0], [0, 0, 1]], dtype=float)
    mix = S.mixture_pmf(rate, grid, w, 0.1)
    assert np.allclose(mix, S.plugin_pmf(rate, np.array([60.0, 90.0]), 0.1))


def test_market_probabilities_are_ordered_and_consistent() -> None:
    pmf = S.nb_pmf(np.array([0.4, 1.5]), 0.1)
    p1, p2, p3 = S.p_ge(pmf, 1), S.p_ge(pmf, 2), S.p_ge(pmf, 3)
    assert (p1 > p2).all() and (p2 > p3).all()
    # every shot on target: Beta prior concentrated near 1 gives SoT >= k close to shots >= k
    ab, bb = np.full(2, 1e6), np.full(2, 1e-6)
    assert np.allclose(S.p_sot_ge(pmf, ab, bb, 1), p1, atol=1e-3)
    assert (S.p_sot_ge(pmf, np.full(2, 3.0), np.full(2, 7.0), 2) < p2).all()
    # goal thinning: g = 1 turns anytime scorer into shots >= 1, g = 0 into ~0
    assert np.allclose(S.p_goal_ge1(pmf, np.ones(2)), p1, atol=1e-5)
    assert (S.p_goal_ge1(pmf, np.zeros(2)) < 1e-5).all()


def test_pooled_priors_replace_groups_with_too_few_shots() -> None:
    gr = pd.DataFrame(
        {
            "sb_match_id": [1, 1],
            "position_group": ["GK", "FWD"],
            "g_shots": [0.0, 400.0],
            "g_sot": [0.0, 150.0],
            "g_xg": [0.0, 40.0],
            "g_goals": [0.0, 42.0],
            "g_expo": [10.0, 90.0],
            "g_minutes": [900.0, 8100.0],
        }
    )
    df = pd.DataFrame({"sb_match_id": [1, 1], "position_group": ["GK", "FWD"]})
    out = S.attach_group_priors(df, gr)
    assert out[["omega_g", "rho_g", "xgps_g"]].notna().all().all()
    assert out.loc[0, "rho_g"] == pytest.approx(150 / 400)  # GK falls back to the pooled rate


def test_d040_test_window_code_only_accepts_verified_tuned_values() -> None:
    """The test-window entry points take plain, already-tuned values, and tuning routines build
    them through tune_interior/require_tuned, so an edge optimum raises before test code exists."""
    src_a = inspect.getsource(phase4.stage_starters)
    assert src_a.index("require_tuned") < src_a.index('d.cand["split"] == "test"')
    src_m = inspect.getsource(phase4.stage_minutes)
    assert "require_tuned(t_s)" in src_m and "require_tuned(t_b)" in src_m
    src_p = inspect.getsource(phase4_props.tune_player_params)
    assert src_p.count("require_tuned") == src_p.count("tune_interior(") == 5
    edge = Tuned("x", 1.0, interior=False, at_natural_bound=False, history=(), n_widenings=0)
    with pytest.raises(GridEdgeError):
        phase4.require_tuned(edge)


def test_every_phase4_tuning_call_site_uses_tune_interior() -> None:
    for mod in (phase4, phase4_props):
        text = inspect.getsource(mod)
        assert "min(grid" not in text and "argmin" not in text.replace("np.argmin", "")
