import numpy as np
import pytest

from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.registry import append_records
from edgeforge.models.poisson_static import fit_static_poisson
from edgeforge.pricing.devig import devig, proportional
from edgeforge.pricing.markets import market_probs, score_grid


@pytest.mark.parametrize("method", ["proportional", "power", "shin"])
def test_devig_sums_to_one_and_removes_margin(method: str) -> None:
    odds = np.array([[1.9, 3.6, 4.2], [2.5, 3.1, 3.0], [1.25, 6.0, 12.0]])
    p, fb = devig(odds, method)
    assert fb == 0
    assert np.allclose(p.sum(axis=1), 1.0)
    assert (p < 1 / odds).all()  # each fair prob below the raw implied prob when there is margin
    assert (p > 0).all()


def test_devig_equal_odds_symmetric_and_fair_book_unchanged() -> None:
    p, _ = devig(np.array([[3.0, 3.0, 3.0]]), "shin")
    assert np.allclose(p, 1 / 3)
    fair = np.array([[2.0, 4.0, 4.0]])  # implied sum exactly 1
    for m in ("proportional", "power", "shin"):
        assert np.allclose(devig(fair, m)[0], [[0.5, 0.25, 0.25]], atol=1e-6)


def test_shin_and_power_shift_mass_to_favourite_less_than_proportional_for_longshots() -> None:
    odds = np.array([[1.3, 5.5, 11.0]])
    prop = proportional(odds)[0]
    shin = devig(odds, "shin")[0][0]
    power = devig(odds, "power")[0][0]
    assert shin[2] < prop[2] and power[2] < prop[2]  # longshot gets less than proportional


def test_two_way_devig() -> None:
    p, _ = devig(np.array([[1.9, 1.95]]), "shin")
    assert np.isclose(p.sum(), 1.0)


def test_score_grid_markets_are_coherent() -> None:
    g = score_grid(np.array([1.5]), np.array([1.1]))
    m = market_probs(g)
    assert np.isclose(m["1x2"].sum(), 1.0)
    assert m["over_0.5"][0] > m["over_1.5"][0] > m["over_2.5"][0] > m["over_3.5"][0]
    assert 0 < m["btts"][0] < 1


def test_multiclass_metrics_known_values() -> None:
    p = np.array([[0.7, 0.2, 0.1], [0.1, 0.1, 0.8]])
    y = np.array([0, 2])
    assert np.allclose(M.log_loss_multiclass(p, y), [-np.log(0.7), -np.log(0.8)])
    assert np.allclose(M.brier_multiclass(p, y), [0.09 + 0.04 + 0.01, 0.01 + 0.01 + 0.04])
    # RPS for a certain correct forecast is 0; for a certain far-wrong one it is 1
    assert np.allclose(M.rps(np.array([[1.0, 0, 0]]), np.array([0])), 0.0)
    assert np.allclose(M.rps(np.array([[1.0, 0, 0]]), np.array([2])), 1.0)


def test_ece_zero_when_calibrated_and_positive_when_not() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0.05, 0.95, 20000)
    y = (rng.uniform(size=p.size) < p).astype(float)
    assert M.ece(p, y) < 0.02
    assert M.ece(np.clip(p + 0.2, 0, 1), y) > 0.1


def test_pit_uniform_and_coverage_for_true_poisson() -> None:
    rng = np.random.default_rng(1)
    lam = rng.uniform(0.3, 3.0, 20000)
    y = rng.poisson(lam).astype(np.int64)
    h = M.pit_histogram(M.pit_values(lam, y, 5))
    assert max(abs(x - 0.1) for x in h) < 0.015
    assert M.central_interval_coverage(lam, y, 0.8) >= 0.8


def test_paired_bootstrap_detects_a_better_model_and_null_includes_zero() -> None:
    rng = np.random.default_rng(2)
    base = rng.uniform(0.5, 1.5, 2000)
    groups = np.repeat(np.arange(500), 4)
    better = M.paired_bootstrap(base - 0.1, base, groups, 500, 7)
    assert better["ci_high"] < 0 and better["prob_a_better"] > 0.99
    null = M.paired_bootstrap(base, base + rng.normal(0, 0.05, 2000), groups, 500, 7)
    assert null["ci_low"] < 0 < null["ci_high"]


def test_static_poisson_recovers_strength_ordering() -> None:
    import pandas as pd

    rng = np.random.default_rng(4)
    teams = ["A", "B", "C", "D"]
    att = {"A": 0.5, "B": 0.0, "C": -0.2, "D": -0.4}
    rows = []
    for _ in range(40):
        for h in teams:
            for a in teams:
                if h != a:
                    rows.append(
                        {
                            "home": h,
                            "away": a,
                            "fthg": rng.poisson(np.exp(0.2 + 0.2 + att[h])),
                            "ftag": rng.poisson(np.exp(0.2 + att[a])),
                        }
                    )
    m = fit_static_poisson(pd.DataFrame(rows))
    a_idx = [m.teams[t] for t in teams]
    assert list(np.argsort(-m.attack[a_idx])) == [0, 1, 2, 3]
    assert m.home_adv > 0.1
    lh, la = m.predict(pd.Series(["A", "Z"]), pd.Series(["D", "A"]))
    assert lh[0] > la[0] and np.isfinite(lh[1])  # unseen team -> league average


def test_registry_appends_once(tmp_path) -> None:  # type: ignore[no-untyped-def]
    rec = {
        "experiment_id": "x",
        "metrics": {"a": 1},
        "hyperparameters": {},
        "validation_window": "w",
        "status": "baseline",
    }
    path = tmp_path / "r.jsonl"
    assert append_records([rec], path) == 1
    assert append_records([rec], path) == 0
    assert append_records([{**rec, "metrics": {"a": 2}}], path) == 1
