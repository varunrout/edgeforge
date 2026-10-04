"""Probabilistic-forecast metrics. Per-sample losses are returned so that paired bootstrap CIs
(resampling matches) can be computed for any model-vs-model comparison."""

from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.stats import poisson

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
EPS = 1e-12


def log_loss_multiclass(p: FloatArray, y: IntArray) -> FloatArray:
    """Per-sample -log p[y]; p has shape (n, k), y holds class indices."""
    out: FloatArray = -np.log(np.clip(p[np.arange(len(y)), y], EPS, 1.0))
    return out


def brier_multiclass(p: FloatArray, y: IntArray) -> FloatArray:
    onehot = np.zeros_like(p)
    onehot[np.arange(len(y)), y] = 1.0
    return ((p - onehot) ** 2).sum(axis=1)


def rps(p: FloatArray, y: IntArray) -> FloatArray:
    """Ranked probability score for ordered outcomes (H, D, A), per sample."""
    k = p.shape[1]
    onehot = np.zeros_like(p)
    onehot[np.arange(len(y)), y] = 1.0
    diff = np.cumsum(p, axis=1)[:, :-1] - np.cumsum(onehot, axis=1)[:, :-1]
    out: FloatArray = (diff**2).sum(axis=1) / (k - 1)
    return out


def log_loss_binary(p: FloatArray, y: FloatArray) -> FloatArray:
    p = np.clip(p, EPS, 1.0 - EPS)
    return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def brier_binary(p: FloatArray, y: FloatArray) -> FloatArray:
    return (p - y) ** 2


def reliability_bins(p: FloatArray, y: FloatArray, n_bins: int = 10) -> list[dict[str, float]]:
    """Equal-width bins on [0, 1]: mean predicted, observed frequency, count."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    out = []
    for b in range(n_bins):
        m = idx == b
        if m.any():
            out.append(
                {
                    "bin_lo": float(edges[b]),
                    "bin_hi": float(edges[b + 1]),
                    "mean_pred": float(p[m].mean()),
                    "obs_freq": float(y[m].mean()),
                    "count": int(m.sum()),
                }
            )
    return out


def ece(p: FloatArray, y: FloatArray, n_bins: int = 10) -> float:
    """Expected calibration error with equal-width bins (binary or one-vs-rest pooled)."""
    bins = reliability_bins(p, y, n_bins)
    n = sum(b["count"] for b in bins)
    return float(sum(b["count"] * abs(b["mean_pred"] - b["obs_freq"]) for b in bins) / n)


def multiclass_ece(p: FloatArray, y: IntArray, n_bins: int = 10) -> float:
    """ECE on the pooled one-vs-rest class probabilities."""
    onehot = np.zeros_like(p)
    onehot[np.arange(len(y)), y] = 1.0
    return ece(p.ravel(), onehot.ravel(), n_bins)


def multiclass_reliability(p: FloatArray, y: IntArray, n_bins: int = 10) -> list[dict[str, float]]:
    onehot = np.zeros_like(p)
    onehot[np.arange(len(y)), y] = 1.0
    return reliability_bins(p.ravel(), onehot.ravel(), n_bins)


def pit_values(lam: FloatArray, y: IntArray, seed: int) -> FloatArray:
    """Randomised PIT for Poisson forecasts (Czado et al.): uniform if calibrated."""
    rng = np.random.default_rng(seed)
    lo = poisson.cdf(y - 1, lam)
    hi = poisson.cdf(y, lam)
    out: FloatArray = lo + rng.uniform(size=len(y)) * (hi - lo)
    return out


def pit_histogram(pit: FloatArray, n_bins: int = 10) -> list[float]:
    counts, _ = np.histogram(pit, bins=np.linspace(0.0, 1.0, n_bins + 1))
    out: list[float] = (counts / counts.sum()).tolist()
    return out


def central_interval_coverage(lam: FloatArray, y: IntArray, level: float) -> float:
    """Share of outcomes inside the central `level` interval of the Poisson forecast.

    Interval is [ppf((1-level)/2), ppf((1+level)/2)]; for discrete forecasts coverage is at least
    `level` by construction, so over-coverage is expected and reported as such.
    """
    lo = poisson.ppf((1.0 - level) / 2.0, lam)
    hi = poisson.ppf((1.0 + level) / 2.0, lam)
    return float(np.mean((y >= lo) & (y <= hi)))


def mae_rmse(pred: FloatArray, y: FloatArray) -> dict[str, float]:
    err = pred - y
    return {"mae": float(np.abs(err).mean()), "rmse": float(np.sqrt((err**2).mean()))}


def paired_bootstrap(
    loss_a: FloatArray,
    loss_b: FloatArray,
    groups: NDArray[Any],
    n_boot: int,
    seed: int,
) -> dict[str, float]:
    """Paired bootstrap of mean(loss_a - loss_b), resampling groups (matches).

    Negative differences favour model A. Returns the point difference, a 95% percentile CI and
    the share of resamples in which A has the lower mean loss.
    """
    diff = np.asarray(loss_a, dtype=float) - np.asarray(loss_b, dtype=float)
    _, inv = np.unique(groups, return_inverse=True)
    n_groups = int(inv.max()) + 1
    sums = np.bincount(inv, weights=diff, minlength=n_groups)
    cnts = np.bincount(inv, minlength=n_groups).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_groups, size=(n_boot, n_groups))
    means = sums[draws].sum(axis=1) / cnts[draws].sum(axis=1)
    return {
        "mean_diff": float(diff.mean()),
        "ci_low": float(np.percentile(means, 2.5)),
        "ci_high": float(np.percentile(means, 97.5)),
        "prob_a_better": float((means < 0).mean()),
        "n_samples": int(len(diff)),
        "n_groups": n_groups,
    }
