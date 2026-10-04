import numpy as np
import pandas as pd

from edgeforge.models.dixon_coles import dc_grid, fit_dixon_coles
from edgeforge.models.poisson_static import fit_static_poisson


def _simulate(rho: float, n_rounds: int = 60, seed: int = 0):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    teams = ["A", "B", "C", "D", "E", "F"]
    att = np.array([0.4, 0.2, 0.0, -0.1, -0.2, -0.3])
    dfn = np.array([-0.2, -0.1, 0.0, 0.0, 0.1, 0.2])
    h, a, hg, ag = [], [], [], []
    for _ in range(n_rounds):
        for i in range(6):
            for j in range(6):
                if i == j:
                    continue
                lam = np.exp(0.15 + 0.25 + att[i] + dfn[j])
                mu = np.exp(0.15 + att[j] + dfn[i])
                g = dc_grid(np.array([lam]), np.array([mu]), rho)[0]
                flat = rng.choice(g.size, p=g.ravel())
                x, y = divmod(flat, g.shape[1])
                h.append(i)
                a.append(j)
                hg.append(x)
                ag.append(y)
    return teams, np.array(h), np.array(a), np.array(hg), np.array(ag)


def test_no_decay_no_rho_matches_poisson_glm() -> None:
    teams, h, a, hg, ag = _simulate(0.0)
    m = fit_dixon_coles(h, a, hg, ag, np.zeros(len(h)), teams, xi=0.0, ridge=1e-3, fit_rho=False)
    df = pd.DataFrame(
        {"home": [teams[i] for i in h], "away": [teams[i] for i in a], "fthg": hg, "ftag": ag}
    )
    g = fit_static_poisson(df, alpha=1e-7)
    ts = pd.Series(teams)
    lam_dc, mu_dc = m.lambdas(ts, ts.iloc[::-1].reset_index(drop=True))
    lam_g, mu_g = g.predict(ts, ts.iloc[::-1].reset_index(drop=True))
    assert np.allclose(lam_dc, lam_g, rtol=0.02)
    assert np.allclose(mu_dc, mu_g, rtol=0.02)
    assert m.rho == 0.0


def test_recovers_negative_rho() -> None:
    teams, h, a, hg, ag = _simulate(-0.15, n_rounds=250, seed=3)
    m = fit_dixon_coles(h, a, hg, ag, np.zeros(len(h)), teams, xi=0.0)
    assert -0.25 < m.rho < -0.05
    assert m.home_adv > 0.15
    assert m.meta["converged"] == 1.0


def test_decay_downweights_old_matches() -> None:
    teams, h, a, hg, ag = _simulate(0.0, n_rounds=40)
    half = len(h) // 2
    # old half: team A strong; recent half: team A weak (swap goals)
    hg2, ag2 = hg.copy(), ag.copy()
    rows = np.flatnonzero(h[half:] == 0) + half
    hg2[rows] = 0
    rows_a = np.flatnonzero(a[half:] == 0) + half
    ag2[rows_a] = 3
    age = np.where(np.arange(len(h)) < half, 400.0, 1.0)
    old_heavy = fit_dixon_coles(h, a, hg2, ag2, age, teams, xi=0.0)
    decayed = fit_dixon_coles(h, a, hg2, ag2, age, teams, xi=np.log(2) / 60)
    assert decayed.attack[0] < old_heavy.attack[0]  # recent weakness dominates once decayed


def test_prior_pulls_unseen_team_toward_prior_mean() -> None:
    teams, h, a, hg, ag = _simulate(0.0, n_rounds=30)
    teams = teams + ["P"]  # a promoted team with no data
    prior = {"P": (-0.5, 0.4)}
    m = fit_dixon_coles(
        h, a, hg, ag, np.zeros(len(h)), teams, xi=0.0, prior_mean=prior, prior_strength=5.0
    )
    i = m.teams["P"]
    assert abs(m.attack[i] - (-0.5)) < 0.05 and abs(m.defence[i] - 0.4) < 0.05
    plain = fit_dixon_coles(h, a, hg, ag, np.zeros(len(h)), teams, xi=0.0)
    assert abs(plain.attack[plain.teams["P"]]) < 0.05


def test_grid_is_normalised_and_rho_shifts_low_scores() -> None:
    g0 = dc_grid(np.array([1.4]), np.array([1.1]), 0.0)[0]
    gn = dc_grid(np.array([1.4]), np.array([1.1]), -0.15)[0]
    assert np.isclose(g0.sum(), 1.0) and np.isclose(gn.sum(), 1.0)
    assert gn[0, 0] > g0[0, 0] and gn[1, 1] > g0[1, 1]  # negative rho adds 0-0 and 1-1
    assert gn[1, 0] < g0[1, 0]
