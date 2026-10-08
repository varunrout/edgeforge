"""Phase 7 runner: Pillar B in-play team markets, evaluation against the Phase 2 naive baseline.

Specification: D-056 (committed before any test-window in-play result). Fitting uses
matchweeks 1-19 only; the kappa grid goes through D-040; evaluation is matchweeks 20-38 at
elapsed minutes 15/30/45/60/75/85 on the elapsed-seconds clock.
"""

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.stats import poisson

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.common import (
    FIGURES_DIR,
    binary_metrics,
    class_metrics,
    connect,
    warehouse_version,
    write_json,
)
from edgeforge.evaluation.inplay_model import (
    CHECKPOINTS_MIN,
    Params,
    build_segments,
    fit_params,
    held_out_loglik,
    price,
)
from edgeforge.evaluation.phase4 import cluster_diff_summary
from edgeforge.evaluation.plots import reliability_plot
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.splits import load_guard, load_split_ids
from edgeforge.evaluation.stats import benjamini_hochberg
from edgeforge.evaluation.tuning import require_tuned, tune_interior
from edgeforge.pricing.markets import score_grid
from edgeforge.provenance import provenance

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]
CMD = "edgeforge phase7 run"
LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
MARKETS = ("1x2", *[f"over_{x}" for x in LINES], "btts")
STRATA = ("level", "one_goal", "two_plus", "after_dismissal")
KAPPA_GRID = [0.0, 1.0, 10.0, 100.0, 1000.0, 10000.0]


# ----------------------------------------------------------------------------- data


def load_data(cfg: dict[str, Any]) -> dict[str, Any]:
    con = connect(cfg)
    sp = load_split_ids("player_2015_16")
    ids = {"burn": sp["burn_in_mw1_9"], "tune": sp["tune_mw10_19"], "test": sp["test_mw20_38"]}
    matches = con.execute(
        "SELECT sb_match_id, home, away, match_week, match_length_s, home_score, away_score FROM sb_matches"
    ).df()
    link = con.execute("SELECT sb_match_id, fd_match_id FROM sb_match_link").df()
    pr = pd.read_parquet(resolve_path(cfg, "processed_dir") / "team_model_preds.parquet")
    pr = pr[(pr["block"] == "team_2015_16") & (pr["test"] == "all_2015_16")]
    lam = link.merge(
        pr.rename(columns={"match_id": "fd_match_id"})[["fd_match_id", "lam_h", "lam_a"]],
        on="fd_match_id",
    ).drop(columns="fd_match_id")
    matches = matches.merge(lam, on="sb_match_id")
    events = con.execute("SELECT sb_match_id, kind, team, elapsed_s FROM events_timeline").df()
    base_lam = pd.read_parquet(
        resolve_path(cfg, "processed_dir") / "baseline_lambdas_team_2015_16.parquet"
    ).merge(link, left_on="match_id", right_on="fd_match_id")
    base_cfg = json.loads(
        (resolve_path(cfg, "metrics_dir") / "baseline_inplay.json").read_text(encoding="utf-8")
    )
    return {
        "con": con,
        "ids": ids,
        "matches": matches,
        "events": events,
        "base_lam": base_lam,
        "mean_len": float(base_cfg["mean_match_length_s_fitted_mw1_19"]),
    }


def states(matches: pd.DataFrame, events: pd.DataFrame, t_s: float) -> pd.DataFrame:
    """Score and dismissals per match from events strictly before elapsed second t."""
    e = events[events["elapsed_s"] < t_s]
    m = matches[["sb_match_id", "home", "away"]].merge(e, on="sb_match_id", how="left")
    goal = m["kind"].isin(["goal", "own_goal"])
    red = m["kind"].isin(["red_card", "second_yellow"])
    out = matches[["sb_match_id"]].copy()
    for name, mask, side in (
        ("h0", goal, "home"),
        ("a0", goal, "away"),
        ("red_h", red, "home"),
        ("red_a", red, "away"),
    ):
        c = m[mask & (m["team"] == m[side])].groupby("sb_match_id").size()
        out[name] = out["sb_match_id"].map(c).fillna(0).astype(int)
    return out


def proposition_frame(
    d: dict[str, Any], ids: list[int], cps: tuple[int, ...] = CHECKPOINTS_MIN
) -> pd.DataFrame:
    mm = d["matches"][d["matches"]["sb_match_id"].isin(ids)].reset_index(drop=True)
    rows = []
    for cp in cps:
        st = states(mm, d["events"], cp * 60.0)
        x = mm.merge(st, on="sb_match_id")
        x["checkpoint"], x["t_s"] = cp, cp * 60.0
        rows.append(x)
    out: pd.DataFrame = pd.concat(rows, ignore_index=True)
    return out


# ----------------------------------------------------------------------------- baseline and truth


def baseline_prices(
    df: pd.DataFrame, base_lam: pd.DataFrame, mean_len: float
) -> dict[str, FloatArray]:
    """Phase 2 naive baseline: static Poisson intensities x share of the match left."""
    b = base_lam.drop_duplicates("sb_match_id").set_index("sb_match_id")
    lam_h = df["sb_match_id"].map(b["lam_h"]).to_numpy(float)
    lam_a = df["sb_match_id"].map(b["lam_a"]).to_numpy(float)
    frac = np.maximum(0.0, (mean_len - df["t_s"].to_numpy(float)) / mean_len)
    lh, la = lam_h * frac, lam_a * frac
    h0, a0 = df["h0"].to_numpy(np.int64), df["a0"].to_numpy(np.int64)
    grid = score_grid(lh, la)
    g = grid.shape[1]
    x = np.arange(g)[:, None]
    y = np.arange(g)[None, :]
    p3 = np.empty((len(df), 3))
    for i in range(len(df)):
        d = a0[i] - h0[i]
        p3[i] = [
            grid[i][(x - y) > d].sum(),
            grid[i][(x - y) == d].sum(),
            grid[i][(x - y) < d].sum(),
        ]
    out: dict[str, FloatArray] = {"1x2": p3, "exp_remaining": lh + la}
    for line in LINES:
        out[f"over_{line}"] = poisson.sf(np.floor(line) - (h0 + a0), lh + la)
    ph, pa = 1.0 - np.exp(-lh), 1.0 - np.exp(-la)
    out["btts"] = np.where(
        (h0 >= 1) & (a0 >= 1), 1.0, np.where(h0 >= 1, pa, np.where(a0 >= 1, ph, ph * pa))
    )
    return out


def truth(df: pd.DataFrame) -> dict[str, Any]:
    fh, fa = df["home_score"].to_numpy(int), df["away_score"].to_numpy(int)
    y: dict[str, Any] = {
        "1x2": np.where(fh > fa, 0, np.where(fh == fa, 1, 2)).astype(np.int64),
        "btts": ((fh > 0) & (fa > 0)).astype(float),
    }
    for line in LINES:
        y[f"over_{line}"] = ((fh + fa) > line).astype(float)
    y["remaining"] = (fh + fa - df["h0"].to_numpy(int) - df["a0"].to_numpy(int)).astype(float)
    return y


def undecided(df: pd.DataFrame) -> dict[str, NDArray[np.bool_]]:
    tot = (df["h0"] + df["a0"]).to_numpy(int)
    out = {
        "1x2": np.ones(len(df), bool),
        "btts": ~((df["h0"].to_numpy() >= 1) & (df["a0"].to_numpy() >= 1)),
    }
    for line in LINES:
        out[f"over_{line}"] = tot <= int(np.floor(line))
    return out


def strata_masks(df: pd.DataFrame) -> dict[str, NDArray[np.bool_]]:
    margin = (df["h0"] - df["a0"]).abs().to_numpy()
    return {
        "level": margin == 0,
        "one_goal": margin == 1,
        "two_plus": margin >= 2,
        "after_dismissal": (df["red_h"] + df["red_a"]).to_numpy() > 0,
    }


# ----------------------------------------------------------------------------- scoring


def row_loss(market: str, p: Any, y: Any) -> FloatArray:
    if market == "1x2":
        return M.log_loss_multiclass(p, y)
    return M.log_loss_binary(np.clip(p, 1e-6, 1 - 1e-6), y)


def row_brier(market: str, p: Any, y: Any) -> FloatArray:
    if market == "1x2":
        return M.brier_multiclass(p, y)
    return M.brier_binary(np.clip(p, 1e-6, 1 - 1e-6), y)


def compare(
    df: pd.DataFrame,
    model: dict[str, FloatArray],
    base: dict[str, FloatArray],
    y: dict[str, Any],
    mask: NDArray[np.bool_],
    market: str,
    seed: int,
) -> dict[str, Any] | None:
    m = mask & undecided(df)[market]
    if m.sum() < 30:
        return None
    pm, pb, yy = model[market][m], base[market][m], y[market][m]
    ll_m, ll_b = row_loss(market, pm, yy), row_loss(market, pb, yy)
    cl = df["sb_match_id"].to_numpy()[m]
    rec: dict[str, Any] = {
        "n": int(m.sum()),
        "model_log_loss": float(ll_m.mean()),
        "baseline_log_loss": float(ll_b.mean()),
        "model_brier": float(row_brier(market, pm, yy).mean()),
        "baseline_brier": float(row_brier(market, pb, yy).mean()),
        "log_loss_diff_model_minus_baseline": cluster_diff_summary(ll_m - ll_b, cl, seed),
    }
    return rec


def bh(recs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    keys = [k for k, v in recs.items() if v]
    q, keep = benjamini_hochberg(
        [recs[k]["log_loss_diff_model_minus_baseline"]["p"] for k in keys], 0.10
    )
    for k, qq, kk in zip(keys, q, keep, strict=True):
        recs[k]["q_bh"], recs[k]["survives_bh_10pct"] = qq, bool(kk)
        recs[k]["verdict"] = (
            (
                "model better"
                if recs[k]["log_loss_diff_model_minus_baseline"]["estimate"] < 0
                else "model worse"
            )
            if kk
            else "not distinguishable"
        )
    return {
        "n_tests": len(keys),
        "fdr": 0.10,
        "model_better": [
            k
            for k in keys
            if recs[k]["survives_bh_10pct"]
            and recs[k]["log_loss_diff_model_minus_baseline"]["estimate"] < 0
        ],
        "model_worse": [
            k
            for k in keys
            if recs[k]["survives_bh_10pct"]
            and recs[k]["log_loss_diff_model_minus_baseline"]["estimate"] > 0
        ],
    }


def price_all(p: Params, df: pd.DataFrame, **kw: Any) -> dict[str, FloatArray]:
    return price(
        p,
        df["lam_h"].to_numpy(float),
        df["lam_a"].to_numpy(float),
        df["h0"].to_numpy(np.int64),
        df["a0"].to_numpy(np.int64),
        df["red_h"].to_numpy(np.int64),
        df["red_a"].to_numpy(np.int64),
        df["t_s"].to_numpy(float),
        **kw,
    )


# ----------------------------------------------------------------------------- runner


def run_phase7(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    seed = int(cfg["seed"])
    d = load_data(cfg)
    guard = load_guard()
    ids = d["ids"]
    fit_ids = ids["burn"] + ids["tune"]
    guard.check_tuning(fit_ids, "in-play model fit (matchweeks 1-19)")
    mm = d["matches"]
    lengths_all = mm[mm["sb_match_id"].isin(fit_ids)]["match_length_s"].to_numpy(float)
    l_bar = float(lengths_all.mean())
    seg_burn = build_segments(mm[mm["sb_match_id"].isin(ids["burn"])], d["events"], l_bar)
    seg_tune = build_segments(mm[mm["sb_match_id"].isin(ids["tune"])], d["events"], l_bar)
    seg_fit = pd.concat([seg_burn, seg_tune], ignore_index=True)
    len_burn = mm[mm["sb_match_id"].isin(ids["burn"])]["match_length_s"].to_numpy(float)

    def score(kappa: float) -> float:
        return -held_out_loglik(fit_params(seg_burn, kappa, len_burn), seg_tune)

    tuned = tune_interior(
        "inplay_kappa", score, KAPPA_GRID, lower_bound=0.0, widen_high=lambda x: x * 10.0
    )
    kappa = require_tuned(tuned)
    params = fit_params(seg_fit, kappa, lengths_all)
    variants = {
        "time_profile_only": fit_params(
            seg_fit, kappa, lengths_all, use_state=False, use_dismissal=False
        ),
        "time_profile_plus_score_state": fit_params(
            seg_fit, kappa, lengths_all, use_state=True, use_dismissal=False
        ),
        "full": params,
    }
    # ---- test window, evaluated once
    df = proposition_frame(d, ids["test"])
    model = price_all(params, df)
    base = baseline_prices(df, d["base_lam"], d["mean_len"])
    y = truth(df)
    st = strata_masks(df)
    cp = df["checkpoint"].to_numpy()
    fam1 = {
        f"{c}|{mk}": compare(df, model, base, y, cp == c, mk, seed)
        for c in CHECKPOINTS_MIN
        for mk in MARKETS
    }
    f1 = bh({k: v for k, v in fam1.items() if v})
    fam2 = {
        f"{s}|{mk}": compare(df, model, base, y, st[s], mk, seed) for s in STRATA for mk in MARKETS
    }
    f2 = bh({k: v for k, v in fam2.items() if v})
    fam3 = {
        f"{c}|{s}|1x2": compare(df, model, base, y, (cp == c) & st[s], "1x2", seed)
        for c in CHECKPOINTS_MIN
        for s in STRATA
    }
    f3 = bh({k: v for k, v in fam3.items() if v})
    # ---- calibration: predicted vs observed remaining goals and 1X2 reliability
    rem: dict[str, Any] = {}
    for c in CHECKPOINTS_MIN:
        for s in ("all", *STRATA):
            m = (cp == c) & (np.ones(len(df), bool) if s == "all" else st[s])
            if m.sum() >= 20:
                rem[f"{c}|{s}"] = {
                    "n": int(m.sum()),
                    "observed_remaining_goals": float(y["remaining"][m].mean()),
                    "model_predicted": float(model["exp_remaining"][m].mean()),
                    "baseline_predicted": float(base["exp_remaining"][m].mean()),
                }
    pooled = {
        "observed": float(y["remaining"].mean()),
        "model": float(model["exp_remaining"].mean()),
        "baseline": float(base["exp_remaining"].mean()),
    }
    ece = {
        mk: {
            "model": binary_metrics(
                np.clip(model[mk][undecided(df)[mk]], 1e-6, 1 - 1e-6), y[mk][undecided(df)[mk]]
            )["ece"],
            "baseline": binary_metrics(
                np.clip(base[mk][undecided(df)[mk]], 1e-6, 1 - 1e-6), y[mk][undecided(df)[mk]]
            )["ece"],
        }
        for mk in MARKETS
        if mk != "1x2"
    }
    ece["1x2"] = {
        "model": class_metrics(model["1x2"], y["1x2"])["ece"],
        "baseline": class_metrics(base["1x2"], y["1x2"])["ece"],
    }
    for c in (15, 45, 75):
        m = cp == c
        reliability_plot(
            {
                "model": M.multiclass_reliability(model["1x2"][m], y["1x2"][m]),
                "naive baseline": M.multiclass_reliability(base["1x2"][m], y["1x2"][m]),
            },
            FIGURES_DIR / f"phase7_reliability_1x2_min{c}.png",
            f"In-play final 1X2, elapsed minute {c}",
            f"test mw20-38, n={int(m.sum())}",
        )
    # ---- ablations (descriptive)
    abl: dict[str, Any] = {}
    for name, v in variants.items():
        kw = {"use_state": name != "time_profile_only", "use_dismissal": name == "full"}
        pv = price_all(v, df, **kw)
        abl[name] = {
            str(c): {
                mk: float(
                    row_loss(
                        mk,
                        pv[mk][(cp == c) & undecided(df)[mk]],
                        y[mk][(cp == c) & undecided(df)[mk]],
                    ).mean()
                )
                for mk in ("1x2", "over_2.5", "btts")
            }
            for c in CHECKPOINTS_MIN
        }
    # ---- showcase
    show = showcase(d, params)
    prov = provenance(CMD, cfg, warehouse_version(cfg))
    out = {
        "provenance": prov,
        "kappa_tuning": tuned.as_record(),
        "params": params.as_record(),
        "n_test_matches": int(df["sb_match_id"].nunique()),
        "n_propositions": int(len(df)),
        "family_I_checkpoint_x_market": {"tests": fam1, "bh": f1},
        "family_II_stratum_x_market": {"tests": fam2, "bh": f2},
        "family_III_checkpoint_x_stratum_1x2": {"tests": fam3, "bh": f3},
        "remaining_goals_calibration": {"by_checkpoint_stratum": rem, "pooled": pooled},
        "ece_pooled": ece,
        "ablations_log_loss": abl,
        "showcase": show["meta"],
    }
    write_json(resolve_path(cfg, "metrics_dir") / "phase7_inplay.json", out)
    recs = []
    for kv, sc in tuned.history:
        recs.append(
            make_record(
                f"phase7::kappa::{kv:g}",
                cfg,
                prov["data_version"],
                CMD,
                feature_set="exposure segments mw1-19",
                model="inplay_poisson_penalised",
                hyperparameters={"kappa": kv},
                validation_window="player_2015_16: tune mw10-19 (fit mw1-9)",
                metrics={"held_out_neg_loglik_per_segment": sc},
                calibration={},
                notes="D-056 kappa grid (D-040)",
                status="promoted" if kv == kappa else "rejected",
            )
        )
    for name in variants:
        recs.append(
            make_record(
                f"phase7::variant::{name}",
                cfg,
                prov["data_version"],
                CMD,
                feature_set="time profile"
                + (
                    ""
                    if name == "time_profile_only"
                    else " + score state" + (" + dismissals" if name == "full" else "")
                ),
                model="inplay_poisson_penalised",
                hyperparameters={"kappa": kappa},
                validation_window="player_2015_16: test mw20-38",
                metrics=abl[name],
                calibration={},
                notes="ablation (descriptive)",
                status="promoted" if name == "full" else "rejected",
            )
        )
    for k, fv in fam1.items():
        if fv:
            recs.append(
                make_record(
                    f"phase7::vs_baseline::{k}",
                    cfg,
                    prov["data_version"],
                    CMD,
                    feature_set="in-play state at checkpoint",
                    model="inplay_full",
                    hyperparameters={"kappa": kappa},
                    validation_window="player_2015_16: test mw20-38",
                    metrics={
                        "log_loss_diff": fv["log_loss_diff_model_minus_baseline"],
                        "q_bh": fv["q_bh"],
                        "n": fv["n"],
                    },
                    calibration={},
                    notes="D-056 family I vs Phase 2 naive baseline",
                    status="promoted" if fv["verdict"] == "model better" else "rejected",
                )
            )
    append_records(recs)
    return resolve_path(cfg, "metrics_dir") / "phase7_inplay.json"


def showcase(d: dict[str, Any], params: Params) -> dict[str, Any]:
    """Price path through the pre-registered showcase match (D-056): the test match with the most
    goal and dismissal events among those with at least one dismissal and three goals."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ev = d["events"][d["events"]["sb_match_id"].isin(d["ids"]["test"])]
    goals = ev[ev["kind"].isin(["goal", "own_goal"])].groupby("sb_match_id").size()
    reds = ev[ev["kind"].isin(["red_card", "second_yellow"])].groupby("sb_match_id").size()
    cand = pd.DataFrame({"goals": goals, "reds": reds}).fillna(0)
    cand = cand[(cand["goals"] >= 3) & (cand["reds"] >= 1)]
    cand["n"] = cand["goals"] + cand["reds"]
    mid = int(
        cand.rename_axis("sb_match_id")
        .reset_index()
        .sort_values(["n", "sb_match_id"], ascending=[False, True])
        .iloc[0]["sb_match_id"]
    )
    m = d["matches"][d["matches"]["sb_match_id"] == mid].iloc[0]
    length = float(m["match_length_s"])
    ts = np.arange(60.0, length, 60.0)
    one = d["matches"][d["matches"]["sb_match_id"] == mid]
    rows = []
    for t in ts:
        s = states(one, d["events"], float(t)).iloc[0]
        rows.append(
            {
                "t_s": t,
                "h0": int(s["h0"]),
                "a0": int(s["a0"]),
                "red_h": int(s["red_h"]),
                "red_a": int(s["red_a"]),
                "lam_h": float(m["lam_h"]),
                "lam_a": float(m["lam_a"]),
            }
        )
    fr = pd.DataFrame(rows)
    pr = price_all(params, fr)
    e = ev[ev["sb_match_id"] == mid]
    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    for k, lab in enumerate(("home win", "draw", "away win")):
        axes[0].plot(ts / 60, pr["1x2"][:, k], label=lab)
    axes[1].plot(ts / 60, pr["over_2.5"], color="k", label="over 2.5 goals")
    for _, r in e.iterrows():
        colour = "tab:green" if r["kind"] in ("goal", "own_goal") else "tab:red"
        for ax in axes:
            ax.axvline(r["elapsed_s"] / 60, color=colour, linestyle="--", linewidth=1, alpha=0.8)
    axes[0].set_ylabel("probability")
    axes[0].legend(fontsize=7)
    axes[1].set_ylabel("probability")
    axes[1].set_xlabel("elapsed minute (play-time clock)")
    axes[1].legend(fontsize=7)
    axes[0].set_title(f"Price path, test match {mid} (green: goal, red: dismissal)", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "phase7_price_path_showcase.png", dpi=110)
    plt.close(fig)
    events = [
        {
            "elapsed_s": int(r["elapsed_s"]),
            "kind": str(r["kind"]),
            "team": "home" if r["team"] == m["home"] else "away",
        }
        for _, r in e.sort_values("elapsed_s").iterrows()
    ]
    return {
        "meta": {
            "sb_match_id": mid,
            "events": events,
            "final_score": [int(m["home_score"]), int(m["away_score"])],
        }
    }
