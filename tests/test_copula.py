"""D-063 B2: the Gaussian-copula baseline reduces to the product at rho 0 and matches scipy."""

import numpy as np
import pytest
from scipy.stats import multivariate_normal, norm

from edgeforge.evaluation.phase5_copula import copula_prob, rho_grid

N = 20000


def _p(k: int, n: int = 6) -> np.ndarray:
    rng = np.random.default_rng(3)
    return rng.uniform(0.05, 0.9, (n, k))


def test_rho_zero_is_the_naive_product() -> None:
    p = _p(4)
    assert copula_prob(p, 0.0, N) == pytest.approx(np.prod(p, axis=1), rel=1e-9)


@pytest.mark.parametrize("rho", [0.3, 0.7, -0.4])
def test_two_leg_probability_matches_scipy_bivariate_normal(rho: float) -> None:
    p = _p(2)
    z = norm.ppf(p)
    cov = np.array([[1.0, rho], [rho, 1.0]])
    ref = np.array([multivariate_normal(mean=[0, 0], cov=cov).cdf(zz) for zz in z])
    assert copula_prob(p, rho, N) == pytest.approx(ref, abs=2e-6)


def test_three_leg_probability_matches_scipy_and_positive_dependence_raises_the_joint() -> None:
    p = _p(3)
    rho = 0.5
    cov = np.full((3, 3), rho) + np.eye(3) * (1 - rho)
    ref = np.array([multivariate_normal(mean=[0, 0, 0], cov=cov).cdf(z) for z in norm.ppf(p)])
    got = copula_prob(p, rho, N)
    assert got == pytest.approx(ref, abs=2e-5)
    assert (got > np.prod(p, axis=1)).all()


def test_grids_and_the_negative_correlation_limit() -> None:
    assert rho_grid(2)[0] == -0.95 and rho_grid(3)[0] == 0.0 and rho_grid(5)[-1] == 0.95
    with pytest.raises(ValueError):
        copula_prob(_p(3), -0.2, N)
