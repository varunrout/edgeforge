"""Dixon-Coles team model with exponential time decay (weighted maximum likelihood).

    lambda_home = exp(c + h + a[home] + d[away]),   lambda_away = exp(c + a[away] + d[home])
    P(x, y) = tau(x, y; lambda, mu, rho) * Poisson(x; lambda) * Poisson(y; mu)

tau adjusts the four low-score cells (0-0, 1-0, 0-1, 1-1). Each match is weighted by
exp(-xi * age_in_days), xi = ln 2 / half_life. A ridge penalty keeps attack/defence identifiable;
for promoted teams it can pull toward a prior mean (the cold-start fix) with strength kappa.
The likelihood gradient is analytic and fully vectorised.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.optimize import minimize

FloatArray = NDArray[np.float64]
RIDGE = 0.5  # default weak ridge, in log-likelihood units (about half a pseudo-match)
RHO_BOUND = 0.3


@dataclass
class DixonColes:
    teams: dict[str, int]
    intercept: float
    home_adv: float
    rho: float
    attack: FloatArray
    defence: FloatArray
    xi: float
    n_matches: int
    meta: dict[str, float] = field(default_factory=dict)

    def lambdas(self, home: pd.Series, away: pd.Series) -> tuple[FloatArray, FloatArray]:
        def eff(names: pd.Series, arr: FloatArray) -> FloatArray:
            return np.array([arr[self.teams[n]] if n in self.teams else 0.0 for n in names])

        lam_h = np.exp(
            self.intercept + self.home_adv + eff(home, self.attack) + eff(away, self.defence)
        )
        lam_a = np.exp(self.intercept + eff(away, self.attack) + eff(home, self.defence))
        return lam_h, lam_a


def _tau_terms(
    x: FloatArray, y: FloatArray, lam: FloatArray, mu: FloatArray, rho: float
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    """log tau and its partial derivatives wrt lambda, mu, rho (zeros outside the 4 cells)."""
    c00 = (x == 0) & (y == 0)
    c01 = (x == 0) & (y == 1)
    c10 = (x == 1) & (y == 0)
    c11 = (x == 1) & (y == 1)
    tau = np.ones_like(lam)
    tau[c00] = 1.0 - lam[c00] * mu[c00] * rho
    tau[c01] = 1.0 + lam[c01] * rho
    tau[c10] = 1.0 + mu[c10] * rho
    tau[c11] = 1.0 - rho
    tau = np.maximum(tau, 1e-10)
    d_lam = np.zeros_like(lam)
    d_mu = np.zeros_like(lam)
    d_rho = np.zeros_like(lam)
    d_lam[c00] = -mu[c00] * rho / tau[c00]
    d_mu[c00] = -lam[c00] * rho / tau[c00]
    d_rho[c00] = -lam[c00] * mu[c00] / tau[c00]
    d_lam[c01] = rho / tau[c01]
    d_rho[c01] = lam[c01] / tau[c01]
    d_mu[c10] = rho / tau[c10]
    d_rho[c10] = mu[c10] / tau[c10]
    d_rho[c11] = -1.0 / tau[c11]
    return np.log(tau), d_lam, d_mu, d_rho


def fit_dixon_coles(
    home: NDArray[np.int64],
    away: NDArray[np.int64],
    hg: NDArray[np.int64],
    ag: NDArray[np.int64],
    age_days: FloatArray,
    team_names: list[str],
    xi: float,
    prior_mean: dict[str, tuple[float, float]] | None = None,
    prior_strength: float = 0.0,
    ridge: float = RIDGE,
    fit_rho: bool = True,
) -> DixonColes:
    """Fit by L-BFGS-B. `home`/`away` index into `team_names`. `prior_mean` maps team name to
    (attack, defence) prior means; those teams are penalised toward them with `prior_strength`
    instead of `ridge` (promoted-team cold-start prior)."""
    n_t = len(team_names)
    w = np.exp(-xi * np.asarray(age_days, dtype=float))
    x = hg.astype(float)
    y = ag.astype(float)
    pen_w = np.full(n_t, ridge)
    a0 = np.zeros(n_t)
    d0 = np.zeros(n_t)
    if prior_mean and prior_strength > 0:
        for i, t in enumerate(team_names):
            if t in prior_mean:
                pen_w[i] = prior_strength
                a0[i], d0[i] = prior_mean[t]

    def unpack(th: FloatArray) -> tuple[float, float, float, FloatArray, FloatArray]:
        return th[0], th[1], th[2], th[3 : 3 + n_t], th[3 + n_t :]

    def fun(th: FloatArray) -> tuple[float, FloatArray]:
        c, h, rho, a, d = unpack(th)
        lam = np.exp(c + h + a[home] + d[away])
        mu = np.exp(c + a[away] + d[home])
        ll_t, dl, dm, dr = _tau_terms(x, y, lam, mu, rho)
        ll = np.sum(w * (ll_t + x * np.log(lam) - lam + y * np.log(mu) - mu))
        r_lam = w * ((x - lam) + lam * dl)
        r_mu = w * ((y - mu) + mu * dm)
        g_c = r_lam.sum() + r_mu.sum()
        g_h = r_lam.sum()
        g_rho = np.sum(w * dr) if fit_rho else 0.0
        g_a = np.bincount(home, r_lam, n_t) + np.bincount(away, r_mu, n_t)
        g_d = np.bincount(away, r_lam, n_t) + np.bincount(home, r_mu, n_t)
        pen = 0.5 * np.sum(pen_w * ((a - a0) ** 2 + (d - d0) ** 2))
        g_a = g_a - pen_w * (a - a0)
        g_d = g_d - pen_w * (d - d0)
        grad = np.concatenate([[g_c, g_h, g_rho], g_a, g_d])
        return -(ll - pen), -grad

    th0 = np.zeros(3 + 2 * n_t)
    th0[0] = np.log(
        max(float(np.average(np.concatenate([x, y]), weights=np.concatenate([w, w]))), 0.1)
    )
    bounds: list[tuple[float | None, float | None]] = [(None, None), (None, None)]
    bounds.append((-RHO_BOUND, RHO_BOUND) if fit_rho else (0.0, 0.0))
    bounds += [(None, None)] * (2 * n_t)
    res = minimize(fun, th0, jac=True, method="L-BFGS-B", bounds=bounds, options={"maxiter": 300})
    c, h, rho, a, d = unpack(res.x)
    return DixonColes(
        teams={t: i for i, t in enumerate(team_names)},
        intercept=float(c),
        home_adv=float(h),
        rho=float(rho),
        attack=a,
        defence=d,
        xi=xi,
        n_matches=len(x),
        meta={"converged": float(res.success), "n_iter": float(res.nit)},
    )


def dc_grid(
    lam_h: FloatArray, lam_a: FloatArray, rho: FloatArray | float, max_goals: int = 12
) -> FloatArray:
    """Score grid with the Dixon-Coles low-score adjustment, renormalised. Shape (n, G+1, G+1)."""
    from scipy.stats import poisson

    lam_h = np.asarray(lam_h, dtype=float)
    lam_a = np.asarray(lam_a, dtype=float)
    rho_v = np.broadcast_to(np.asarray(rho, dtype=float), lam_h.shape)
    k = np.arange(max_goals + 1)
    grid = poisson.pmf(k[None, :, None], lam_h[:, None, None]) * poisson.pmf(
        k[None, None, :], lam_a[:, None, None]
    )
    grid[:, 0, 0] *= np.maximum(1.0 - lam_h * lam_a * rho_v, 1e-10)
    grid[:, 0, 1] *= np.maximum(1.0 + lam_h * rho_v, 1e-10)
    grid[:, 1, 0] *= np.maximum(1.0 + lam_a * rho_v, 1e-10)
    grid[:, 1, 1] *= np.maximum(1.0 - rho_v, 1e-10)
    out: FloatArray = grid / grid.sum(axis=(1, 2), keepdims=True)
    return out
