"""Player shot, shot-on-target and goal model (Phase 4, D-030 settlement).

Shots:   S ~ NegBin(mean = omega_p * mu_team * m / (11 * Lbar), overdispersion alpha), alpha = 0 is
         Poisson. omega_p is the player's shots relative to his minutes-weighted fair share of team
         shots, shrunk toward the position group with strength k (in fair-share shot units).
SoT:     SoT | S ~ BetaBinomial(S, a*rho_g + sot_p, a*(1-rho_g) + shots_p - sot_p), the posterior
         predictive of a Beta prior (strength a) around the group on-target rate rho_g.
Goals:   each shot scores with probability g_p = xGps_p * phi_p, where xGps_p is the shrunk xG per
         shot (strength b) and phi_p = (goals_p + c) / (xG_p + c) is a heavily shrunk finishing
         multiplier (strength c, in xG units). Goals | S ~ Binomial(S, g_p).

Minutes enter as a distribution (pmf over a minutes grid) or as its mean (plug-in); both are priced
so the difference can be measured.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.special import betaln, gammaln

FloatArray = NDArray[np.float64]
K_MAX = 16  # shots pmf support 0..K_MAX
MIN_GROUP_SHOTS = 20  # fewer group shots than this and the pooled priors are used


@dataclass(frozen=True)
class ShotParams:
    k: float  # shot-share shrinkage (fair-share shot units)
    alpha: float  # negative-binomial overdispersion (0 = Poisson)
    a_sot: float  # beta-binomial prior strength (shots)
    b_xg: float  # xG-per-shot shrinkage (shots)
    c_fin: float  # finishing-multiplier shrinkage (xG units)


def nb_pmf(mean: FloatArray, alpha: float, k_max: int = K_MAX) -> FloatArray:
    """pmf over 0..k_max for NegBin(mean, alpha); shape mean.shape + (k_max + 1,)."""
    mu = np.maximum(np.asarray(mean, dtype=float), 1e-12)[..., None]
    k = np.arange(k_max + 1)
    if alpha <= 1e-9:
        out: FloatArray = np.exp(k * np.log(mu) - mu - gammaln(k + 1))
        return out
    r = 1.0 / alpha
    p = r / (r + mu)
    out = np.exp(gammaln(k + r) - gammaln(k + 1) - gammaln(r) + r * np.log(p) + k * np.log1p(-p))
    return out


def mixture_pmf(
    rate_per_min: FloatArray, minutes: FloatArray, weights: FloatArray, alpha: float
) -> FloatArray:
    """pmf of the shot count when minutes ~ weights over the grid `minutes` (per row or shared)."""
    n = len(rate_per_min)
    m = np.broadcast_to(minutes, weights.shape)
    pmf = np.zeros((n, K_MAX + 1))
    for b in range(weights.shape[1]):
        w = weights[:, b]
        if not np.any(w > 0):
            continue
        pmf += w[:, None] * nb_pmf(rate_per_min * m[:, b], alpha)
    return pmf


def plugin_pmf(rate_per_min: FloatArray, minutes_mean: FloatArray, alpha: float) -> FloatArray:
    return nb_pmf(rate_per_min * minutes_mean, alpha)


def p_ge(pmf: FloatArray, k: int) -> FloatArray:
    """P(S >= k); the pmf is truncated at K_MAX, so renormalise by the covered mass."""
    cover = pmf.sum(axis=1)
    out: FloatArray = 1.0 - pmf[:, :k].sum(axis=1) / cover
    return np.clip(out, 1e-6, 1 - 1e-6)


def p_sot_ge(pmf: FloatArray, alpha_bb: FloatArray, beta_bb: FloatArray, k: int) -> FloatArray:
    """P(SoT >= k) for k in {1, 2} under the beta-binomial thinning of the shot count."""
    n = np.arange(K_MAX + 1)[None, :]
    a, b = alpha_bb[:, None], beta_bb[:, None]
    lb0 = betaln(a, b)
    p0 = np.exp(betaln(a, b + n) - lb0)
    p1 = np.where(n >= 1, n * np.exp(betaln(a + 1, np.maximum(b + n - 1, 1e-9)) - lb0), 0.0)
    cover = pmf.sum(axis=1)
    e0 = (pmf * p0).sum(axis=1) / cover
    e1 = (pmf * p1).sum(axis=1) / cover
    out: FloatArray = 1.0 - e0 if k == 1 else 1.0 - e0 - e1
    return np.clip(out, 1e-6, 1 - 1e-6)


def p_goal_ge1(pmf: FloatArray, g: FloatArray) -> FloatArray:
    n = np.arange(K_MAX + 1)[None, :]
    cover = pmf.sum(axis=1)
    p0 = (pmf * (1.0 - g[:, None]) ** n).sum(axis=1) / cover
    out: FloatArray = 1.0 - p0
    return np.clip(out, 1e-6, 1 - 1e-6)


def attach_group_priors(df: pd.DataFrame, gr: pd.DataFrame) -> pd.DataFrame:
    """Merge position-group league totals (as of each match) and derive group-level priors."""
    g = gr.copy()
    keep = [
        "sb_match_id",
        "position_group",
        "g_shots",
        "g_sot",
        "g_xg",
        "g_goals",
        "g_expo",
        "g_minutes",
    ]
    allg = (
        g.groupby("sb_match_id")[["g_shots", "g_sot", "g_xg", "g_goals", "g_expo", "g_minutes"]]
        .sum()
        .reset_index()
    )
    d = df.merge(g[keep], on=["sb_match_id", "position_group"], how="left", suffixes=("", "_grp"))
    d = d.merge(allg, on="sb_match_id", how="left", suffixes=("", "_all"))
    thin = d["g_shots"].fillna(0) < MIN_GROUP_SHOTS  # too few group shots: use the pooled priors
    for c in ("g_shots", "g_sot", "g_xg", "g_goals", "g_expo", "g_minutes"):
        d[f"{c}_p"] = d[c].where(~thin, d[f"{c}_all"]).fillna(d[f"{c}_all"])
    d["omega_g"] = d["g_shots_p"] / d["g_expo_p"].replace(0, np.nan)
    d["rho_g"] = d["g_sot_p"] / d["g_shots_p"].replace(0, np.nan)
    d["xgps_g"] = d["g_xg_p"] / d["g_shots_p"].replace(0, np.nan)
    return d


def player_quantities(d: pd.DataFrame, p: ShotParams) -> pd.DataFrame:
    """Shrunk player quantities from rolling history (all prior matches, window 1000)."""
    out = pd.DataFrame(index=d.index)
    out["omega"] = (d["shots_1000"] + p.k * d["omega_g"]) / (d["expo_1000"] + p.k)
    a, b = p.a_sot, p.a_sot
    out["bb_alpha"] = a * d["rho_g"] + d["sot_1000"]
    out["bb_beta"] = b * (1.0 - d["rho_g"]) + (d["shots_1000"] - d["sot_1000"])
    xgps = (d["xg_1000"] + p.b_xg * d["xgps_g"]) / (d["shots_1000"] + p.b_xg)
    phi = (d["goals_1000"] + p.c_fin) / (d["xg_1000"] + p.c_fin)
    out["g_shot"] = np.clip(xgps * phi, 0.0, 0.9)
    out["xgps"] = xgps
    return out


def price_markets(
    q: pd.DataFrame,
    mu_team: FloatArray,
    minutes_grid: FloatArray,
    weights: FloatArray | None,
    minutes_mean: FloatArray,
    l_bar: float,
    alpha: float,
    full: bool,
) -> dict[str, FloatArray]:
    """Prices for shots 1+/2+/3+, SoT 1+/2+ and anytime scorer. `full` uses the minutes
    distribution, otherwise the plug-in mean minutes."""
    rate = q["omega"].to_numpy(float) * mu_team / (11.0 * l_bar)
    if full and weights is not None:
        pmf = mixture_pmf(rate, minutes_grid, weights, alpha)
    else:
        pmf = plugin_pmf(rate, minutes_mean, alpha)
    ab, bb = q["bb_alpha"].to_numpy(float), q["bb_beta"].to_numpy(float)
    return {
        "shots_1plus": p_ge(pmf, 1),
        "shots_2plus": p_ge(pmf, 2),
        "shots_3plus": p_ge(pmf, 3),
        "sot_1plus": p_sot_ge(pmf, ab, bb, 1),
        "sot_2plus": p_sot_ge(pmf, ab, bb, 2),
        "anytime_scorer": p_goal_ge1(pmf, q["g_shot"].to_numpy(float)),
        "_exp_shots": (pmf * np.arange(K_MAX + 1)).sum(axis=1),
    }
