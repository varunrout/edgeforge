"""Pillar B in-play model (D-056): segments, fit recovery, pricing, clock and leakage."""

import inspect

import numpy as np
import pandas as pd
from scipy.stats import poisson

from edgeforge.evaluation import inplay_model, phase7_run
from edgeforge.evaluation.inplay_model import (
    BIN_EDGES_S,
    Params,
    build_segments,
    fit_params,
    price,
    state_index,
    time_bin,
)
from edgeforge.evaluation.phase7_run import states


def _matches(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "sb_match_id": np.arange(1, n + 1), "home": "H", "away": "A",
        "match_length_s": [5700.0, 5800.0, 5600.0][:n], "lam_h": 1.5, "lam_a": 1.1,
    })  # fmt: skip


def _events() -> pd.DataFrame:
    return pd.DataFrame({
        "sb_match_id": [1, 1, 1, 2, 2],
        "kind": ["goal", "red_card", "own_goal", "goal", "second_yellow"],
        "team": ["H", "A", "A", "A", "A"],  # own_goal credited to A means a goal for A
        "elapsed_s": [1000.0, 2000.0, 3000.0, 100.0, 4000.0],
    })  # fmt: skip


def test_segments_cover_each_match_exactly_and_count_every_goal_once() -> None:
    m, e = _matches(), _events()
    seg = build_segments(m, e, 5700.0)
    expo = seg.groupby(["sb_match_id", "side"])["exposure_s"].sum()
    for mid, length in zip(m["sb_match_id"], m["match_length_s"], strict=True):
        assert np.allclose(expo.loc[mid].to_numpy(), length)
    goals = seg.groupby(["sb_match_id", "side"])["y"].sum()
    assert goals.loc[(1, 0)] == 1 and goals.loc[(1, 1)] == 1 and goals.loc[(2, 1)] == 1
    # state is from events at or before the segment start: after the home goal at 1000 s the
    # home team leads by one in the segment starting then, the away team trails
    s1 = seg[(seg["sb_match_id"] == 1)]
    later = s1[s1["exposure_s"] > 0]
    assert (later[(later["side"] == 0) & (later["state"] == 3)].shape[0]) >= 1
    # a red card against A (side 1) is "own" for A and "opponent" for H afterwards
    assert s1[(s1["side"] == 0) & (s1["opp_red"] == 1)].shape[0] >= 1
    assert s1[(s1["side"] == 1) & (s1["own_red"] == 1)].shape[0] >= 1
    assert (seg["bin"] == time_bin(0.0)).any() and set(seg["bin"]) <= set(range(len(BIN_EDGES_S)))


def test_fit_recovers_planted_multipliers() -> None:
    rng = np.random.default_rng(4)
    n = 60000
    seg = pd.DataFrame({
        "sb_match_id": 1, "side": 0, "exposure_s": 300.0, "rate0": 1.4 / 5700.0,
        "bin": rng.integers(0, 7, n), "state": rng.integers(0, 5, n),
        "own_red": rng.binomial(1, 0.05, n), "opp_red": rng.binomial(1, 0.05, n),
    })  # fmt: skip
    beta = np.log(np.array([0.8, 1.0, 0.9, 1.0, 1.0, 1.1, 1.2]))
    gamma = np.array([0.15, 0.08, 0.0, -0.05, -0.1])
    mu = (
        seg["exposure_s"]
        * seg["rate0"]
        * np.exp(
            beta[seg["bin"]] + gamma[seg["state"]] - 0.4 * seg["own_red"] + 0.5 * seg["opp_red"]
        )
    )
    seg["y"] = rng.poisson(mu)
    p = fit_params(seg, 0.0, np.array([5700.0] * 5))
    assert np.allclose(p.beta, beta, atol=0.1)
    assert np.allclose(p.gamma, gamma, atol=0.1)
    assert abs(p.delta_own + 0.4) < 0.15 and abs(p.delta_opp - 0.5) < 0.15


def _flat(l_bar: float = 5700.0) -> Params:
    return Params(np.zeros(7), np.zeros(5), 0.0, 0.0, 0.0, l_bar, np.full(20, l_bar))


def test_flat_model_reproduces_the_poisson_remainder_and_probabilities_sum_to_one() -> None:
    p = _flat()
    out = price(
        p,
        np.array([1.5]),
        np.array([1.1]),
        np.array([1]),
        np.array([0]),
        np.array([0]),
        np.array([0]),
        np.array([900.0]),
    )
    lam_rem = (1.5 + 1.1) * (5700 - 900) / 5700
    assert abs(out["exp_remaining"][0] - lam_rem) < 0.01  # cap at 8 goals per team is immaterial
    assert abs(out["over_2.5"][0] - poisson.sf(2 - 1, lam_rem)) < 0.002  # one goal already scored
    assert abs(out["1x2"].sum() - 1.0) < 1e-9


def test_an_opponent_dismissal_helps_and_the_clock_ordering_is_monotone() -> None:
    base = Params(np.zeros(7), np.zeros(5), -0.4, 0.5, 0.0, 5700.0, np.full(20, 5700.0))
    args = (np.array([1.4]), np.array([1.2]), np.array([0]), np.array([0]))
    plain = price(base, *args, np.array([0]), np.array([0]), np.array([3600.0]))
    away_red = price(base, *args, np.array([0]), np.array([1]), np.array([3600.0]))
    assert away_red["1x2"][0, 0] > plain["1x2"][0, 0]
    late = price(base, *args, np.array([0]), np.array([0]), np.array([5100.0]))
    assert late["exp_remaining"][0] < plain["exp_remaining"][0]
    assert state_index(np.array([-5, -1, 0, 1, 7])).tolist() == [0, 1, 2, 3, 4]


def test_state_at_t_uses_only_events_strictly_before_t_and_never_the_minute_field() -> None:
    m = _matches(1)
    e = pd.DataFrame(
        {
            "sb_match_id": [1, 1],
            "kind": ["goal", "goal"],
            "team": ["H", "H"],
            "elapsed_s": [900.0, 901.0],
        }
    )
    assert states(m, e, 900.0).iloc[0]["h0"] == 0  # a goal at exactly t is not yet known
    assert states(m, e, 901.0).iloc[0]["h0"] == 1
    assert states(m, e, 5000.0).iloc[0]["h0"] == 2
    for mod in (inplay_model, phase7_run):
        src = inspect.getsource(mod)
        assert '"minute"' not in src and "['minute']" not in src
