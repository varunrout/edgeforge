"""D-063 B1: a stronger in-play baseline (time profile + score state, naive-baseline intensities,
no dismissal terms) and the new family IV: Pillar B model against B1.

Pre-registered in D-063 before any result existed. Fitting uses matchweeks 1-19 only with kappa
fixed at the committed value; nothing is tuned and no test-window quantity enters a choice.
"""

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation.common import warehouse_version, write_json
from edgeforge.evaluation.inplay_model import (
    CHECKPOINTS_MIN,
    Params,
    build_segments,
    fit_params,
    price,
)
from edgeforge.evaluation.phase7_run import (
    MARKETS,
    STRATA,
    baseline_prices,
    bh,
    compare,
    load_data,
    price_all,
    proposition_frame,
    row_loss,
    strata_masks,
    truth,
    undecided,
)
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.splits import load_guard
from edgeforge.provenance import provenance

log = logging.getLogger(__name__)
CMD = "edgeforge phase7 b1"
WINDOW = "player_2015_16: test mw20-38"


def _same_as_committed(p: Params, committed: dict[str, Any]) -> bool:
    ref = committed["params"]
    return bool(
        np.allclose(np.exp(p.beta), ref["beta_per_second_multiplier"], rtol=1e-6)
        and np.allclose(
            np.exp(p.gamma),
            [
                ref["gamma_multiplier_by_state"][k]
                for k in ("trail2", "trail1", "level", "lead1", "lead2")
            ],
            rtol=1e-6,
        )
        and np.isclose(np.exp(p.delta_own), ref["own_dismissal_multiplier"], rtol=1e-6)
        and np.isclose(np.exp(p.delta_opp), ref["opponent_dismissal_multiplier"], rtol=1e-6)
    )


def run_b1(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    seed = int(cfg["seed"])
    metrics_dir = resolve_path(cfg, "metrics_dir")
    committed = json.loads((metrics_dir / "phase7_inplay.json").read_text(encoding="utf-8"))
    kappa = float(committed["kappa_tuning"]["value"])
    d = load_data(cfg)
    guard = load_guard()
    ids = d["ids"]
    fit_ids = ids["burn"] + ids["tune"]
    guard.check_tuning(fit_ids, "B1 in-play baseline fit (matchweeks 1-19)")
    mm = d["matches"]
    lengths_all = mm[mm["sb_match_id"].isin(fit_ids)]["match_length_s"].to_numpy(float)
    l_bar = float(lengths_all.mean())
    # ---- B1: naive-baseline intensities in place of the Dixon-Coles ones
    naive_lam = d["base_lam"].drop_duplicates("sb_match_id")[["sb_match_id", "lam_h", "lam_a"]]
    fit_m = mm[mm["sb_match_id"].isin(fit_ids)].drop(columns=["lam_h", "lam_a"])
    fit_b1 = fit_m.merge(naive_lam, on="sb_match_id")
    n_excluded = int(len(fit_m) - len(fit_b1))
    seg_b1 = build_segments(fit_b1, d["events"], l_bar)
    b1 = fit_params(seg_b1, kappa, lengths_all, use_state=True, use_dismissal=False)
    # ---- Pillar B model refit with the committed kappa (must equal the committed parameters)
    seg_full = build_segments(mm[mm["sb_match_id"].isin(fit_ids)], d["events"], l_bar)
    full = fit_params(seg_full, kappa, lengths_all)
    reproduced = _same_as_committed(full, committed)
    if not reproduced:
        raise RuntimeError("Pillar B refit does not reproduce the committed parameters")
    # ---- test window, evaluated once
    df = proposition_frame(d, ids["test"])
    nl = naive_lam.set_index("sb_match_id")
    df_b1 = df.copy()
    df_b1["lam_h"] = df["sb_match_id"].map(nl["lam_h"])
    df_b1["lam_a"] = df["sb_match_id"].map(nl["lam_a"])
    n_missing_test = int(df_b1["lam_h"].isna().sum())
    keep = df_b1["lam_h"].notna().to_numpy()
    df, df_b1 = df[keep].reset_index(drop=True), df_b1[keep].reset_index(drop=True)
    model = price_all(full, df)
    b1_prices = price(
        b1,
        df_b1["lam_h"].to_numpy(float),
        df_b1["lam_a"].to_numpy(float),
        df_b1["h0"].to_numpy(np.int64),
        df_b1["a0"].to_numpy(np.int64),
        df_b1["red_h"].to_numpy(np.int64),
        df_b1["red_a"].to_numpy(np.int64),
        df_b1["t_s"].to_numpy(float),
        use_state=True,
        use_dismissal=False,
    )
    naive = baseline_prices(df, d["base_lam"], d["mean_len"])
    y = truth(df)
    cp = df["checkpoint"].to_numpy()
    fam4 = {
        f"{c}|{mk}": compare(df, model, b1_prices, y, cp == c, mk, seed)
        for c in CHECKPOINTS_MIN
        for mk in MARKETS
    }
    f4 = bh({k: v for k, v in fam4.items() if v})
    desc_b1_vs_naive = {
        f"{c}|{mk}": compare(df, b1_prices, naive, y, cp == c, mk, seed)
        for c in CHECKPOINTS_MIN
        for mk in MARKETS
    }
    und = undecided(df)
    pooled: dict[str, Any] = {}
    for mk in MARKETS:
        m = und[mk]
        pooled[mk] = {
            "n": int(m.sum()),
            "pillar_b": float(row_loss(mk, model[mk][m], y[mk][m]).mean()),
            "b1": float(row_loss(mk, b1_prices[mk][m], y[mk][m]).mean()),
            "naive": float(row_loss(mk, naive[mk][m], y[mk][m]).mean()),
        }
    st = strata_masks(df)
    strata_desc = {
        s: {
            mk: {
                "n": int((st[s] & und[mk]).sum()),
                "pillar_b": float(
                    row_loss(mk, model[mk][st[s] & und[mk]], y[mk][st[s] & und[mk]]).mean()
                ),
                "b1": float(
                    row_loss(mk, b1_prices[mk][st[s] & und[mk]], y[mk][st[s] & und[mk]]).mean()
                ),
                "naive": float(
                    row_loss(mk, naive[mk][st[s] & und[mk]], y[mk][st[s] & und[mk]]).mean()
                ),
            }
            for mk in ("1x2", "over_2.5", "btts")
            if (st[s] & und[mk]).sum() >= 30
        }
        for s in STRATA
    }
    n_cells = len([v for v in fam4.values() if v])
    summary = {
        "n_cells": n_cells,
        "pillar_b_better_than_b1": len(f4["model_better"]),
        "pillar_b_worse_than_b1": len(f4["model_worse"]),
        "not_distinguishable": n_cells - len(f4["model_better"]) - len(f4["model_worse"]),
        "pillar_b_better_than_naive_committed": len(
            committed["family_I_checkpoint_x_market"]["bh"]["model_better"]
        ),
        "pillar_b_worse_than_naive_committed": len(
            committed["family_I_checkpoint_x_market"]["bh"]["model_worse"]
        ),
    }
    prov = provenance(CMD, cfg, warehouse_version(cfg))
    out = {
        "provenance": prov,
        "decision": "D-063 (B1 and family IV)",
        "kappa_fixed_at": kappa,
        "b1_params": b1.as_record(),
        "pillar_b_refit_matches_committed_params": reproduced,
        "n_fit_matches_excluded_no_naive_lambda": n_excluded,
        "n_test_propositions_without_naive_lambda": n_missing_test,
        "n_test_matches": int(df["sb_match_id"].nunique()),
        "family_IV_pillar_b_vs_b1": {"tests": fam4, "bh": f4},
        "summary": summary,
        "descriptive_b1_vs_naive_by_cell": desc_b1_vs_naive,
        "pooled_log_loss": pooled,
        "by_stratum_log_loss": strata_desc,
        "remaining_goals_pooled": {
            "observed": float(y["remaining"].mean()),
            "pillar_b": float(model["exp_remaining"].mean()),
            "b1": float(b1_prices["exp_remaining"].mean()),
            "naive": float(naive["exp_remaining"].mean()),
        },
    }
    write_json(metrics_dir / "phase7_b1.json", out)
    recs = [
        make_record(
            "phase7::b1::fit",
            cfg,
            prov["data_version"],
            CMD,
            feature_set="naive-baseline intensities x time bins x score state (no dismissals)",
            model="inplay_b1_poisson_penalised",
            hyperparameters={"kappa": kappa},
            validation_window="player_2015_16: fit mw1-19, test mw20-38 (kappa fixed, not tuned)",
            metrics={"pooled_log_loss": pooled, "summary": summary},
            calibration={},
            notes="D-063 B1 baseline",
            status="baseline",
        )
    ]
    for k, fv in fam4.items():
        if fv:
            recs.append(
                make_record(
                    f"phase7::familyIV::{k}",
                    cfg,
                    prov["data_version"],
                    CMD,
                    feature_set="in-play state at checkpoint",
                    model="inplay_full_vs_b1",
                    hyperparameters={"kappa": kappa},
                    validation_window=WINDOW,
                    metrics={
                        "log_loss_diff": fv["log_loss_diff_model_minus_baseline"],
                        "q_bh": fv["q_bh"],
                        "n": fv["n"],
                    },
                    calibration={},
                    notes="D-063 family IV: Pillar B model vs B1 (negative favours Pillar B)",
                    status="promoted" if fv["verdict"] == "model better" else "rejected",
                )
            )
    append_records(recs)
    log.info("B1: %s", summary)
    return metrics_dir / "phase7_b1.json"


__all__ = ["run_b1", "pd"]
