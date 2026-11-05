"""Pricing layer invariants: overround equals target, monotone in margin, long-shot loading."""

import numpy as np
import pytest

from edgeforge.pricing.devig import power as devig_power
from edgeforge.pricing.devig import proportional as devig_proportional
from edgeforge.pricing.margin import METHODS, apply_margin, fair_odds, quoted_odds, two_way

P3 = np.array([0.55, 0.25, 0.20])
P2 = np.array([0.30, 0.70])


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("m", [0.0, 0.05, 0.10, 0.15, 0.20])
@pytest.mark.parametrize("p", [P3, P2, np.array([0.10, 0.20, 0.30, 0.40])])
def test_overround_equals_target(method: str, m: float, p: np.ndarray) -> None:
    q = apply_margin(p, m, method)
    assert q.sum() == pytest.approx(1.0 + m, abs=1e-6)
    assert (q >= p - 1e-9).all()


@pytest.mark.parametrize("method", METHODS)
def test_quoted_probabilities_are_monotone_in_margin(method: str) -> None:
    prev = apply_margin(P3, 0.0, method)
    for m in (0.05, 0.10, 0.15, 0.20):
        cur = apply_margin(P3, m, method)
        assert (cur > prev).all()
        prev = cur


def test_zero_margin_returns_the_fair_probabilities() -> None:
    for method in METHODS:
        assert np.allclose(apply_margin(P3, 0.0, method), P3, atol=1e-6)
    assert np.allclose(fair_odds(P3), 1 / P3)


def test_power_and_odds_ratio_load_more_on_long_shots_than_proportional() -> None:
    prop = apply_margin(P3, 0.10, "proportional") / P3
    for method in ("power", "odds_ratio"):
        rel = apply_margin(P3, 0.10, method) / P3
        assert rel[2] > prop[2] > 1.0  # the outsider is charged more than proportionally
        assert rel[0] < prop[0]  # and the favourite less


def test_margin_then_devig_round_trips() -> None:
    q = apply_margin(P3, 0.10, "proportional")
    assert np.allclose(devig_proportional((1 / q)[None, :])[0], P3, atol=1e-9)
    qp = apply_margin(P3, 0.10, "power")
    back, fb = devig_power((1 / qp)[None, :])
    assert fb == 0 and np.allclose(back[0], P3, atol=1e-6)


def test_quoted_odds_and_two_way_helpers_and_input_validation() -> None:
    assert np.allclose(quoted_odds(P3, 0.1, "proportional"), 1 / (P3 * 1.1))
    yes, no = two_way(0.25, 0.10)
    assert yes + no == pytest.approx(1.10, abs=1e-6)
    with pytest.raises(ValueError):
        apply_margin(P3, -0.01)
    with pytest.raises(ValueError):
        apply_margin(P3, 0.1, "magic")
    with pytest.raises(ValueError):
        apply_margin(np.array([0.5, 0.6]), 0.1)
