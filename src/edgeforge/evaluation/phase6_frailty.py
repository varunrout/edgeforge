"""D-049: shared match-level gamma frailty experiment (labelled extra; Phase 5 stays the headline).

theta is fitted on matchweeks 10-14 (tune_interior, D-040) and the decision to adopt is made on
matchweeks 15-19 (D-043); both use only tuning-window matches, the D-047 templates (lineups
state) and the team markets. The test window is evaluated once, adopted or not, for the fitted
theta (or, when theta = 0 is chosen, the best positive grid value so that the before/after
under-prediction can still be reported).
"""

import logging
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.common import write_json
from edgeforge.evaluation.phase4 import cluster_diff_summary
from edgeforge.evaluation.phase5 import (
    TEMPLATE_META,
    evaluate_instance_outcome,
    rank_players,
    template_instances,
)
from edgeforge.evaluation.phase5_inputs import (
    Phase5Context,
    build_inputs,
    load_context,
    realised_players,
)
from edgeforge.evaluation.phase5_run import _model
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.splits import load_guard
from edgeforge.evaluation.tuning import Tuned, require_tuned, tune_interior
from edgeforge.models.dixon_coles import dc_grid
from edgeforge.provenance import provenance
from edgeforge.sga.engine import (
    SGAError,
    indicator_matrix,
    leg_ok_matrix,
    price_from_indicators,
    validate_sga,
)
from edgeforge.simulation.engine import ShotsGivenGoals, SimResult, simulate

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]
CMD = "edgeforge phase6 frailty"
UNDER_PREDICTED = ("T05", "T06", "T09", "T10", "T11")


def match_weeks(ctx: Phase5Context) -> dict[int, int]:
    c = ctx.d.clock
    return {int(m): int(w) for m, w in zip(c["sb_match_id"], c["match_week"], strict=True)}


def team_probs_from_sim(r: SimResult) -> dict[str, float]:
    g = r.team_goals
    return {
        "home": float((g[:, 0] > g[:, 1]).mean()),
        "draw": float((g[:, 0] == g[:, 1]).mean()),
        "away": float((g[:, 0] < g[:, 1]).mean()),
        "over_2.5": float((g.sum(axis=1) > 2.5).mean()),
        "btts": float(((g > 0).all(axis=1)).mean()),
    }


def team_probs_dc(lam_h: float, lam_a: float, rho: float) -> dict[str, float]:
    grid = dc_grid(np.array([lam_h]), np.array([lam_a]), rho, 10)[0]
    i, j = np.indices(grid.shape)
    return {
        "home": float(grid[i > j].sum()),
        "draw": float(grid[i == j].sum()),
        "away": float(grid[i < j].sum()),
        "over_2.5": float(grid[i + j > 2].sum()),
        "btts": float(grid[(i > 0) & (j > 0)].sum()),
    }


def team_loss(p: dict[str, float], score: tuple[int, int]) -> float:
    """Mean of the 1X2, over 2.5 and BTTS log losses for one match."""
    h, a = score
    pr = {"home": h > a, "draw": h == a, "away": h < a}
    ll_1x2 = -np.log(np.clip(p[next(k for k, v in pr.items() if v)], 1e-6, 1))
    o = float(h + a > 2.5)
    b = float(h > 0 and a > 0)
    ll_o = -(
        o * np.log(np.clip(p["over_2.5"], 1e-6, 1))
        + (1 - o) * np.log(np.clip(1 - p["over_2.5"], 1e-6, 1))
    )
    ll_b = -(
        b * np.log(np.clip(p["btts"], 1e-6, 1)) + (1 - b) * np.log(np.clip(1 - p["btts"], 1e-6, 1))
    )
    return float((ll_1x2 + ll_o + ll_b) / 3.0)


def simulate_records(
    ctx: Phase5Context,
    state: str,
    split: str,
    theta: float,
    n_sims: int,
    seed: int,
    model: ShotsGivenGoals,
    weeks: tuple[int, int] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """D-047 instances (named by this state's ranking) and team-market probabilities per match."""
    inputs, rows, prices = build_inputs(ctx, state, split)
    s_unc = prices["_p_appear"] * prices["anytime_scorer"]
    realised = realised_players(rows)
    mw = match_weeks(ctx)
    scores = {
        int(m): (int(h), int(a))
        for m, h, a in zip(
            ctx.scores["sb_match_id"],
            ctx.scores["home_score"],
            ctx.scores["away_score"],
            strict=True,
        )
    }
    inst: list[dict[str, Any]] = []
    team: list[dict[str, Any]] = []
    for m, inp in sorted(inputs.items()):
        if m not in scores or (weeks and not (weeks[0] <= mw[m] <= weeks[1])):
            continue
        res = simulate(inp, model, n_sims, seed, theta)
        tp = team_probs_from_sim(res)
        team.append(
            {
                "sb_match_id": m,
                "state": state,
                "theta": theta,
                **tp,
                "loss": team_loss(tp, scores[m]),
            }
        )
        rank = rank_players(inp, s_unc[inp.row_index])
        tmap = {int(p): int(t) for p, t in zip(inp.player_id, inp.team, strict=True)}
        for tpl, tag, sga in template_instances(rank):
            try:
                validate_sga(sga, tmap)
            except SGAError:
                continue
            ind, ok = indicator_matrix(sga, res)
            pr = price_from_indicators(ind, ok, leg_ok_matrix(sga, res))
            inst.append({
                "sb_match_id": m, "state": state, "theta": theta, "template": tpl, "side": tag,
                "p_sim": pr["joint"], "p_naive": pr["naive_product"], "n_eff": pr["n_effective_sims"],
                "y": evaluate_instance_outcome(sga, scores[m], realised.get(m, {})),
            })  # fmt: skip
    df = pd.DataFrame(inst)
    floor = 1.0 / (2.0 * n_sims)
    df["p_sim"] = df["p_sim"].clip(floor, 1 - floor)
    df["p_naive"] = df["p_naive"].clip(floor, 1 - floor)
    return df, pd.DataFrame(team)


def joint_ll(df: pd.DataFrame) -> float:
    """Mean over templates of the mean instance log loss (equal template weight)."""
    d = df[df["y"].notna()]
    ll = M.log_loss_binary(d["p_sim"].to_numpy(float), d["y"].to_numpy(float))
    return float(pd.Series(ll, index=d.index).groupby(d["template"]).mean().mean())


def _template_weighted_diff(a: pd.DataFrame, b: pd.DataFrame) -> tuple[FloatArray, NDArray[Any]]:
    """Per-instance log-loss differences (a minus b) with weights that give templates equal
    weight, and match ids for the cluster bootstrap. Frames hold identical instances."""
    k = ["sb_match_id", "template", "side"]
    m = a.merge(b, on=k, suffixes=("_a", "_b"))
    m = m[m["y_a"].notna()]
    d = M.log_loss_binary(
        m["p_sim_a"].to_numpy(float), m["y_a"].to_numpy(float)
    ) - M.log_loss_binary(m["p_sim_b"].to_numpy(float), m["y_a"].to_numpy(float))
    cnt = m.groupby("template")["template"].transform("count").to_numpy(float)
    w = (1.0 / cnt) / m["template"].nunique()
    # scaled so that the weighted mean over rows equals the mean of template means
    return d * w * len(m), m["sb_match_id"].to_numpy()


def per_template_diff(a: pd.DataFrame, b: pd.DataFrame, seed: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tpl in TEMPLATE_META:
        m = a[a["template"] == tpl].merge(
            b[b["template"] == tpl], on=["sb_match_id", "side"], suffixes=("_a", "_b")
        )
        m = m[m["y_a"].notna()]
        if len(m) < 30:
            continue
        d = M.log_loss_binary(
            m["p_sim_a"].to_numpy(float), m["y_a"].to_numpy(float)
        ) - M.log_loss_binary(m["p_sim_b"].to_numpy(float), m["y_a"].to_numpy(float))
        out[tpl] = cluster_diff_summary(d, m["sb_match_id"].to_numpy(), seed)
    return out


def under_prediction(df: pd.DataFrame) -> dict[str, dict[str, float]]:
    out = {}
    for tpl in TEMPLATE_META:
        d = df[(df["template"] == tpl) & df["y"].notna()]
        if len(d):
            out[tpl] = {
                "n": float(len(d)),
                "mean_predicted": float(d["p_sim"].mean()),
                "realised_rate": float(d["y"].mean()),
                "gap": float(d["p_sim"].mean() - d["y"].mean()),
            }
    return out


def run_frailty(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_config("data")
    p6 = load_config("phase6")["frailty"]
    p5 = load_config("phase5")
    n_sims, seed = int(p5["n_sims"]), int(cfg["seed"])
    ctx = load_context(cfg)
    model = _model(ctx)
    guard = load_guard()
    fit_w, cho_w = tuple(p6["fit_weeks"]), tuple(p6["choose_weeks"])
    mw = match_weeks(ctx)
    tune_ids = [
        m for m, w in mw.items() if fit_w[0] <= w <= cho_w[1] and m in set(ctx.d.ids["tune"])
    ]
    guard.check_tuning(tune_ids, "frailty theta tuning")
    cache: dict[float, tuple[pd.DataFrame, pd.DataFrame]] = {}

    def fit_half(theta: float) -> tuple[pd.DataFrame, pd.DataFrame]:
        if theta not in cache:
            cache[theta] = simulate_records(
                ctx, "lineups", "tune", theta, n_sims, seed, model, fit_w
            )
        return cache[theta]

    def score(theta: float) -> float:
        j, t = fit_half(theta)
        log.info(
            "theta %.3f: joint log loss %.5f team loss %.5f", theta, joint_ll(j), t["loss"].mean()
        )
        return joint_ll(j)

    tuned: Tuned = tune_interior(
        "frailty_theta", score, p6["theta_grid"], lower_bound=0.0, widen_high=lambda x: x * 2.0
    )
    theta_star = require_tuned(tuned)
    # ---- adoption decision on matchweeks 15-19
    j0, t0 = simulate_records(ctx, "lineups", "tune", 0.0, n_sims, seed, model, cho_w)
    pos = [th for th in sorted(cache) if th > 0]
    theta_eval = theta_star if theta_star > 0 else min(pos, key=lambda th: joint_ll(cache[th][0]))
    j1, t1 = simulate_records(ctx, "lineups", "tune", theta_eval, n_sims, seed, model, cho_w)
    dj, cj = _template_weighted_diff(j1, j0)
    sj = cluster_diff_summary(dj, cj, seed)
    tt = t1.merge(t0, on="sb_match_id", suffixes=("_f", "_0"))
    st = cluster_diff_summary(
        (tt["loss_f"] - tt["loss_0"]).to_numpy(float), tt["sb_match_id"].to_numpy(), seed
    )
    adopt = bool(
        theta_star > 0 and sj["estimate"] < 0 and sj["ci_high"] < 0 and not st["ci_low"] > 0
    )
    decision = {
        "theta_fitted_on_mw10_14": theta_star,
        "theta_evaluated": theta_eval,
        "choose_window_joint_log_loss_diff_theta_minus_zero": sj,
        "choose_window_team_market_log_loss_diff_theta_minus_zero": st,
        "adopted": adopt,
        "rule": "adopt iff theta>0 interior, joint log loss improves with CI excluding zero, and team-market log loss is not significantly worse",
    }
    log.info("D-049 decision: %s", decision)
    # ---- test window, once
    test: dict[str, Any] = {"theta": theta_eval}
    for state in ("lineups", "opening"):
        base_j, base_t = simulate_records(ctx, state, "test", 0.0, n_sims, seed, model)
        fr_j, fr_t = simulate_records(ctx, state, "test", theta_eval, n_sims, seed, model)
        dj2, cj2 = _template_weighted_diff(fr_j, base_j)
        tt2 = fr_t.merge(base_t, on="sb_match_id", suffixes=("_f", "_0"))
        pooled = fr_j[fr_j["y"].notna()]
        pooled0 = base_j[base_j["y"].notna()]
        test[state] = {
            "joint_log_loss_diff_theta_minus_zero_template_equal_weight": cluster_diff_summary(dj2, cj2, seed),
            "joint_log_loss_pooled": {
                "theta": float(M.log_loss_binary(pooled["p_sim"].to_numpy(float), pooled["y"].to_numpy(float)).mean()),
                "zero": float(M.log_loss_binary(pooled0["p_sim"].to_numpy(float), pooled0["y"].to_numpy(float)).mean()),
            },
            "team_market_log_loss_diff_theta_minus_zero": cluster_diff_summary(
                (tt2["loss_f"] - tt2["loss_0"]).to_numpy(float), tt2["sb_match_id"].to_numpy(), seed
            ),
            "team_market_log_loss": {"theta": float(fr_t["loss"].mean()), "zero": float(base_t["loss"].mean())},
            "under_prediction_zero": under_prediction(base_j),
            "under_prediction_theta": under_prediction(fr_j),
            "by_template_log_loss_diff": per_template_diff(fr_j, base_j, seed),
        }  # fmt: skip
    prov = provenance(CMD, cfg, ctx.version)
    out = {
        "provenance": prov,
        "tuning": tuned.as_record(),
        "decision": decision,
        "test": test,
        "under_predicted_templates": list(UNDER_PREDICTED),
    }
    write_json(resolve_path(cfg, "metrics_dir") / "phase6_frailty.json", out)
    recs = []
    for value, sc in tuned.history:
        recs.append(
            make_record(
                f"phase6::frailty::theta{value:g}", cfg, ctx.version, CMD,
                feature_set="D-047 templates (lineups) mw10-14", model="simulator_d014_gamma_frailty",
                hyperparameters={"theta": value}, validation_window="player_2015_16: tune mw10-14",
                metrics={"joint_log_loss": sc}, calibration={},
                notes="D-049 experiment; " + ("adopted" if adopt and value == theta_star else "not adopted"),
                status="promoted" if adopt and value == theta_star else "rejected",
            )
        )  # fmt: skip
    append_records(recs)
    return out
