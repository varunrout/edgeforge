"""dc_opening_v2 (D-065): Dixon-Coles with a shots-on-target likelihood that shares the attack and
defence parameters of the goals likelihood.

    goals:            as in `dixon_coles.py`
    shots on target:  HST ~ Poisson(exp(c_s + h_s + a[home] + d[away]))
                      AST ~ Poisson(exp(c_s + a[away] + d[home]))

The shots terms enter the weighted log likelihood multiplied by a fixed weight `omega`; omega = 0
reproduces the goals-only model. A match with a missing HST or AST contributes its goals only.
"""

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize

from edgeforge.models.dixon_coles import RHO_BOUND, RIDGE, DixonColes, _tau_terms

FloatArray = NDArray[np.float64]


def fit_dixon_coles_shots(
    home: NDArray[np.int64],
    away: NDArray[np.int64],
    hg: NDArray[np.int64],
    ag: NDArray[np.int64],
    hst: FloatArray,
    ast: FloatArray,
    age_days: FloatArray,
    team_names: list[str],
    xi: float,
    omega: float,
    ridge: float = RIDGE,
    fit_rho: bool = True,
) -> DixonColes:
    """Fit by L-BFGS-B; `hst`/`ast` may contain NaN (those matches add no shots information)."""
    n_t = len(team_names)
    w = np.exp(-xi * np.asarray(age_days, dtype=float))
    x = hg.astype(float)
    y = ag.astype(float)
    has_shots = np.isfinite(hst) & np.isfinite(ast)
    sh = np.where(has_shots, hst, 0.0).astype(float)
    sa = np.where(has_shots, ast, 0.0).astype(float)
    ws = omega * w * has_shots

    def unpack(th: FloatArray) -> tuple[float, float, float, float, float, FloatArray, FloatArray]:
        return th[0], th[1], th[2], th[3], th[4], th[5 : 5 + n_t], th[5 + n_t :]

    def fun(th: FloatArray) -> tuple[float, FloatArray]:
        c, h, rho, cs, hs, a, d = unpack(th)
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
        g_cs = 0.0
        g_hs = 0.0
        if omega > 0.0:
            eta_h = cs + hs + a[home] + d[away]
            eta_a = cs + a[away] + d[home]
            ls, ms = np.exp(eta_h), np.exp(eta_a)
            ll += np.sum(ws * (sh * eta_h - ls + sa * eta_a - ms))
            s_h = ws * (sh - ls)
            s_a = ws * (sa - ms)
            g_cs = s_h.sum() + s_a.sum()
            g_hs = s_h.sum()
            g_a = g_a + np.bincount(home, s_h, n_t) + np.bincount(away, s_a, n_t)
            g_d = g_d + np.bincount(away, s_h, n_t) + np.bincount(home, s_a, n_t)
        pen = 0.5 * np.sum(ridge * (a**2 + d**2))
        g_a = g_a - ridge * a
        g_d = g_d - ridge * d
        grad = np.concatenate([[g_c, g_h, g_rho, g_cs, g_hs], g_a, g_d])
        return -(ll - pen), -grad

    th0 = np.zeros(5 + 2 * n_t)
    th0[0] = np.log(
        max(float(np.average(np.concatenate([x, y]), weights=np.concatenate([w, w]))), 0.1)
    )
    if has_shots.any():
        th0[3] = np.log(max(float(np.mean(np.concatenate([sh[has_shots], sa[has_shots]]))), 0.5))
    bounds: list[tuple[float | None, float | None]] = [(None, None), (None, None)]
    bounds.append((-RHO_BOUND, RHO_BOUND) if fit_rho else (0.0, 0.0))
    bounds += [(None, None), (None, None)]
    bounds += [(None, None)] * (2 * n_t)
    res = minimize(fun, th0, jac=True, method="L-BFGS-B", bounds=bounds, options={"maxiter": 300})
    c, h, rho, cs, hs, a, d = unpack(res.x)
    return DixonColes(
        teams={t: i for i, t in enumerate(team_names)},
        intercept=float(c),
        home_adv=float(h),
        rho=float(rho),
        attack=a,
        defence=d,
        xi=xi,
        n_matches=len(x),
        meta={
            "converged": float(res.success),
            "n_iter": float(res.nit),
            "omega": float(omega),
            "shots_intercept": float(cs),
            "shots_home_adv": float(hs),
        },
    )
