"""Margin application and fair odds (Phase 8, D-053/D-055).

`apply_margin` turns fair probabilities into quoted probabilities whose sum is 1 + overround.
Three allocations: proportional (every price loaded by the same factor), power (p_i ** k) and
odds ratio (the odds of every outcome divided by a common c), which load more of the margin on
long shots. Quoted probabilities are capped just below 1.
"""

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
METHODS = ("proportional", "power", "odds_ratio")
Q_CAP = 0.9999


def fair_odds(p: FloatArray) -> FloatArray:
    """Decimal odds of fair probabilities."""
    out: FloatArray = 1.0 / np.clip(np.asarray(p, dtype=float), 1e-9, None)
    return out


def _solve(f: object, lo: float, hi: float, target: float) -> float:
    """Bisection for a decreasing function f on [lo, hi] with f(x) == target."""
    assert callable(f)
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if f(mid) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def apply_margin(p: FloatArray, overround: float, method: str = "proportional") -> FloatArray:
    """Quoted probabilities for one market (1-D array summing to 1) with the given overround."""
    p = np.asarray(p, dtype=float)
    if overround < 0:
        raise ValueError("overround must be >= 0")
    if not np.isclose(p.sum(), 1.0, atol=1e-6):
        raise ValueError("fair probabilities must sum to 1")
    if method not in METHODS:
        raise ValueError(f"unknown margin method {method!r}")
    pc = np.clip(p, 1e-9, 1 - 1e-9)
    if overround == 0:
        return pc / pc.sum()
    target = 1.0 + overround
    if method == "proportional":
        q = pc * target
    elif method == "power":
        k = _solve(lambda k: float(np.sum(pc**k)), 1e-4, 1.0, target)
        q = pc**k
    else:
        c = _solve(lambda c: float(np.sum(pc / (pc + c * (1 - pc)))), 1e-6, 1.0, target)
        q = pc / (pc + c * (1 - pc))
    out: FloatArray = np.minimum(q, Q_CAP)
    return out


def quoted_odds(p: FloatArray, overround: float, method: str = "proportional") -> FloatArray:
    return fair_odds(apply_margin(p, overround, method))


def two_way(p_yes: float, overround: float, method: str = "odds_ratio") -> tuple[float, float]:
    """Quoted (yes, no) probabilities of a two-way prop."""
    q = apply_margin(np.array([p_yes, 1.0 - p_yes]), overround, method)
    return float(q[0]), float(q[1])
