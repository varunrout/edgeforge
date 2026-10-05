"""D-043 / D-044 adoption rules and the D-046 ci-local helpers."""

import numpy as np
import pandas as pd
import pytest

from edgeforge.ci_local import _counts
from edgeforge.evaluation.phase4_recal import rescale_pmf
from edgeforge.evaluation.team_model import MARKETS, calibration_study, choose_calibrators


def _tune_frame(n: int, miscalibrated: bool, seed: int = 0) -> tuple[pd.DataFrame, dict, dict]:
    rng = np.random.default_rng(seed)
    season = np.repeat([2013, 2014], n // 2)
    true_p = rng.uniform(0.15, 0.85, n)
    y = (rng.random(n) < true_p).astype(float)
    shown = np.clip(0.5 + (true_p - 0.5) * 2.5, 0.02, 0.98) if miscalibrated else true_p
    probs: dict[str, np.ndarray] = {}
    ys: dict[str, object] = {}
    for mk in MARKETS:
        if mk == "1x2":
            probs[mk] = np.stack([shown * 0 + 0.3, shown * 0 + 0.3, shown * 0 + 0.4], axis=1)
            ys[mk] = rng.integers(0, 3, n)
        else:
            probs[mk] = shown
            ys[mk] = y
    return pd.DataFrame({"season_start_year": season}), probs, ys


def test_d043_adopts_only_what_the_choose_window_supports() -> None:
    ev, probs, ys = _tune_frame(6000, miscalibrated=True)
    res = choose_calibrators(ev, probs, ys, seed=1)
    adopted = {(r["market"], r["method"]) for r in res if r["adopt"]}
    assert ("btts", "platt") in adopted
    assert all(r["choose_season_start_year"] == 2014 for r in res)
    ev, probs, ys = _tune_frame(6000, miscalibrated=False)
    res0 = choose_calibrators(ev, probs, ys, seed=1)
    assert not [r for r in res0 if r["adopt"] and r["method"] == "platt" and r["market"] != "1x2"]


def test_d043_adoption_is_independent_of_test_data() -> None:
    ev, probs, ys = _tune_frame(4000, miscalibrated=True)
    a = choose_calibrators(ev, probs, ys, seed=2)
    b = choose_calibrators(ev, probs, ys, seed=2)
    assert a == b  # the rule's only inputs are tuning-window arrays
    adopted = {("btts", "platt")}
    ev2, probs2, ys2 = _tune_frame(1000, miscalibrated=False, seed=9)
    out = calibration_study(probs, ys, probs2, ys2, seed=3, adopted=adopted)
    keep = {(c["market"], c["method"]) for c in out if c["keep"]}
    assert keep == adopted  # `keep` follows the tuning-window decision, not the test result


def test_d044_rescale_keeps_a_pmf_and_moves_the_event_level() -> None:
    pmf = np.array([[0.1, 0.2, 0.3, 0.4], [0.05, 0.05, 0.1, 0.8]])
    out = rescale_pmf(pmf, np.array([0.5, 0.5]))
    assert np.allclose(out.sum(axis=1), 1.0)
    assert np.allclose(1.0 - out[:, -1], 0.5)
    assert np.allclose(out[:, 0] / out[:, 1], pmf[:, 0] / pmf[:, 1])  # shape over bins kept


def test_ci_local_parses_pytest_summary() -> None:
    assert _counts("93 passed, 2 warnings in 214.84s (0:03:34)") == {"passed": 93, "warning": 2}
    assert _counts("1 failed, 8 passed in 30.44s")["failed"] == 1
    with pytest.raises(KeyError):
        _counts("no tests ran")["passed"]
