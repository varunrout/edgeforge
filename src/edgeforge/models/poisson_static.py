"""Static Poisson team model (baseline): log lambda = mu + home + attack(team) + defence(opp).

No time decay and no tuned hyper-parameters; a tiny fixed ridge makes the one-hot design
identifiable. Teams unseen in the training window get zero effects (the league average).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy import sparse
from sklearn.linear_model import PoissonRegressor

RIDGE_ALPHA = 1e-4
FloatArray = NDArray[np.float64]


@dataclass
class StaticPoisson:
    teams: dict[str, int]
    intercept: float
    home_adv: float
    attack: FloatArray
    defence: FloatArray

    def predict(self, home: pd.Series, away: pd.Series) -> tuple[FloatArray, FloatArray]:
        def eff(names: pd.Series, arr: FloatArray) -> FloatArray:
            return np.array([arr[self.teams[n]] if n in self.teams else 0.0 for n in names])

        att_h, def_h = eff(home, self.attack), eff(home, self.defence)
        att_a, def_a = eff(away, self.attack), eff(away, self.defence)
        lam_h = np.exp(self.intercept + self.home_adv + att_h + def_a)
        lam_a = np.exp(self.intercept + att_a + def_h)
        return lam_h, lam_a


def fit_static_poisson(matches: pd.DataFrame, alpha: float = RIDGE_ALPHA) -> StaticPoisson:
    """Fit on rows with home, away, fthg, ftag."""
    teams = sorted(set(matches["home"]) | set(matches["away"]))
    idx = {t: i for i, t in enumerate(teams)}
    n_t, n = len(teams), len(matches)
    h = matches["home"].map(idx).to_numpy()
    a = matches["away"].map(idx).to_numpy()
    rows = np.arange(2 * n)
    home_flag = np.concatenate([np.ones(n), np.zeros(n)])
    att_idx = np.concatenate([h, a])
    def_idx = np.concatenate([a, h])
    x_home = sparse.csr_matrix(home_flag.reshape(-1, 1))
    x_att = sparse.csr_matrix((np.ones(2 * n), (rows, att_idx)), shape=(2 * n, n_t))
    x_def = sparse.csr_matrix((np.ones(2 * n), (rows, def_idx)), shape=(2 * n, n_t))
    x = sparse.hstack([x_home, x_att, x_def]).tocsr()
    y = np.concatenate([matches["fthg"].to_numpy(float), matches["ftag"].to_numpy(float)])
    reg = PoissonRegressor(alpha=alpha, max_iter=500)
    reg.fit(x, y)
    coef = reg.coef_
    return StaticPoisson(
        teams=idx,
        intercept=float(reg.intercept_),
        home_adv=float(coef[0]),
        attack=coef[1 : 1 + n_t],
        defence=coef[1 + n_t :],
    )
