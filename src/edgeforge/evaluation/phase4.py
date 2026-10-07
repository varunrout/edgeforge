"""Phase 4 stages: starter probability, minutes, team shots (player props live in phase4_props).

Every tuning routine goes through `tune_interior` (D-040) on the tuning window only, with
all test-window matches excluded from both the training pool and the targets, and the test stage
only receives `Tuned` values via `require_tuned`.
"""

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.common import binary_metrics
from edgeforge.evaluation.phase4_data import P4Data
from edgeforge.evaluation.splits import SplitGuard
from edgeforge.evaluation.stats import Boot, summarize
from edgeforge.evaluation.tuning import Tuned, require_tuned, tune_interior
from edgeforge.models.participation import (
    N_BINS,
    BenchEntryHazard,
    StartedLastBaseline,
    StarterHazard,
    StartModel,
    bench_event_bins,
    starter_event_bins,
)

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]
N_BOOT = 1000


def walk_forward(
    pool: pd.DataFrame,
    targets: pd.DataFrame,
    fit: Callable[[pd.DataFrame], Any],
    predict: Callable[[Any, pd.DataFrame], FloatArray],
    guard: SplitGuard,
    cutoff_col: str = "cutoff",
    width: int = 1,
    min_rows: int = 300,
    context: str = "",
) -> FloatArray:
    """Fit on pool rows completed by each cutoff and predict that cutoff's targets.

    Returns an array aligned with `targets` (NaN where the pool was too small).
    """
    out = np.full((len(targets), width), np.nan)
    pos = {ix: i for i, ix in enumerate(targets.index)}
    for cutoff, g in targets.groupby(cutoff_col):
        tr = pool[pool["done_ts"] <= cutoff]
        if len(tr) < min_rows:
            continue
        guard.check_fit_before([tr["done_ts"].max()], cutoff, f"{context} cutoff {cutoff}")
        model = fit(tr)
        res = np.asarray(predict(model, g), dtype=float).reshape(len(g), width)
        out[[pos[i] for i in g.index]] = res
    return out if width > 1 else out[:, 0]


def cluster_diff_summary(
    diff: FloatArray, cluster: NDArray[Any], seed: int, null: float = 0.0
) -> dict[str, float]:
    """Mean of per-row differences with a match-cluster bootstrap CI and two-sided p."""
    _, inv = np.unique(cluster, return_inverse=True)
    n = int(inv.max()) + 1
    num = np.bincount(inv, diff, minlength=n)
    den = np.bincount(inv, minlength=n).astype(float)
    boot = Boot.make(n, N_BOOT, seed)
    draws = (boot.counts @ num) / (boot.counts @ den)
    s = summarize(float(num.sum() / den.sum()), draws, null)
    s["n_rows"] = float(len(diff))
    s["n_clusters"] = float(n)
    return s


# ------------------------------------------------------------------------------- starters


def _logloss3(p: FloatArray, y: NDArray[np.int64]) -> float:
    ok = ~np.isnan(p).any(axis=1)
    return float(-np.log(np.clip(p[ok, :][np.arange(ok.sum()), y[ok]], 1e-12, 1)).mean())


def stage_starters(d: P4Data, guard: SplitGuard, seed: int) -> tuple[dict[str, Any], pd.DataFrame]:
    """Start / bench / out probabilities for opening-state candidates."""
    tv = d.cand[d.cand["split"].isin(["burn", "tune"])].reset_index(drop=True)
    tune = tv[tv["split"] == "tune"]
    guard.check_tuning(tv["sb_match_id"].unique().tolist(), "starter model tuning")

    def fit_c(c: float) -> Callable[[pd.DataFrame], Any]:
        return lambda tr: StartModel(c).fit(tr, tr["cls"].to_numpy(np.int64))

    def score(c: float) -> float:
        p = walk_forward(
            tv, tune, fit_c(c), lambda m, g: m.predict_proba(g), guard, width=3, context="start"
        )
        return _logloss3(p, tune["cls"].to_numpy(np.int64))

    tuned = tune_interior(
        "start_model_C",
        score,
        [0.003, 0.01, 0.03, 0.1, 0.3, 1.0],
        widen_low=lambda x: x / 3,
        widen_high=lambda x: x * 3,
    )
    c_best = require_tuned(tuned)
    # tuning-window predictions at the chosen C (walk-forward, no test match involved); the
    # Pillar C policy is fitted on the tuning window and needs opening-state prices there
    p_tune = walk_forward(
        tv,
        tune,
        fit_c(c_best),
        lambda m, g: m.predict_proba(g),
        guard,
        width=3,
        context="start-tune",
    )
    # ---- test window: only now do test rows get scored
    te = d.cand[d.cand["split"] == "test"].reset_index(drop=True)
    pool = d.cand.reset_index(drop=True)
    p3 = walk_forward(
        pool,
        te,
        fit_c(c_best),
        lambda m, g: m.predict_proba(g),
        guard,
        width=3,
        context="start-test",
    )
    base = walk_forward(
        pool,
        te,
        lambda tr: StartedLastBaseline().fit(tr, (tr["cls"].to_numpy() == 2).astype(np.int64)),
        lambda m, g: m.predict(g),
        guard,
        context="start-base",
    )
    y = (te["cls"].to_numpy() == 2).astype(float)
    ok = ~np.isnan(p3).any(axis=1) & ~np.isnan(base)
    ps = p3[ok, 2]
    res: dict[str, Any] = {
        "tuned": tuned.as_record(),
        "n_test_candidate_rows": int(ok.sum()),
        "start_model": binary_metrics(ps, y[ok]),
        "started_last_match_baseline": binary_metrics(base[ok], y[ok]),
        "three_class_log_loss_test": _logloss3(p3[ok], te["cls"].to_numpy(np.int64)[ok]),
        "reliability_start_model": M.reliability_bins(ps, y[ok]),
        "reliability_baseline": M.reliability_bins(base[ok], y[ok]),
    }
    cl = te["sb_match_id"].to_numpy()[ok]
    res["paired_vs_baseline_log_loss"] = cluster_diff_summary(
        M.log_loss_binary(ps, y[ok]) - M.log_loss_binary(base[ok], y[ok]), cl, seed
    )
    res["paired_vs_baseline_brier"] = cluster_diff_summary(
        M.brier_binary(ps, y[ok]) - M.brier_binary(base[ok], y[ok]), cl, seed
    )
    # candidate coverage: actual starters in test matches outside the candidate set
    test_ids = d.ids["test"]
    sq = d.squad[(d.squad["split"] == "test") & (d.squad["started"].astype(bool))]
    key = set(zip(te["sb_match_id"], te["player_id"], strict=True))
    outside = [(m, p) not in key for m, p in zip(sq["sb_match_id"], sq["player_id"], strict=True)]
    res["starters_total_test"] = int(len(sq))
    res["starters_outside_candidate_set"] = int(np.sum(outside))
    res["share_starters_outside_candidate_set"] = float(np.mean(outside))
    res["n_test_matches"] = len(test_ids)
    # expected vs actual starters per team among candidates
    te2 = te.assign(p_start=p3[:, 2], p_bench=p3[:, 1], p_out=p3[:, 0])
    per = te2.groupby(["sb_match_id", "team_id"]).agg(
        exp=("p_start", "sum"), act=("cls", lambda s: float((s == 2).sum()))
    )
    res["per_team_expected_starters_among_candidates"] = {
        "mean_expected": float(per["exp"].mean()),
        "mean_actual": float(per["act"].mean()),
        "mean_abs_gap": float((per["exp"] - per["act"]).abs().mean()),
    }
    pred = te2[["sb_match_id", "player_id", "team_id", "p_out", "p_bench", "p_start"]].copy()
    pred["p_start_baseline"] = base
    tune_pred = tune[["sb_match_id", "player_id", "team_id"]].copy().reset_index(drop=True)
    tune_pred[["p_out", "p_bench", "p_start"]] = p_tune
    tune_pred["p_start_baseline"] = np.nan
    return res, pd.concat([tune_pred, pred], ignore_index=True)


# ------------------------------------------------------------------------------- minutes


def _expand_logscore(pmf: FloatArray, event_bin: NDArray[np.int64]) -> float:
    ok = ~np.isnan(pmf).any(axis=1)
    return float(-np.log(np.clip(pmf[ok][np.arange(ok.sum()), event_bin[ok]], 1e-12, 1)).mean())


def stage_minutes(d: P4Data, guard: SplitGuard) -> tuple[dict[str, Any], dict[str, Any]]:
    """Tune and fit starter-exit and bench-entry hazards; return tuning record and predictions.

    Predictions (pmf over exit/entry bins plus 'none') are returned for tune and test squad rows
    and for opening candidate rows, each from the hazard fitted at the row's biweekly cutoff.
    """
    sq = d.squad.reset_index(drop=True)
    starters = sq[sq["announced_starter"].astype(bool)].copy()
    bench = sq[~sq["announced_starter"].astype(bool)].copy()
    for df, kind in ((starters, "s"), (bench, "b")):
        df["ev"] = (
            starter_event_bins(df["exit_kind"], df["exit_s"])
            if kind == "s"
            else bench_event_bins(df["appeared"].astype(bool), df["sub_on_s"])
        )
    tv_s, tv_b = starters[starters["split"] != "test"], bench[bench["split"] != "test"]
    guard.check_tuning(tv_s["sb_match_id"].unique().tolist(), "starter hazard tuning")
    tune_s, tune_b = tv_s[tv_s["split"] == "tune"], tv_b[tv_b["split"] == "tune"]

    def tune_one(name: str, pool: pd.DataFrame, tgt: pd.DataFrame, cls: type) -> Tuned:
        def sc(c: float) -> float:
            pmf = walk_forward(
                pool,
                tgt,
                lambda tr: cls(c).fit(tr, tr["ev"].to_numpy(np.int64)),
                lambda m, g: m.pmf(g),
                guard,
                cutoff_col="cutoff",
                width=N_BINS + 1,
                min_rows=800,
                context=name,
            )
            return _expand_logscore(pmf, tgt["ev"].to_numpy(np.int64))

        return tune_interior(
            name,
            sc,
            [0.01, 0.03, 0.1, 0.3, 1.0],
            widen_low=lambda x: x / 3,
            widen_high=lambda x: x * 3,
        )

    t_s = tune_one("starter_hazard_C", tv_s, tune_s, StarterHazard)
    t_b = tune_one("bench_entry_hazard_C", tv_b, tune_b, BenchEntryHazard)
    c_s, c_b = require_tuned(t_s), require_tuned(t_b)
    return {"starter": t_s.as_record(), "bench": t_b.as_record()}, {
        "c_start": c_s,
        "c_bench": c_b,
        "starters": starters,
        "bench": bench,
    }


class GroupEmpirical:
    """Baseline: empirical bin distribution by position group (add-half smoothing)."""

    def __init__(self) -> None:
        self.tab: dict[str, FloatArray] = {}
        self.all: FloatArray = np.ones(N_BINS + 1)

    def fit(self, df: pd.DataFrame, ev: NDArray[np.int64]) -> "GroupEmpirical":
        g = df["position_group"].fillna("UNK").to_numpy()
        allc = np.bincount(ev, minlength=N_BINS + 1) + 0.5
        self.all = allc / allc.sum()
        for k in np.unique(g):
            c = np.bincount(ev[g == k], minlength=N_BINS + 1) + 0.5
            self.tab[str(k)] = c / c.sum()
        return self

    def pmf(self, df: pd.DataFrame) -> FloatArray:
        g = df["position_group"].fillna("UNK").to_numpy()
        return np.stack([self.tab.get(str(k), self.all) for k in g])


def minutes_predictions(
    d: P4Data,
    starters: pd.DataFrame,
    bench: pd.DataFrame,
    c_s: float,
    c_b: float,
    guard: SplitGuard,
) -> dict[str, pd.DataFrame]:
    """Walk-forward hazard pmfs for tune and test squad rows and for opening candidates.

    Tune targets are predicted from models fitted without any test-window match; test targets from
    models fitted on everything completed before the cutoff.
    """
    cols = list(range(N_BINS + 1))
    parts: dict[str, list[pd.DataFrame]] = {"sq": [], "cs": [], "cb": []}
    cand = d.cand
    for split, allowed in (("tune", ["burn", "tune"]), ("test", ["burn", "tune", "test"])):
        ts, tb = starters[starters["split"] == split], bench[bench["split"] == split]
        tc = cand[cand["split"] == split]
        ps, pb = starters[starters["split"].isin(allowed)], bench[bench["split"].isin(allowed)]
        if split == "tune":
            guard.check_tuning(ps["sb_match_id"].unique().tolist(), "hazard predictions for tuning")
        for cutoff in sorted(set(ts["cutoff"]) | set(tb["cutoff"]) | set(tc["cutoff"])):
            trs, trb = ps[ps["done_ts"] <= cutoff], pb[pb["done_ts"] <= cutoff]
            if len(trs) < 800 or len(trb) < 800:
                continue
            guard.check_fit_before([trs["done_ts"].max(), trb["done_ts"].max()], cutoff, "hazard")
            ms = StarterHazard(c_s).fit(trs, trs["ev"].to_numpy(np.int64))
            mb = BenchEntryHazard(c_b).fit(trb, trb["ev"].to_numpy(np.int64))
            gs, gb, gc = (
                ts[ts["cutoff"] == cutoff],
                tb[tb["cutoff"] == cutoff],
                tc[tc["cutoff"] == cutoff],
            )
            if len(gs):
                parts["sq"].append(pd.DataFrame(ms.pmf(gs), index=gs.index, columns=cols))
            if len(gb):
                parts["sq"].append(pd.DataFrame(mb.pmf(gb), index=gb.index, columns=cols))
            if len(gc):
                parts["cs"].append(pd.DataFrame(ms.pmf(gc), index=gc.index, columns=cols))
                parts["cb"].append(pd.DataFrame(mb.pmf(gc), index=gc.index, columns=cols))
    return {k: pd.concat(v) for k, v in parts.items()}


def _pit_discrete(pmf: FloatArray, obs: NDArray[np.int64], seed: int) -> FloatArray:
    cdf = np.cumsum(pmf, axis=1)
    hi = cdf[np.arange(len(obs)), obs]
    lo = hi - pmf[np.arange(len(obs)), obs]
    u = np.random.default_rng(seed).uniform(size=len(obs))
    return lo + u * (hi - lo)


def _coverage_bins(pmf: FloatArray, obs: NDArray[np.int64], level: float) -> float:
    cdf = np.cumsum(pmf, axis=1)
    lo = (cdf >= (1 - level) / 2).argmax(axis=1)
    hi = (cdf >= (1 + level) / 2).argmax(axis=1)
    return float(np.mean((obs >= lo) & (obs <= hi)))


def evaluate_minutes(
    d: P4Data,
    starters: pd.DataFrame,
    bench: pd.DataFrame,
    mp: dict[str, pd.DataFrame],
    l_bar: float,
    guard: SplitGuard,
    seed: int,
) -> dict[str, Any]:
    """Test-window evaluation of the minutes distributions against empirical baselines."""
    from edgeforge.models.participation import bench_minutes_grid, starter_minutes_grid

    res: dict[str, Any] = {"l_bar_minutes": l_bar}
    S = starters[starters["split"] == "test"]
    S = S[S.index.isin(mp["sq"].index)]
    pm = mp["sq"].loc[S.index].to_numpy()
    ev = S["ev"].to_numpy(np.int64)
    base = walk_forward(
        starters,
        S,
        lambda tr: GroupEmpirical().fit(tr, tr["ev"].to_numpy(np.int64)),
        lambda m, g: m.pmf(g),
        guard,
        width=N_BINS + 1,
        min_rows=800,
        context="minutes-base-s",
    )
    ok = ~np.isnan(base).any(axis=1)
    ls_m = -np.log(np.clip(pm[np.arange(len(ev)), ev], 1e-12, 1))
    ls_b = -np.log(np.clip(base[np.arange(len(ev)), ev], 1e-12, 1))
    grid_s = starter_minutes_grid(l_bar)
    e_min = pm @ grid_s
    act = S["minutes"].to_numpy(float)
    prior = ((S["mins_started_1000"] + 2 * 75.0) / (S["n_start_1000"] + 2.0)).to_numpy(float)
    pit = _pit_discrete(pm, ev, seed)
    cl = S["sb_match_id"].to_numpy()
    res["starters"] = {
        "n": int(len(S)),
        "log_score_model": float(ls_m[ok].mean()),
        "log_score_group_empirical_baseline": float(ls_b[ok].mean()),
        "paired_log_score_diff_model_minus_baseline": cluster_diff_summary(
            (ls_m - ls_b)[ok], cl[ok], seed
        ),
        "pit_histogram_10": M.pit_histogram(pit),
        "coverage_50": _coverage_bins(pm, ev, 0.5),
        "coverage_80": _coverage_bins(pm, ev, 0.8),
        "coverage_95": _coverage_bins(pm, ev, 0.95),
        "expected_minutes_mean": float(e_min.mean()),
        "actual_minutes_mean": float(act.mean()),
        "expected_minutes_mae": float(np.abs(e_min - act).mean()),
        "prior_mean_minutes_mae_phase2_style": float(np.abs(prior - act).mean()),
        "paired_mae_diff_model_minus_prior": cluster_diff_summary(
            np.abs(e_min - act) - np.abs(prior - act), cl, seed
        ),
        "share_exit_before_90_actual": float((ev < N_BINS).mean()),
        "share_exit_before_90_predicted": float(1.0 - pm[:, -1].mean()),
    }
    B = bench[bench["split"] == "test"]
    B = B[B.index.isin(mp["sq"].index)]
    pb = mp["sq"].loc[B.index].to_numpy()
    evb = B["ev"].to_numpy(np.int64)
    baseb = walk_forward(
        bench,
        B,
        lambda tr: GroupEmpirical().fit(tr, tr["ev"].to_numpy(np.int64)),
        lambda m, g: m.pmf(g),
        guard,
        width=N_BINS + 1,
        min_rows=800,
        context="minutes-base-b",
    )
    okb = ~np.isnan(baseb).any(axis=1)
    app = (evb < N_BINS).astype(float)
    p_app, p_app_b = 1.0 - pb[:, -1], 1.0 - baseb[:, -1]
    clb = B["sb_match_id"].to_numpy()
    lsb_m = -np.log(np.clip(pb[np.arange(len(evb)), evb], 1e-12, 1))
    lsb_b = -np.log(np.clip(baseb[np.arange(len(evb)), evb], 1e-12, 1))
    grid_b = bench_minutes_grid(l_bar)
    cond = pb[:, :-1] / np.clip(pb[:, :-1].sum(axis=1, keepdims=True), 1e-12, None)
    e_min_b = cond @ grid_b
    appd = app > 0
    res["bench"] = {
        "n": int(len(B)),
        "appear_model": binary_metrics(p_app[okb], app[okb]),
        "appear_group_empirical_baseline": binary_metrics(p_app_b[okb], app[okb]),
        "paired_appear_log_loss_diff": cluster_diff_summary(
            (M.log_loss_binary(p_app, app) - M.log_loss_binary(p_app_b, app))[okb], clb[okb], seed
        ),
        "reliability_appear_model": M.reliability_bins(p_app[okb], app[okb]),
        "entry_log_score_model": float(lsb_m[okb].mean()),
        "entry_log_score_baseline": float(lsb_b[okb].mean()),
        "paired_entry_log_score_diff": cluster_diff_summary((lsb_m - lsb_b)[okb], clb[okb], seed),
        "minutes_given_appearance_mae": float(
            np.abs(e_min_b[appd] - B["minutes"].to_numpy(float)[appd]).mean()
        ),
        "mean_expected_minutes_given_appearance": float(e_min_b[appd].mean()),
        "mean_actual_minutes_given_appearance": float(B["minutes"].to_numpy(float)[appd].mean()),
    }
    res["_pit_hist"] = res["starters"]["pit_histogram_10"]
    return res


# ------------------------------------------------------------------------------- team shots


def _nb_loglik(y: FloatArray, mu: FloatArray, alpha: float) -> FloatArray:
    from scipy.special import gammaln

    mu = np.maximum(mu, 1e-9)
    if alpha <= 1e-9:
        return y * np.log(mu) - mu - gammaln(y + 1)  # type: ignore[no-any-return]
    r = 1.0 / alpha
    return (  # type: ignore[no-any-return]
        gammaln(y + r)
        - gammaln(y + 1)
        - gammaln(r)
        + r * np.log(r / (r + mu))
        + y * np.log(mu / (r + mu))
    )


class ShotMap:
    """Map football-data shot-rating intensity to StatsBomb team shots: log mu = a + b log(lam),
    plus a negative-binomial overdispersion alpha fitted by maximum likelihood."""

    def fit(self, df: pd.DataFrame) -> "ShotMap":
        from scipy.optimize import minimize_scalar
        from sklearn.linear_model import PoissonRegressor

        x = df["x"].to_numpy(float).reshape(-1, 1)
        y = df["shots"].to_numpy(float)
        reg = PoissonRegressor(alpha=0.0, max_iter=300).fit(x, y)
        self.a, self.b = float(reg.intercept_), float(reg.coef_[0])
        mu = np.exp(self.a + self.b * x[:, 0])
        res = minimize_scalar(
            lambda la: -_nb_loglik(y, mu, float(np.exp(la))).sum(), bounds=(-8, 1), method="bounded"
        )
        self.alpha = float(np.exp(res.x))
        return self

    def predict(self, df: pd.DataFrame) -> FloatArray:
        mu = np.exp(self.a + self.b * df["x"].to_numpy(float))
        return np.stack([mu, np.full(len(df), self.alpha)], axis=1)


def stage_team_shots(
    d: P4Data, guard: SplitGuard, seed: int
) -> tuple[dict[str, Any], pd.DataFrame]:
    from edgeforge.evaluation.splits import load_split_ids
    from edgeforge.evaluation.team_model import ev_frame, league_data
    from edgeforge.evaluation.team_model import walk_forward as dc_walk
    from edgeforge.features.pit import fd_clock

    con = d.con
    raw = con.execute(
        "SELECT match_id, league, season, season_start_year, date, kickoff_local, home, away,"
        " hs AS fthg, as_ AS ftag FROM matches WHERE hs IS NOT NULL AND as_ IS NOT NULL"
    ).df()
    fds = fd_clock(raw)
    ld = league_data(fds)
    t = load_split_ids("team_2015_16")
    have = set(fds["match_id"])
    ev_tune = ev_frame(fds, [i for i in t["decay_tuning_2013_14"] if i in have])
    guard.check_tuning(ev_tune["match_id"].tolist(), "shot-rating half-life tuning")

    def score(hl: float) -> float:
        pr = dc_walk(ld, ev_tune, int(round(hl)), guard, fit_rho=False)
        m = ev_tune[["match_id", "fthg", "ftag"]].merge(pr, on="match_id")
        ll = _nb_loglik(m["fthg"].to_numpy(float), m["lam_h"].to_numpy(float), 0.0)
        ll = np.concatenate(
            [ll, _nb_loglik(m["ftag"].to_numpy(float), m["lam_a"].to_numpy(float), 0.0)]
        )
        return float(-ll.mean())

    tuned = tune_interior(
        "shot_rating_half_life_days",
        score,
        [90, 180, 365, 730],
        widen_low=lambda x: x / 2,
        widen_high=lambda x: x * 2,
        lower_bound=15.0,
    )
    hl = int(round(require_tuned(tuned)))
    ev_all = ev_frame(fds, [i for i in t["eval_2015_16_all"] if i in have])
    fd_lam = dc_walk(ld, ev_all, hl, guard, fit_rho=False)
    link = con.execute("SELECT sb_match_id, fd_match_id FROM sb_match_link").df()
    rows = d.team_shots.merge(link, on="sb_match_id").merge(
        fd_lam.rename(columns={"match_id": "fd_match_id"}), on="fd_match_id"
    )
    rows["x"] = np.log(np.where(rows["is_home"].astype(bool), rows["lam_h"], rows["lam_a"]))
    rows = rows.reset_index(drop=True)
    tv = rows[rows["split"].isin(["burn", "tune"])]
    tune = tv[tv["split"] == "tune"]
    guard.check_tuning(tv["sb_match_id"].unique().tolist(), "team shot mapping tuning")
    pr_tune = walk_forward(
        tv,
        tune,
        lambda tr: ShotMap().fit(tr),
        lambda m, g: m.predict(g),
        guard,
        width=2,
        min_rows=300,
        context="shotmap-tune",
    )
    y_t = tune["shots"].to_numpy(float)
    ok = ~np.isnan(pr_tune).any(axis=1)
    ll_pois = _nb_loglik(y_t[ok], pr_tune[ok, 0], 0.0).mean()
    ll_nb = _nb_loglik(y_t[ok], pr_tune[ok, 0], float(np.median(pr_tune[ok, 1]))).mean()
    use_nb = bool(ll_nb > ll_pois)  # chosen on the tuning window
    te = rows[rows["split"] == "test"]
    pr_te = walk_forward(
        rows,
        te,
        lambda tr: ShotMap().fit(tr),
        lambda m, g: m.predict(g),
        guard,
        width=2,
        min_rows=300,
        context="shotmap-test",
    )
    base_mu = walk_forward(
        rows,
        te,
        lambda tr: float(tr["shots"].mean()),
        lambda m, g: np.full(len(g), m),
        guard,
        min_rows=300,
        context="shots-baseline",
    )
    yt = te["shots"].to_numpy(float)
    okt = ~np.isnan(pr_te).any(axis=1) & ~np.isnan(base_mu)
    alpha_used = pr_te[:, 1] if use_nb else np.zeros(len(te))
    ll_m = np.array(
        [_nb_loglik(yt[i : i + 1], pr_te[i : i + 1, 0], alpha_used[i])[0] for i in range(len(yt))]
    )
    ll_p = _nb_loglik(yt, pr_te[:, 0], 0.0)
    ll_nbt = np.array(
        [_nb_loglik(yt[i : i + 1], pr_te[i : i + 1, 0], pr_te[i, 1])[0] for i in range(len(yt))]
    )
    ll_b = _nb_loglik(yt, base_mu, 0.0)
    cl = te["sb_match_id"].to_numpy()
    r = 1.0 / np.maximum(alpha_used, 1e-9)
    from scipy.stats import nbinom, poisson

    def cdf(yv: FloatArray) -> FloatArray:
        if use_nb:
            return nbinom.cdf(yv, r, r / (r + pr_te[:, 0]))  # type: ignore[no-any-return]
        return poisson.cdf(yv, pr_te[:, 0])  # type: ignore[no-any-return]

    u = np.random.default_rng(seed).uniform(size=len(yt))
    pit = np.where(okt, cdf(yt - 1) + u * (cdf(yt) - cdf(yt - 1)), np.nan)
    res: dict[str, Any] = {
        "tuned_half_life": tuned.as_record(),
        "mapping_note": "football-data HS/AS ratings mapped to StatsBomb shot definition by log-linear Poisson regression, refitted weekly",
        "tuning_window_log_lik": {
            "poisson": float(ll_pois),
            "negative_binomial": float(ll_nb),
            "chosen": "negative_binomial" if use_nb else "poisson",
        },
        "median_alpha_tune": float(np.median(pr_tune[ok, 1])),
        "n_test_team_rows": int(okt.sum()),
        "test_mean_log_lik": {
            "chosen_model": float(ll_m[okt].mean()),
            "poisson": float(ll_p[okt].mean()),
            "negative_binomial": float(ll_nbt[okt].mean()),
            "league_mean_baseline_poisson": float(ll_b[okt].mean()),
        },
        "paired_chosen_minus_baseline_log_lik": cluster_diff_summary(
            (ll_m - ll_b)[okt], cl[okt], seed
        ),
        "paired_nb_minus_poisson_log_lik": cluster_diff_summary(
            (ll_nbt - ll_p)[okt], cl[okt], seed
        ),
        "pit_histogram_10": M.pit_histogram(pit[okt]),
        "mean_pred": float(pr_te[okt, 0].mean()),
        "mean_obs": float(yt[okt].mean()),
        "mae": float(np.abs(pr_te[okt, 0] - yt[okt]).mean()),
        "mae_baseline": float(np.abs(base_mu[okt] - yt[okt]).mean()),
        "mapping_slope_b": None,
    }
    out = rows[["sb_match_id", "is_home", "team_id", "split", "shots", "lam_h", "lam_a"]].copy()
    out["mu"] = np.nan
    out["alpha"] = np.nan
    out.loc[tune.index, ["mu", "alpha"]] = pr_tune
    out.loc[te.index, ["mu", "alpha"]] = pr_te
    out["alpha_used"] = out["alpha"] if use_nb else 0.0
    out["use_nb"] = use_nb
    return res, out
