import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from edgeforge.evaluation.pillar_a import Frame, analyse_frame, build_claims, fit_pool
from edgeforge.evaluation.stats import Boot, batched_logistic, benjamini_hochberg, summarize


def test_benjamini_hochberg_known_example() -> None:
    p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
    q, keep = benjamini_hochberg(p, 0.05)
    assert keep == [True, True] + [False] * 8
    assert all(0 <= x <= 1 for x in q) and q == sorted(q)


def test_boot_mean_and_summarize() -> None:
    rng = np.random.default_rng(0)
    v = rng.normal(0.3, 1.0, 400)
    boot = Boot.make(len(v), 800, 1)
    draws = boot.mean(v)
    s = summarize(float(v.mean()), draws, 0.0)
    assert s["ci_low"] > 0 and s["p"] < 0.01
    s0 = summarize(float(v.mean()), draws, 0.3)
    assert s0["ci_low"] < 0.3 < s0["ci_high"] and s0["p"] > 0.2


def test_batched_logistic_matches_sklearn_and_weights() -> None:
    rng = np.random.default_rng(2)
    x = rng.normal(0, 1.5, 3000)
    y = (rng.uniform(size=x.size) < 1 / (1 + np.exp(-(0.2 + 0.7 * x)))).astype(float)
    b = batched_logistic(x, y, np.ones((1, x.size)))[0]
    ref = LogisticRegression(C=1e8, max_iter=2000).fit(x.reshape(-1, 1), y)
    assert np.allclose(b, [ref.intercept_[0], ref.coef_[0, 0]], atol=1e-3)
    w = np.vstack([np.ones(x.size), np.where(np.arange(x.size) % 2 == 0, 2.0, 0.0)])
    bb = batched_logistic(x, y, w)
    assert bb.shape == (2, 2) and np.allclose(bb[0], b, atol=1e-6)


def _frame(distort: float, n: int = 2500, seed: int = 5) -> Frame:
    """Synthetic market: truth is a favourite-longshot distortion of the quoted probability."""
    rng = np.random.default_rng(seed)
    raw = rng.dirichlet([4, 2.5, 3.5], n)
    # truth = quoted probabilities pushed away from / toward the favourite by `distort`
    z = np.log(raw) * (1.0 + distort)
    truth = np.exp(z) / np.exp(z).sum(axis=1, keepdims=True)
    y = np.array([rng.choice(3, p=t) for t in truth])
    ev = pd.DataFrame(
        {
            "league": rng.choice(["E0", "D1", "SP1"], n),
            "early_phase": rng.uniform(size=n) < 0.2,
            "home_promoted": rng.uniform(size=n) < 0.1,
            "away_promoted": rng.uniform(size=n) < 0.1,
            "home_n": rng.integers(1, 39, n),
            "away_n": rng.integers(1, 39, n),
        }
    )
    ev["promoted_first10"] = (ev["home_promoted"] & (ev["home_n"] <= 10)) | (
        ev["away_promoted"] & (ev["away_n"] <= 10)
    )
    ev["promoted_involved"] = ev["home_promoted"] | ev["away_promoted"]
    ev["promoted_later"] = ev["promoted_involved"] & ~ev["promoted_first10"]
    close = {m: raw for m in ("proportional", "power", "shin")}
    early = {m: np.clip(raw + rng.normal(0, 0.01, raw.shape), 0.01, 0.98) for m in close}
    early = {m: v / v.sum(axis=1, keepdims=True) for m, v in early.items()}
    model = np.clip(raw + rng.normal(0, 0.03, raw.shape), 0.01, 0.98)
    model = model / model.sum(axis=1, keepdims=True)
    return Frame("synthetic", "test", "PS", ev, y, close, early, model, model)


def test_calibrated_market_produces_no_bh_survivors_and_slope_near_one() -> None:
    f = _frame(0.0)
    res = analyse_frame(f, None, Boot.make(f.n, 400, 3), 3)
    slope = next(c for c in res["claims"] if c["id"] == "eff.flb.close.slope")
    assert 0.85 < slope["estimate"] < 1.15 and slope["ci_low"] < 1.0 < slope["ci_high"]
    flb = [c for c in res["claims"] if c["id"].startswith("eff.")]
    assert sum(c["survives_bh_10pct"] for c in flb) <= 1  # null control: at most the odd false hit
    assert res["n_claims_in_bh_family"] == len(res["claims"])


def test_planted_flb_distortion_is_detected_and_survives_bh() -> None:
    f = _frame(0.35)  # outcomes more extreme than quoted: quoted probs too flat => slope > 1
    res = analyse_frame(f, None, Boot.make(f.n, 400, 3), 3)
    slope = next(c for c in res["claims"] if c["id"] == "eff.flb.close.slope")
    assert slope["ci_low"] > 1.0 and slope["survives_bh_10pct"]
    assert (
        "[early-market snapshot"
        in next(c for c in res["claims"] if c["id"] == "eff.flb.early.slope")["description"]
    )


def test_claim_set_covers_every_d037_segment() -> None:
    f = _frame(0.0, n=600)
    ids = [c["id"] for c in build_claims(f, Boot.make(f.n, 100, 1), "proportional", None)]
    for prefix in (
        "eff.league.",
        "eff.phase.",
        "eff.flb.close.band0",
        "eff.flb.close.band3",
        "eff.flb.early.band0",
        "eff.flb.close.slope",
        "eff.flb.early.slope",
        "eff.draw.close.bias",
        "eff.draw.close.slope",
        "eff.promoted.first10",
        "eff.promoted.later",
        "eff.snapshot.movement_slope",
        "eff.snapshot.logloss_close_minus_early",
        "mvm.league.",
        "mvm.phase.early",
        "mvm.flb.band0",
        "mvm.draw",
        "mvm.promoted.first10",
        "mvm.linemove.slope",
        "coldstart.fix_vs_none.first10",
    ):
        assert any(i.startswith(prefix) for i in ids), prefix


def test_pool_weight_on_informative_model() -> None:
    rng = np.random.default_rng(9)
    n = 6000
    truth = rng.dirichlet([3, 2, 3], n)
    y = np.array([rng.choice(3, p=t) for t in truth])
    market = np.clip(truth + rng.normal(0, 0.08, truth.shape), 0.02, 0.95)
    market /= market.sum(axis=1, keepdims=True)
    model = np.clip(truth + rng.normal(0, 0.04, truth.shape), 0.02, 0.95)
    model /= model.sum(axis=1, keepdims=True)
    th = fit_pool(market, model, y)
    assert th[3] > th[2]  # the less noisy forecast gets the larger weight


def _frame_underconfident(seed: int, n: int = 3000, informative: bool = False) -> Frame:
    """Market quotes `raw`. Uninformative case: truth is a sharpened `raw` (the market is merely
    under-confident) and the model is noise. Informative case: truth also depends on a signal s that
    the market does not see and the model partly observes."""
    rng = np.random.default_rng(seed)
    raw = rng.dirichlet([4, 2.5, 3.5], n)
    if informative:
        s_ = rng.normal(0, 0.45, (n, 3))
        truth = raw * np.exp(s_)
        model = raw * np.exp(0.8 * s_ + rng.normal(0, 0.1, (n, 3)))
        model = model / model.sum(axis=1, keepdims=True)
    else:
        truth = np.exp(np.log(raw) * 1.4)
        model = rng.dirichlet([4, 2.5, 3.5], n)
    truth = truth / truth.sum(axis=1, keepdims=True)
    y = np.array([rng.choice(3, p=t) for t in truth])
    f = _frame(0.0, n=n, seed=seed)
    f.y = y
    f.close = {m: raw for m in f.close}
    f.early = {m: raw for m in f.early}
    f.model = model
    f.model_fix = model
    return f


def test_encompassing_isolates_model_information_from_market_sharpening() -> None:
    tune, test = _frame_underconfident(21), _frame_underconfident(22)
    res = analyse_frame(test, tune, Boot.make(test.n, 400, 4), 4, alt_methods=False)
    enc = next(c for c in res["claims"] if c["id"] == "mvm.encompassing.test_delta")
    recal = next(c for c in res["claims"] if c["id"] == "desc.market_recalibration")
    assert recal["ci_high"] < 0  # sharpening the under-confident market helps
    assert enc["ci_low"] < 0 < enc["ci_high"] or enc["ci_low"] >= -0.002  # noise model adds nothing
    assert not enc["survives_bh_10pct"]
    assert recal["in_bh_family"] is False and recal["status"] == "descriptive, not tested"


def test_encompassing_detects_an_informative_model() -> None:
    tune, test = (
        _frame_underconfident(31, informative=True),
        _frame_underconfident(32, informative=True),
    )
    res = analyse_frame(test, tune, Boot.make(test.n, 400, 4), 4, alt_methods=False)
    enc = next(c for c in res["claims"] if c["id"] == "mvm.encompassing.test_delta")
    assert enc["ci_high"] < 0 and res["encompassing_fit"]["model_weight"] > 0.2


def test_non_estimable_claims_are_excluded_from_the_bh_family() -> None:
    f = _frame(0.0, n=800)
    f.ev["early_phase"] = False  # no early-phase matches: phase claims cannot be estimated
    res = analyse_frame(f, None, Boot.make(f.n, 200, 2), 2)
    phase = next(c for c in res["claims"] if c["id"] == "eff.phase.draw_bias_diff")
    assert phase["in_bh_family"] is False and phase["status"].startswith("not estimable")
    assert res["n_claims_in_bh_family"] == sum(c["in_bh_family"] for c in res["claims"])
