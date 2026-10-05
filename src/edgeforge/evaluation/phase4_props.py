"""Phase 4 player markets: lineups-state pricing, opening-state mixture, plug-in vs full minutes,
coherence diagnostic. Markets (D-030): shots 1+/2+/3+, SoT 1+/2+, anytime scorer, settled on
players who appear.
"""

import logging
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.baselines_player import predict_lambda
from edgeforge.evaluation.common import binary_metrics
from edgeforge.evaluation.phase4 import cluster_diff_summary
from edgeforge.evaluation.phase4_data import P4Data
from edgeforge.evaluation.splits import SplitGuard
from edgeforge.evaluation.stats import benjamini_hochberg
from edgeforge.evaluation.tuning import Tuned, require_tuned, tune_interior
from edgeforge.models.participation import (
    bench_minutes_grid,
    starter_minutes_grid,
)
from edgeforge.models.shots_model import (
    ShotParams,
    attach_group_priors,
    player_quantities,
    price_markets,
)

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]
MARKETS = ["shots_1plus", "shots_2plus", "shots_3plus", "sot_1plus", "sot_2plus", "anytime_scorer"]
THRESH = {"shots_1plus": ("shots", 1), "shots_2plus": ("shots", 2), "shots_3plus": ("shots", 3),
          "sot_1plus": ("sot", 1), "sot_2plus": ("sot", 2), "anytime_scorer": ("goals", 1)}  # fmt: skip
DEFAULTS = ShotParams(k=1.0, alpha=0.0, a_sot=10.0, b_xg=10.0, c_fin=50.0)


def _replace(p: ShotParams, **kw: float) -> ShotParams:
    return ShotParams(**{**p.__dict__, **kw})


def minutes_arrays(
    is_starter: NDArray[np.bool_], pmf: FloatArray, l_bar: float
) -> tuple[FloatArray, FloatArray]:
    """Weights and minute values on a common 20-point grid. Bench players are conditioned on
    appearing (the void rule): entry bins renormalised, 'never enters' dropped."""
    gs = starter_minutes_grid(l_bar)
    gb = bench_minutes_grid(l_bar)
    gb = np.append(gb, gb[-1])
    cond = pmf.copy()
    cond[:, -1] = 0.0
    cond = cond / np.clip(cond.sum(axis=1, keepdims=True), 1e-12, None)
    w = np.where(is_starter[:, None], pmf, cond)
    g = np.where(is_starter[:, None], gs[None, :], gb[None, :])
    return w, g


def lineup_inputs(
    d: P4Data, mp: dict[str, pd.DataFrame], shots: pd.DataFrame, split: str, l_bar: float
) -> pd.DataFrame:
    """Squad rows of one split with everything needed to price them, as of kickoff - 60 min."""
    sq = d.squad[(d.squad["split"] == split) & d.squad.index.isin(mp["sq"].index)].copy()
    sq = sq.reset_index().rename(columns={"index": "sq_index"})
    pmf = mp["sq"].loc[sq["sq_index"]].to_numpy()
    sq["is_starter"] = sq["announced_starter"].astype(bool)
    w, g = minutes_arrays(sq["is_starter"].to_numpy(), pmf, l_bar)
    mu = shots[["sb_match_id", "team_id", "mu", "alpha_used"]].drop_duplicates(
        ["sb_match_id", "team_id"]
    )
    sq = sq.merge(mu, on=["sb_match_id", "team_id"], how="left")
    sq = attach_group_priors(sq, d.gr_lineups)
    sq.attrs["W"], sq.attrs["G"] = w, g
    sq["p_never"] = np.where(sq["is_starter"], np.nan, pmf[:, -1])
    sq["minutes_mean"] = (w * g).sum(axis=1)
    return sq


def price_rows(
    rows: pd.DataFrame, p: ShotParams, l_bar: float, full: bool, mu_col: str = "mu"
) -> dict[str, FloatArray]:
    q = player_quantities(rows, p)
    return price_markets(
        q,
        rows[mu_col].to_numpy(float),
        rows.attrs["G"],
        rows.attrs["W"],
        rows["minutes_mean"].to_numpy(float),
        l_bar,
        p.alpha,
        full,
    )


def _eligible(rows: pd.DataFrame) -> NDArray[np.bool_]:
    return (
        rows["appeared"].astype(bool).to_numpy()
        & rows["mu"].notna().to_numpy()
        & rows["omega_g"].notna().to_numpy()
        & np.isfinite(rows["minutes_mean"].to_numpy(float))
    )


def outcome_arrays(rows: pd.DataFrame) -> dict[str, FloatArray]:
    out = {}
    for m, (col, thr) in THRESH.items():
        out[m] = (rows[col].to_numpy(float) >= thr).astype(float)
    return out


def tune_player_params(
    d: P4Data, rows_tune: pd.DataFrame, l_bar: float, guard: SplitGuard
) -> tuple[ShotParams, dict[str, Tuned]]:
    """Coordinate-wise tuning on the tuning window, every step under the D-040 grid-edge rule."""
    guard.check_tuning(rows_tune["sb_match_id"].unique().tolist(), "player parameter tuning")
    ok = _eligible(rows_tune)
    rt = rows_tune[ok]
    rt.attrs["W"], rt.attrs["G"] = rows_tune.attrs["W"][ok], rows_tune.attrs["G"][ok]
    y = outcome_arrays(rt)

    def ll(p: ShotParams, markets: list[str]) -> float:
        pr = price_rows(rt, p, l_bar, full=True)
        return float(np.mean([M.log_loss_binary(pr[m], y[m]).mean() for m in markets]))

    cur = DEFAULTS
    tuned: dict[str, Tuned] = {}
    shots_m = ["shots_1plus", "shots_2plus", "shots_3plus"]
    t = tune_interior(
        "shot_share_k",
        lambda v: ll(_replace(cur, k=v), shots_m),
        [0.5, 1, 2, 4, 8],
        lower_bound=0.0,
        widen_low=lambda x: x / 2,
        widen_high=lambda x: x * 2,
    )
    cur = _replace(cur, k=require_tuned(t))
    tuned["k"] = t
    t = tune_interior(
        "shot_overdispersion_alpha",
        lambda v: ll(_replace(cur, alpha=v), shots_m),
        [0.0, 0.05, 0.1, 0.2, 0.4],
        lower_bound=0.0,
        widen_high=lambda x: x * 2,
    )
    cur = _replace(cur, alpha=require_tuned(t))
    tuned["alpha"] = t
    t = tune_interior(
        "sot_prior_strength_a",
        lambda v: ll(_replace(cur, a_sot=v), ["sot_1plus", "sot_2plus"]),
        [1, 3, 10, 30, 100],
        widen_low=lambda x: x / 3,
        widen_high=lambda x: x * 3,
        lower_bound=0.1,
    )
    cur = _replace(cur, a_sot=require_tuned(t))
    tuned["a_sot"] = t
    t = tune_interior(
        "xg_per_shot_shrinkage_b",
        lambda v: ll(_replace(cur, b_xg=v), ["anytime_scorer"]),
        [1, 3, 10, 30, 100],
        widen_low=lambda x: x / 3,
        widen_high=lambda x: x * 3,
        lower_bound=0.1,
    )
    cur = _replace(cur, b_xg=require_tuned(t))
    tuned["b_xg"] = t
    t = tune_interior(
        "finishing_shrinkage_c",
        lambda v: ll(_replace(cur, c_fin=v), ["anytime_scorer"]),
        [3, 10, 30, 100, 300],
        widen_low=lambda x: x / 3,
        widen_high=lambda x: x * 3,
        lower_bound=0.1,
        upper_bound=1e5,
    )
    cur = _replace(cur, c_fin=require_tuned(t))
    tuned["c_fin"] = t
    return cur, tuned


def _pois_ge(lam: FloatArray, k: int) -> FloatArray:
    from scipy.stats import poisson

    out: FloatArray = np.clip(poisson.sf(k - 1, np.maximum(lam, 1e-9)), 1e-6, 1 - 1e-6)
    return out


def phase2_baseline(
    rows: pd.DataFrame, gr: pd.DataFrame, n: int, k: float
) -> dict[str, FloatArray]:
    """The Phase 2 rolling per-90 x plug-in-minutes baseline, priced for all six markets."""
    lam = predict_lambda(rows, gr, n, k)
    assert len(lam) == len(rows)  # predict_lambda keeps the row order of its input
    m = lam
    return {
        "shots_1plus": _pois_ge(m["lam_shots"].to_numpy(float), 1),
        "shots_2plus": _pois_ge(m["lam_shots"].to_numpy(float), 2),
        "shots_3plus": _pois_ge(m["lam_shots"].to_numpy(float), 3),
        "sot_1plus": _pois_ge(m["lam_sot"].to_numpy(float), 1),
        "sot_2plus": _pois_ge(m["lam_sot"].to_numpy(float), 2),
        "anytime_scorer": _pois_ge(m["lam_goals"].to_numpy(float), 1),
    }


def compare_markets(
    new: dict[str, FloatArray],
    base: dict[str, FloatArray],
    y: dict[str, FloatArray],
    cluster: NDArray[Any],
    seed: int,
    ok: NDArray[np.bool_],
) -> dict[str, Any]:
    """Per-market metrics, paired bootstrap of the log-loss difference and BH across the family."""
    res: dict[str, Any] = {"markets": {}}
    ps, names = [], []
    for m in MARKETS:
        okm = ok & np.isfinite(new[m]) & np.isfinite(base[m])
        d = M.log_loss_binary(new[m][okm], y[m][okm]) - M.log_loss_binary(base[m][okm], y[m][okm])
        s = cluster_diff_summary(d, cluster[okm], seed)
        res["markets"][m] = {
            "n": int(okm.sum()),
            "new": binary_metrics(new[m][okm], y[m][okm]),
            "baseline": binary_metrics(base[m][okm], y[m][okm]),
            "log_loss_diff_new_minus_baseline": s,
            "reliability_new": M.reliability_bins(new[m][okm], y[m][okm]),
            "reliability_baseline": M.reliability_bins(base[m][okm], y[m][okm]),
        }
        ps.append(s["p"])
        names.append(m)
    q, keep = benjamini_hochberg(ps, 0.10)
    for m, qq, kk in zip(names, q, keep, strict=True):
        res["markets"][m]["q_bh"], res["markets"][m]["survives_bh_10pct"] = qq, bool(kk)
    res["bh_family"] = {
        "n_tests": len(ps),
        "fdr": 0.10,
        "survivors": [m for m, kk in zip(names, keep, strict=True) if kk],
    }
    return res


def evaluate_lineups(
    d: P4Data,
    rows: pd.DataFrame,
    params: ShotParams,
    l_bar: float,
    base_cfg: tuple[int, float],
    seed: int,
) -> tuple[dict[str, Any], dict[str, FloatArray], dict[str, FloatArray]]:
    ok = _eligible(rows)
    n, k = base_cfg
    full = price_rows(rows, params, l_bar, full=True)
    plug = price_rows(rows, params, l_bar, full=False)
    base = phase2_baseline(rows, d.gr_lineups, n, k)
    y = outcome_arrays(rows)
    cl = rows["sb_match_id"].to_numpy()
    res: dict[str, Any] = {
        "comparison_E_full_distribution_vs_phase2_baseline": compare_markets(
            full, base, y, cl, seed, ok
        ),
        "plugin_minutes_vs_phase2_baseline": compare_markets(plug, base, y, cl, seed, ok),
    }
    # plug-in vs full distribution (item 7)
    pv: dict[str, Any] = {}
    for m in MARKETS:
        okm = ok & np.isfinite(full[m]) & np.isfinite(plug[m])
        diff = full[m][okm] - plug[m][okm]
        ll = M.log_loss_binary(full[m][okm], y[m][okm]) - M.log_loss_binary(plug[m][okm], y[m][okm])
        odds_f, odds_p = 1 / full[m][okm], 1 / plug[m][okm]
        rel = (odds_p - odds_f) / odds_f
        pv[m] = {
            "n": int(okm.sum()),
            "mean_prob_full": float(full[m][okm].mean()),
            "mean_prob_plugin": float(plug[m][okm].mean()),
            "mean_abs_prob_diff": float(np.abs(diff).mean()),
            "p95_abs_prob_diff": float(np.percentile(np.abs(diff), 95)),
            "mean_signed_prob_diff_full_minus_plugin": float(diff.mean()),
            "mean_abs_relative_odds_diff": float(np.abs(rel).mean()),
            "p95_abs_relative_odds_diff": float(np.percentile(np.abs(rel), 95)),
            "log_loss_diff_full_minus_plugin": cluster_diff_summary(ll, cl[okm], seed),
            "log_loss_full": float(M.log_loss_binary(full[m][okm], y[m][okm]).mean()),
            "log_loss_plugin": float(M.log_loss_binary(plug[m][okm], y[m][okm]).mean()),
        }
    res["plugin_vs_full_minutes"] = pv
    res["n_priced_rows"] = int(ok.sum())
    return res, full, base


def coherence(
    d: P4Data, rows: pd.DataFrame, params: ShotParams, l_bar: float, preds: pd.DataFrame
) -> dict[str, Any]:
    """Sum of players' expected goals vs the Dixon-Coles team expected goals, per match side."""
    q = player_quantities(rows, params)
    pr = price_markets(
        q,
        rows["mu"].to_numpy(float),
        rows.attrs["G"],
        rows.attrs["W"],
        rows["minutes_mean"].to_numpy(float),
        l_bar,
        params.alpha,
        True,
    )
    eg_cond = pr["_exp_shots"] * q["g_shot"].to_numpy(float)
    p_app = np.where(rows["is_starter"], 1.0, 1.0 - rows["p_never"].fillna(1.0))
    r = rows.assign(eg=eg_cond * p_app)
    r = r[np.isfinite(r["eg"])]
    side = r.groupby(["sb_match_id", "is_home"])["eg"].sum().reset_index()
    link = d.con.execute("SELECT sb_match_id, fd_match_id FROM sb_match_link").df()
    side = side.merge(link, on="sb_match_id").merge(
        preds.rename(columns={"match_id": "fd_match_id"})[["fd_match_id", "lam_h", "lam_a"]],
        on="fd_match_id",
    )
    side["dc"] = np.where(side["is_home"].astype(bool), side["lam_h"], side["lam_a"])
    side["ratio"] = side["eg"] / side["dc"]
    tot = side.groupby("sb_match_id").agg(eg=("eg", "sum"), dc=("dc", "sum"))
    tot["ratio"] = tot["eg"] / tot["dc"]

    def q5(s: pd.Series) -> dict[str, float]:
        return {str(k): float(v) for k, v in s.quantile([0.05, 0.25, 0.5, 0.75, 0.95]).items()}

    return {
        "n_matches": int(len(tot)),
        "note": "players' expected goals exclude own goals and are not forced to match; own goals are about 2% of goals",
        "ratio_player_sum_over_dixon_coles_per_match_total": {
            "mean": float(tot["ratio"].mean()),
            "quantiles": q5(tot["ratio"]),
        },
        "ratio_per_team_side": {
            "mean": float(side["ratio"].mean()),
            "quantiles": q5(side["ratio"]),
        },
        "ratio_home_side_mean": float(side[side["is_home"].astype(bool)]["ratio"].mean()),
        "ratio_away_side_mean": float(side[~side["is_home"].astype(bool)]["ratio"].mean()),
        "mean_player_sum": float(tot["eg"].mean()),
        "mean_dixon_coles": float(tot["dc"].mean()),
        "_ratios": tot["ratio"].to_numpy(),
    }


# ----------------------------------------------------------------------------- opening state


def opening_inputs(
    d: P4Data,
    mp: dict[str, pd.DataFrame],
    start_pred: pd.DataFrame,
    shots: pd.DataFrame,
    split: str,
    l_bar: float,
) -> pd.DataFrame:
    c = d.cand[(d.cand["split"] == split) & d.cand.index.isin(mp["cs"].index)].copy()
    c = c.reset_index().rename(columns={"index": "cand_index"})
    c = c.merge(start_pred, on=["sb_match_id", "player_id", "team_id"], how="inner")
    mu = shots[["sb_match_id", "team_id", "mu", "alpha_used"]].drop_duplicates(
        ["sb_match_id", "team_id"]
    )
    c = c.merge(mu, on=["sb_match_id", "team_id"], how="left")
    c = attach_group_priors(c, d.gr_opening)
    ps = mp["cs"].loc[c["cand_index"]].to_numpy()
    pb = mp["cb"].loc[c["cand_index"]].to_numpy()
    n = len(c)
    ws, gs = minutes_arrays(np.ones(n, dtype=bool), ps, l_bar)
    wb, gb = minutes_arrays(np.zeros(n, dtype=bool), pb, l_bar)
    c.attrs.update({"Ws": ws, "Gs": gs, "Wb": wb, "Gb": gb})
    c["p_appear_if_bench"] = 1.0 - pb[:, -1]
    c["minutes_mean_s"] = (ws * gs).sum(axis=1)
    c["minutes_mean_b"] = (wb * gb).sum(axis=1)
    return c


def price_opening(
    c: pd.DataFrame, p: ShotParams, l_bar: float, full: bool
) -> dict[str, FloatArray]:
    """Mixture over start / bench / not in squad, conditional on appearance."""
    q = player_quantities(c, p)
    mu = c["mu"].to_numpy(float)
    ps = price_markets(
        q,
        mu,
        c.attrs["Gs"],
        c.attrs["Ws"],
        c["minutes_mean_s"].to_numpy(float),
        l_bar,
        p.alpha,
        full,
    )
    pb = price_markets(
        q,
        mu,
        c.attrs["Gb"],
        c.attrs["Wb"],
        c["minutes_mean_b"].to_numpy(float),
        l_bar,
        p.alpha,
        full,
    )
    s = c["p_start"].to_numpy(float)
    b = c["p_bench"].to_numpy(float) * c["p_appear_if_bench"].to_numpy(float)
    den = np.clip(s + b, 1e-9, None)
    return {m: (s * ps[m] + b * pb[m]) / den for m in MARKETS}


def opening_baseline(c: pd.DataFrame, gr: pd.DataFrame, n: int, k: float) -> dict[str, FloatArray]:
    """Opening-state baseline: rolling per-90 rate x mean minutes per past appearance (void rule)."""
    d = c.copy()
    d["announced_starter"] = False  # unused by the rate part
    lam = predict_lambda(d, gr, n, k)
    assert len(lam) == len(c)  # keyed by row order: a player can be a candidate for two clubs
    m = lam
    e_min = ((c[f"mins_{n}"] + 2 * 60.0) / (c[f"n_app_{n}"] + 2.0)).to_numpy(float)
    rate90 = (m["lam_shots"] * 90.0 / m["exp_min"]).to_numpy(float)
    rate_sot = (m["lam_sot"] * 90.0 / m["exp_min"]).to_numpy(float)
    rate_g = (m["lam_goals"] * 90.0 / m["exp_min"]).to_numpy(float)
    f = e_min / 90.0
    return {
        "shots_1plus": _pois_ge(rate90 * f, 1), "shots_2plus": _pois_ge(rate90 * f, 2),
        "shots_3plus": _pois_ge(rate90 * f, 3), "sot_1plus": _pois_ge(rate_sot * f, 1),
        "sot_2plus": _pois_ge(rate_sot * f, 2), "anytime_scorer": _pois_ge(rate_g * f, 1),
    }  # fmt: skip


def evaluate_opening(
    d: P4Data,
    c: pd.DataFrame,
    params: ShotParams,
    l_bar: float,
    base_cfg: tuple[int, float],
    seed: int,
) -> tuple[dict[str, Any], dict[str, FloatArray], dict[str, FloatArray]]:
    ok = (
        c["appeared"].astype(bool).to_numpy()
        & c["mu"].notna().to_numpy()
        & c["omega_g"].notna().to_numpy()
    )
    n, k = base_cfg
    full = price_opening(c, params, l_bar, full=True)
    plug = price_opening(c, params, l_bar, full=False)
    base = opening_baseline(c, d.gr_opening, n, k)
    y = outcome_arrays(c)
    cl = c["sb_match_id"].to_numpy()
    res = {
        "opening_model_vs_opening_baseline": compare_markets(full, base, y, cl, seed, ok),
        "n_priced_rows": int(ok.sum()),
    }
    # coverage: appearing players outside the candidate set
    sq = d.squad[(d.squad["split"] == "test") & d.squad["appeared"].astype(bool)]
    key = set(zip(c["sb_match_id"], c["player_id"], strict=True))
    outside = np.array(
        [(m, p) not in key for m, p in zip(sq["sb_match_id"], sq["player_id"], strict=True)]
    )
    res["coverage"] = {
        "appearing_players_test": int(len(sq)),
        "outside_candidate_set": int(outside.sum()),
        "share_players_outside": float(outside.mean()),
        "share_shots_outside": float(
            sq["shots"].to_numpy()[outside].sum() / max(sq["shots"].sum(), 1)
        ),
        "share_goals_outside": float(
            sq["goals"].to_numpy()[outside].sum() / max(sq["goals"].sum(), 1)
        ),
    }
    res["plugin_vs_full_minutes_opening"] = {
        m: {"mean_abs_prob_diff": float(np.abs(full[m] - plug[m])[ok].mean())} for m in MARKETS
    }
    return res, full, base
