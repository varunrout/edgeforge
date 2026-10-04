"""Bootstrap and multiple-testing helpers for the Pillar A analysis (D-037).

All inference resamples matches (clusters). A bootstrap draw is represented by a count vector
over matches, so every claim is a function of weighted sums and many claims share one weight
matrix. Ratios and logistic slopes are recomputed per draw.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


@dataclass
class Boot:
    """B bootstrap draws over n matches: counts[b, i] = times match i is drawn in draw b."""

    counts: FloatArray

    @classmethod
    def make(cls, n: int, n_boot: int, seed: int) -> "Boot":
        rng = np.random.default_rng(seed)
        draws = rng.integers(0, n, size=(n_boot, n))
        counts = np.zeros((n_boot, n), dtype=np.float32)
        rows = np.repeat(np.arange(n_boot), n)
        np.add.at(counts, (rows, draws.ravel()), 1.0)
        return cls(counts.astype(np.float64))

    @property
    def n_boot(self) -> int:
        return int(self.counts.shape[0])

    def mean(self, v: FloatArray, mask: NDArray[np.bool_] | None = None) -> FloatArray:
        """Bootstrap distribution of the mean of v over matches in mask."""
        m = np.ones(len(v), bool) if mask is None else mask
        num = self.counts @ np.where(m, v, 0.0)
        den = self.counts @ m.astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            return num / den  # type: ignore[no-any-return]


def point_mean(v: FloatArray, mask: NDArray[np.bool_] | None = None) -> float:
    m = np.ones(len(v), bool) if mask is None else mask
    return float(v[m].mean()) if m.any() else float("nan")


def summarize(point: float, draws: FloatArray, null: float) -> dict[str, float]:
    """Point estimate, 95% percentile CI and a two-sided bootstrap p-value against `null`."""
    d = draws[np.isfinite(draws)]
    if len(d) < 50 or not np.isfinite(point):
        return {"estimate": point, "ci_low": float("nan"), "ci_high": float("nan"), "p": 1.0}
    b = len(d)
    lo = (np.sum(d <= null) + 1) / (b + 1)
    hi = (np.sum(d >= null) + 1) / (b + 1)
    return {
        "estimate": float(point),
        "ci_low": float(np.percentile(d, 2.5)),
        "ci_high": float(np.percentile(d, 97.5)),
        "p": float(min(1.0, 2.0 * min(lo, hi))),
    }


def batched_logistic(
    x: FloatArray, y: FloatArray, weights: FloatArray, iters: int = 25
) -> FloatArray:
    """Weighted logistic regression y ~ sigmoid(a + b x), one fit per weight row. Returns (B, 2)."""
    w = np.atleast_2d(weights)
    n_fits = w.shape[0]
    beta = np.zeros((n_fits, 2))
    for _ in range(iters):
        eta = beta[:, :1] + beta[:, 1:] * x[None, :]
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -30, 30)))
        r = w * (y[None, :] - p)
        g0, g1 = r.sum(axis=1), (r * x[None, :]).sum(axis=1)
        s = w * p * (1.0 - p)
        h00 = s.sum(axis=1) + 1e-8
        h01 = (s * x[None, :]).sum(axis=1)
        h11 = (s * x[None, :] ** 2).sum(axis=1) + 1e-8
        det = h00 * h11 - h01**2
        det = np.where(np.abs(det) < 1e-12, 1e-12, det)
        step0 = (h11 * g0 - h01 * g1) / det
        step1 = (-h01 * g0 + h00 * g1) / det
        beta = beta + np.stack([step0, step1], axis=1)
        if max(np.abs(step0).max(), np.abs(step1).max()) < 1e-8:
            break
    return beta


def benjamini_hochberg(p: list[float], q: float = 0.10) -> tuple[list[float], list[bool]]:
    """Adjusted q-values and survival flags at false discovery rate q."""
    m = len(p)
    order = np.argsort(p)
    ranked = np.asarray(p)[order]
    adj = ranked * m / (np.arange(m) + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    adj = np.minimum(adj, 1.0)
    out = np.empty(m)
    out[order] = adj
    return out.tolist(), (out <= q).tolist()
