"""In-play naive baseline (Pillar B reference, D-017/D-033).

Pre-match Poisson intensities (the static team baseline) are scaled linearly by the share of the
match left, ignoring score state and dismissals. The final result is the current score plus the
remaining goals. State at elapsed second t uses only events strictly before t. The mean match
length used for scaling is fitted on matchweeks 1-19 only; evaluation is the matchweek 20-38 test
window at minutes 15/30/45/60/75 of elapsed match time.
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.stats import poisson

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.evaluation import plots
from edgeforge.evaluation.common import (
    FIGURES_DIR,
    binary_metrics,
    class_metrics,
    connect,
    warehouse_version,
    write_json,
)
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.splits import load_guard, load_split_ids
from edgeforge.features.pit import inplay_state
from edgeforge.pricing.markets import score_grid

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]
CHECKPOINTS = (15, 30, 45, 60, 75)
REMAINING_LINES = (0.5, 1.5, 2.5)


def final_probs(
    lam_h: FloatArray, lam_a: FloatArray, h0: NDArray[np.int64], a0: NDArray[np.int64]
) -> dict[str, FloatArray]:
    """Final 1X2, remaining-goals overs and final BTTS from current score + Poisson remainder."""
    grid = score_grid(lam_h, lam_a)
    g = grid.shape[1]
    x = np.arange(g)[:, None]
    y = np.arange(g)[None, :]
    diff = x - y
    n = len(lam_h)
    p3 = np.empty((n, 3))
    for i in range(n):
        d = a0[i] - h0[i]
        p3[i, 0] = grid[i][diff > d].sum()
        p3[i, 1] = grid[i][diff == d].sum()
        p3[i, 2] = grid[i][diff < d].sum()
    out: dict[str, FloatArray] = {"1x2": p3}
    lam_t = lam_h + lam_a
    for line in REMAINING_LINES:
        out[f"rem_over_{line}"] = poisson.sf(np.floor(line), lam_t)
    ph = 1.0 - np.exp(-lam_h)  # home scores at least once more
    pa = 1.0 - np.exp(-lam_a)
    out["btts"] = np.where(
        (h0 >= 1) & (a0 >= 1), 1.0, np.where(h0 >= 1, pa, np.where(a0 >= 1, ph, ph * pa))
    )
    return out


def run_inplay_baseline(cfg: dict[str, Any] | None = None) -> Path:
    from edgeforge.provenance import provenance

    cfg = cfg or load_config("data")
    con = connect(cfg)
    guard = load_guard()
    splits = load_split_ids("player_2015_16")
    fit_ids = splits["burn_in_mw1_9"] + splits["tune_mw10_19"]
    test_ids = splits["test_mw20_38"]
    guard.check_tuning(fit_ids, "in-play mean match length")
    con.register("fit_ids", pd.DataFrame({"id": fit_ids}))
    mean_len = float(
        con.execute(
            "SELECT avg(match_length_s) FROM sb_matches "
            "WHERE sb_match_id IN (SELECT id FROM fit_ids)"
        )
        .df()
        .iloc[0, 0]  # type: ignore[arg-type]
    )
    proc = resolve_path(cfg, "processed_dir")
    lam = pd.read_parquet(proc / "baseline_lambdas_team_2015_16.parquet")
    con.register("lam_tbl", lam)
    con.register("test_ids", pd.DataFrame({"id": test_ids}))
    base = con.execute(
        """
        SELECT m.sb_match_id, m.home_score, m.away_score, m.match_length_s, l.lam_h, l.lam_a
        FROM sb_matches m JOIN sb_match_link k USING (sb_match_id)
        JOIN lam_tbl l ON l.match_id = k.fd_match_id
        WHERE m.sb_match_id IN (SELECT id FROM test_ids)
        """
    ).df()
    log.info("in-play test matches with pre-match intensities: %d of %d", len(base), len(test_ids))
    rows = []
    for m in CHECKPOINTS:
        t = m * 60
        for r in base.itertuples():
            st = inplay_state(con, int(str(r.sb_match_id)), t)
            rows.append(
                {
                    "checkpoint": m,
                    "sb_match_id": r.sb_match_id,
                    "h0": st["home_goals"],
                    "a0": st["away_goals"],
                    "dismissals": st["home_dismissals"] + st["away_dismissals"],
                    "final_h": r.home_score,
                    "final_a": r.away_score,
                    "frac_left": max(0.0, (mean_len - t) / mean_len),
                    "lam_h": r.lam_h,
                    "lam_a": r.lam_a,
                }
            )
    d = pd.DataFrame(rows)
    probs = final_probs(
        (d["lam_h"] * d["frac_left"]).to_numpy(float),
        (d["lam_a"] * d["frac_left"]).to_numpy(float),
        d["h0"].to_numpy(np.int64),
        d["a0"].to_numpy(np.int64),
    )
    fin_diff = d["final_h"] - d["final_a"]
    y1 = np.where(fin_diff > 0, 0, np.where(fin_diff == 0, 1, 2)).astype(np.int64)
    rem = (d["final_h"] + d["final_a"] - d["h0"] - d["a0"]).to_numpy()
    ys: dict[str, Any] = {
        "1x2": y1,
        "btts": ((d["final_h"] > 0) & (d["final_a"] > 0)).to_numpy(float),
    }
    for line in REMAINING_LINES:
        ys[f"rem_over_{line}"] = (rem > line).astype(float)
    margin = (d["h0"] - d["a0"]).abs()
    d["score_state"] = np.where(margin == 0, "level", np.where(margin == 1, "one_goal", "two_plus"))
    d["dismissal_state"] = np.where(d["dismissals"] > 0, "after_dismissal", "no_dismissal")

    def block(mask: NDArray[np.bool_]) -> dict[str, Any]:
        res: dict[str, Any] = {"n": int(mask.sum())}
        if not mask.any():
            return res
        res["1x2"] = class_metrics(probs["1x2"][mask], y1[mask])
        for k in [f"rem_over_{x}" for x in REMAINING_LINES] + ["btts"]:
            res[k] = binary_metrics(probs[k][mask], ys[k][mask])
        return res

    by_cp = {str(m): block((d["checkpoint"] == m).to_numpy()) for m in CHECKPOINTS}
    by_score = {
        s: block((d["score_state"] == s).to_numpy()) for s in ("level", "one_goal", "two_plus")
    }
    by_dis = {
        s: block((d["dismissal_state"] == s).to_numpy())
        for s in ("no_dismissal", "after_dismissal")
    }
    by_cp_state = {
        f"{m}_{s}": block(((d["checkpoint"] == m) & (d["score_state"] == s)).to_numpy())
        for m in CHECKPOINTS
        for s in ("level", "one_goal", "two_plus")
    }
    curves = {
        f"min {m}": M.multiclass_reliability(
            probs["1x2"][(d["checkpoint"] == m).to_numpy()], y1[(d["checkpoint"] == m).to_numpy()]
        )
        for m in (15, 45, 75)
    }
    plots.reliability_plot(
        curves,
        FIGURES_DIR / "reliability_inplay_naive_1x2.png",
        "In-play naive baseline, final 1X2",
        f"test mw20-38, matches: {len(base)}",
    )
    plots.line_plot(
        {
            "1X2 log loss": [(m, by_cp[str(m)]["1x2"]["log_loss"]) for m in CHECKPOINTS],
            "remaining >1.5 log loss": [
                (m, by_cp[str(m)]["rem_over_1.5"]["log_loss"]) for m in CHECKPOINTS
            ],
        },
        FIGURES_DIR / "inplay_naive_logloss_by_checkpoint.png",
        "In-play naive baseline by checkpoint",
        "elapsed minute",
        "log loss",
    )
    version = warehouse_version(cfg)
    payload = {
        "provenance": provenance("edgeforge baselines inplay", cfg, version),
        "mean_match_length_s_fitted_mw1_19": mean_len,
        "checkpoints_elapsed_minutes": list(CHECKPOINTS),
        "n_test_matches": len(base),
        "by_checkpoint": by_cp,
        "by_score_state_pooled": by_score,
        "by_dismissal_state_pooled": by_dis,
        "by_checkpoint_and_score_state": by_cp_state,
        "note": "Intensities ignore score and dismissals; final result = current score + remaining "
        "Poisson goals. Elapsed minutes use real match time (stoppage included).",
    }
    out = resolve_path(cfg, "metrics_dir") / "baseline_inplay.json"
    write_json(out, payload)
    rec = make_record(
        "baseline::inplay::naive_linear",
        cfg,
        version,
        "edgeforge baselines inplay",
        feature_set="pre-match static Poisson intensities x remaining time share; score at t",
        model="naive_time_scaled_poisson",
        hyperparameters={"checkpoints": list(CHECKPOINTS), "mean_match_length_s": mean_len},
        validation_window="StatsBomb 2015/16 test window matchweeks 20-38",
        metrics={"by_checkpoint": by_cp, "by_dismissal_state": by_dis},
        calibration={str(m): by_cp[str(m)]["1x2"]["ece"] for m in CHECKPOINTS},
        notes="Ignores score-state and dismissal effects by construction.",
        status="baseline",
    )
    append_records([rec])
    con.close()
    return out
