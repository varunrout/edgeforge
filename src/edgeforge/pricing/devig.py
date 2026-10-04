"""Margin removal for decimal odds: proportional, power and Shin.

Each function takes an array of shape (n, k) of decimal odds (one row per market) and returns
fair probabilities of the same shape. Rows where a method cannot be solved fall back to the
proportional method; the number of fallbacks is returned alongside.
"""

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import brentq

FloatArray = NDArray[np.float64]


def proportional(odds: FloatArray) -> FloatArray:
    p = 1.0 / np.asarray(odds, dtype=float)
    out: FloatArray = p / p.sum(axis=1, keepdims=True)
    return out


def _power_row(p: FloatArray) -> FloatArray:
    # find k with sum(p_i ** k) == 1; p_i in (0, 1) so the sum is decreasing in k
    f = lambda k: float(np.sum(p**k) - 1.0)  # noqa: E731
    k = brentq(f, 1e-3, 100.0)
    out: FloatArray = p**k
    return out


def power(odds: FloatArray) -> tuple[FloatArray, int]:
    p = 1.0 / np.asarray(odds, dtype=float)
    out = np.empty_like(p)
    fallbacks = 0
    for i in range(len(p)):
        try:
            out[i] = _power_row(p[i])
        except (ValueError, RuntimeError):
            out[i] = p[i] / p[i].sum()
            fallbacks += 1
    return out, fallbacks


def _shin_row(p: FloatArray) -> FloatArray:
    big_b = float(p.sum())

    def probs(z: float) -> FloatArray:
        return (np.sqrt(z * z + 4.0 * (1.0 - z) * p * p / big_b) - z) / (2.0 * (1.0 - z))

    f = lambda z: float(probs(z).sum() - 1.0)  # noqa: E731
    if big_b <= 1.0:  # no overround: nothing to remove
        return p / big_b
    z = brentq(f, 1e-12, 0.999999)
    out = probs(z)
    return out / out.sum()


def shin(odds: FloatArray) -> tuple[FloatArray, int]:
    p = 1.0 / np.asarray(odds, dtype=float)
    out = np.empty_like(p)
    fallbacks = 0
    for i in range(len(p)):
        try:
            out[i] = _shin_row(p[i])
        except (ValueError, RuntimeError):
            out[i] = p[i] / p[i].sum()
            fallbacks += 1
    return out, fallbacks


def devig(odds: FloatArray, method: str) -> tuple[FloatArray, int]:
    """Dispatch by method name: 'proportional', 'power', 'shin'. Returns (probs, fallbacks)."""
    if method == "proportional":
        return proportional(odds), 0
    if method == "power":
        return power(odds)
    if method == "shin":
        return shin(odds)
    raise ValueError(f"unknown de-vig method {method!r}")
