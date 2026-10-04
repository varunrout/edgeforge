"""Team markets from a bivariate score grid (independent Poisson goals)."""

import numpy as np
from numpy.typing import NDArray
from scipy.stats import poisson

FloatArray = NDArray[np.float64]
MAX_GOALS = 12
OU_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)


def score_grid(lam_h: FloatArray, lam_a: FloatArray, max_goals: int = MAX_GOALS) -> FloatArray:
    """Joint probabilities, shape (n, G+1, G+1): [i, h, a]; renormalised for truncation."""
    k = np.arange(max_goals + 1)
    ph = poisson.pmf(k[None, :], np.asarray(lam_h)[:, None])
    pa = poisson.pmf(k[None, :], np.asarray(lam_a)[:, None])
    grid = ph[:, :, None] * pa[:, None, :]
    return grid / grid.sum(axis=(1, 2), keepdims=True)  # type: ignore[no-any-return]


def market_probs(grid: FloatArray) -> dict[str, FloatArray]:
    """1X2 (columns H, D, A), Over lines 0.5..4.5 on total goals, and BTTS yes."""
    g = grid.shape[1]
    h = np.arange(g)[:, None]
    a = np.arange(g)[None, :]
    home = (grid * (h > a)).sum(axis=(1, 2))
    draw = (grid * (h == a)).sum(axis=(1, 2))
    away = (grid * (h < a)).sum(axis=(1, 2))
    tot = h + a
    out: dict[str, FloatArray] = {"1x2": np.stack([home, draw, away], axis=1)}
    for line in OU_LINES:
        out[f"over_{line}"] = (grid * (tot > line)).sum(axis=(1, 2))
    out["btts"] = (grid * ((h >= 1) & (a >= 1))).sum(axis=(1, 2))
    return out
