"""D-044: post-hoc level recalibration of bench P(appear) and starter P(exit before the end).

D-043 rule: the calibrator is fitted on matchweeks 10-14, chosen on matchweeks 15-19 (paired
match-cluster bootstrap, CI must exclude zero) and only then applied. The test window is
evaluated once, for both the raw and the recalibrated probabilities, and reported either way.
Recalibration rescales the pmf over event bins so its shape is kept and the event level moves.
"""

import logging
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.calibration import BinaryCalibrator
from edgeforge.evaluation.common import binary_metrics
from edgeforge.evaluation.phase4 import cluster_diff_summary
from edgeforge.models.participation import N_BINS

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]
FIT_WEEKS = (10, 14)
CHOOSE_WEEKS = (15, 19)


def rescale_pmf(pmf: FloatArray, p_event_new: FloatArray) -> FloatArray:
    """Move the total event probability to `p_event_new`, keeping the shape over bins."""
    old = np.clip(1.0 - pmf[:, -1], 1e-9, None)
    new = np.clip(p_event_new, 1e-6, 1 - 1e-6)
    out = pmf.copy()
    out[:, :-1] = pmf[:, :-1] * (new / old)[:, None]
    out[:, -1] = 1.0 - new
    return out


def _one(
    name: str, rows: pd.DataFrame, pmf: pd.DataFrame, seed: int
) -> tuple[BinaryCalibrator | None, dict[str, Any]]:
    """Choose inside the tuning window, evaluate on test; rows carry split, match_week, ev."""
    p = 1.0 - pmf.loc[rows.index, N_BINS].to_numpy(float)
    y = (rows["ev"].to_numpy() < N_BINS).astype(float)
    wk = rows["match_week"].to_numpy()
    tune = (rows["split"] == "tune").to_numpy()
    fit_m = tune & (wk >= FIT_WEEKS[0]) & (wk <= FIT_WEEKS[1])
    cho_m = tune & (wk >= CHOOSE_WEEKS[0]) & (wk <= CHOOSE_WEEKS[1])
    cal_fc = BinaryCalibrator("platt").fit(p[fit_m], y[fit_m])
    pc = cal_fc.predict(p[cho_m])
    diff = M.log_loss_binary(pc, y[cho_m]) - M.log_loss_binary(p[cho_m], y[cho_m])
    cl = rows["sb_match_id"].to_numpy()
    choice = cluster_diff_summary(diff, cl[cho_m], seed)
    adopt = bool(choice["estimate"] < 0 and choice["ci_high"] < 0)
    final = BinaryCalibrator("platt").fit(p[tune], y[tune])
    te = (rows["split"] == "test").to_numpy()
    pt = final.predict(p[te])
    dtest = M.log_loss_binary(pt, y[te]) - M.log_loss_binary(p[te], y[te])
    rep: dict[str, Any] = {
        "name": name,
        "rule": "D-043: fit mw10-14, choose mw15-19 (CI excludes zero), final fit on mw10-19",
        "n_fit": int(fit_m.sum()),
        "n_choose": int(cho_m.sum()),
        "choose_window_raw": binary_metrics(p[cho_m], y[cho_m]),
        "choose_window_recalibrated": binary_metrics(pc, y[cho_m]),
        "choose_window_log_loss_diff_recal_minus_raw": choice,
        "adopted": adopt,
        "test_raw": binary_metrics(p[te], y[te]),
        "test_recalibrated": binary_metrics(pt, y[te]),
        "test_log_loss_diff_recal_minus_raw": cluster_diff_summary(dtest, cl[te], seed),
        "test_reliability_raw": M.reliability_bins(p[te], y[te]),
        "test_reliability_recalibrated": M.reliability_bins(pt, y[te]),
    }
    return (final if adopt else None), rep


def recalibrate_participation(
    starters: pd.DataFrame,
    bench: pd.DataFrame,
    mp: dict[str, pd.DataFrame],
    seed: int,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Return participation predictions with adopted recalibrations applied, and the report."""
    keep = ["sb_match_id", "split", "match_week", "ev"]
    rs = starters.loc[starters.index.intersection(mp["sq"].index), keep]
    rb = bench.loc[bench.index.intersection(mp["sq"].index), keep]
    cal_s, rep_s = _one("starter_exit_level", rs, mp["sq"], seed)
    cal_b, rep_b = _one("bench_appearance_level", rb, mp["sq"], seed)
    out = {k: v.copy() for k, v in mp.items()}

    def apply(df: pd.DataFrame, idx: pd.Index, cal: BinaryCalibrator | None) -> None:
        if cal is None or len(idx) == 0:
            return
        arr = df.loc[idx].to_numpy(float)
        df.loc[idx] = rescale_pmf(arr, cal.predict(1.0 - arr[:, -1]))

    apply(out["sq"], rs.index, cal_s)
    apply(out["sq"], rb.index, cal_b)
    for key, cal in (("cs", cal_s), ("cb", cal_b)):
        apply(out[key], out[key].index, cal)
    log.info("D-044 adopted: starter %s bench %s", rep_s["adopted"], rep_b["adopted"])
    return out, {"starter_exit_level": rep_s, "bench_appearance_level": rep_b}
