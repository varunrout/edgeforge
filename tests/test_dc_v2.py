"""D-065: dc_opening_v2 model: omega 0 is goals-only, gradient is right, shots matter."""

import numpy as np
import pandas as pd
from scipy.optimize import check_grad

from edgeforge.models.dixon_coles import fit_dixon_coles
from edgeforge.models.dixon_coles_shots import fit_dixon_coles_shots


def _data(n: int = 400) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(5)
    t = 8
    att, dfn = rng.normal(0, 0.3, t), rng.normal(0, 0.3, t)
    home = rng.integers(0, t, n)
    away = (home + rng.integers(1, t, n)) % t
    lam = np.exp(0.2 + 0.25 + att[home] + dfn[away])
    mu = np.exp(0.2 + att[away] + dfn[home])
    hg, ag = rng.poisson(lam), rng.poisson(mu)
    hst = rng.poisson(3.0 * lam).astype(float)
    ast = rng.poisson(3.0 * mu).astype(float)
    age = rng.uniform(0, 900, n)
    return home, away, hg, ag, hst, ast, age


def test_omega_zero_reproduces_the_goals_only_model() -> None:
    home, away, hg, ag, hst, ast, age = _data()
    names = [f"t{i}" for i in range(8)]
    xi = np.log(2) / 365
    a = fit_dixon_coles(home, away, hg, ag, age, names, xi)
    b = fit_dixon_coles_shots(home, away, hg, ag, hst, ast, age, names, xi, 0.0)
    assert np.allclose(a.attack, b.attack, atol=1e-4) and np.allclose(
        a.defence, b.defence, atol=1e-4
    )
    assert abs(a.rho - b.rho) < 1e-4 and abs(a.home_adv - b.home_adv) < 1e-4


def test_shots_change_the_fit_and_missing_shots_fall_back_to_goals() -> None:
    home, away, hg, ag, hst, ast, age = _data()
    names = [f"t{i}" for i in range(8)]
    xi = np.log(2) / 365
    g = fit_dixon_coles_shots(home, away, hg, ag, hst, ast, age, names, xi, 0.0)
    s = fit_dixon_coles_shots(home, away, hg, ag, hst, ast, age, names, xi, 1.0)
    assert not np.allclose(g.attack, s.attack, atol=1e-3)
    nan = np.full(len(hg), np.nan)
    m = fit_dixon_coles_shots(home, away, hg, ag, nan, nan, age, names, xi, 1.0)
    assert np.allclose(g.attack, m.attack, atol=1e-4)


def test_analytic_gradient_matches_finite_differences() -> None:
    import edgeforge.models.dixon_coles_shots as mod

    home, away, hg, ag, hst, ast, age = _data(120)
    captured: dict[str, object] = {}
    orig = mod.minimize

    def spy(fun, th0, **kw):  # type: ignore[no-untyped-def]
        captured["fun"], captured["th0"] = fun, th0
        return orig(fun, th0, **kw)

    mod.minimize = spy  # type: ignore[assignment]
    try:
        fit_dixon_coles_shots(
            home, away, hg, ag, hst, ast, age, [f"t{i}" for i in range(8)], np.log(2) / 365, 0.7
        )
    finally:
        mod.minimize = orig  # type: ignore[assignment]
    fun = captured["fun"]
    th = np.asarray(captured["th0"]) + np.random.default_rng(1).normal(
        0, 0.05, len(captured["th0"])
    )  # type: ignore[arg-type]
    err = check_grad(lambda x: fun(x)[0], lambda x: fun(x)[1], th)  # type: ignore[operator]
    assert err < 1e-3 * max(1.0, float(np.linalg.norm(fun(th)[1])))  # type: ignore[operator]
    _ = pd
