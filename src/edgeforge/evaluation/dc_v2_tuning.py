"""D-065: tuning of the single hyperparameter omega of `dc_opening_v2` on matches strictly before
2024/25 (seasons 2022/23 and 2023/24, five leagues, walk-forward with 28-day refits).

The code asserts that no match on or after 1 July 2024 is read, so 2024/25, 2025/26 and every
shadow fixture are out of reach by construction.
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.common import connect, warehouse_version, write_json
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.tuning import require_tuned, tune_interior
from edgeforge.models.dixon_coles import dc_grid
from edgeforge.models.dixon_coles_shots import fit_dixon_coles_shots
from edgeforge.pricing.markets import market_probs
from edgeforge.provenance import provenance

log = logging.getLogger(__name__)
CMD = "edgeforge shadow tune-v2"
LIMIT = pd.Timestamp("2024-07-01")
LEAGUES = ("E0", "D1", "SP1", "I1", "F1")
TUNE_SEASONS = (2022, 2023)
HALF_LIFE_DAYS = 365
WINDOW_DAYS = 1825
STEP_DAYS = 28
OMEGA_GRID = [0.0, 0.1, 0.25, 0.5, 1.0, 2.0]
MIN_TRAIN = 150


def load_history(cfg: dict[str, Any]) -> pd.DataFrame:
    con = connect(cfg)
    df = con.execute(
        "SELECT league, season_start_year, date, home, away, fthg, ftag, hst, ast FROM matches "
        "WHERE fthg IS NOT NULL AND ftag IS NOT NULL AND league IN ('E0','D1','SP1','I1','F1')"
    ).df()
    con.close()
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] < LIMIT].reset_index(drop=True)
    if len(df) and df["date"].max() >= LIMIT:
        raise RuntimeError("a match on or after 2024-07-01 was read")
    return df


def fit_predict_block(
    train: pd.DataFrame, target: pd.DataFrame, cutoff: pd.Timestamp, omega: float
) -> np.ndarray | None:
    if len(train) < MIN_TRAIN:
        return None
    names = sorted(
        set(train["home"]) | set(train["away"]) | set(target["home"]) | set(target["away"])
    )
    idx = {t: i for i, t in enumerate(names)}
    age = (cutoff - train["date"]).dt.total_seconds().to_numpy(float) / 86400.0
    m = fit_dixon_coles_shots(
        train["home"].map(idx).to_numpy(),
        train["away"].map(idx).to_numpy(),
        train["fthg"].to_numpy(np.int64),
        train["ftag"].to_numpy(np.int64),
        train["hst"].to_numpy(float),
        train["ast"].to_numpy(float),
        age,
        names,
        float(np.log(2.0) / HALF_LIFE_DAYS),
        omega,
    )
    lam_h, lam_a = m.lambdas(target["home"], target["away"])
    p = market_probs(dc_grid(np.asarray(lam_h), np.asarray(lam_a), m.rho))["1x2"]
    return np.asarray(p)


def walk_forward(df: pd.DataFrame, omega: float) -> dict[str, Any]:
    ll_all: list[np.ndarray] = []
    per_league: dict[str, list[np.ndarray]] = {}
    n_fits = 0
    for league in LEAGUES:
        lg = df[df["league"] == league]
        for season in TUNE_SEASONS:
            tgt_season = lg[lg["season_start_year"] == season]
            if tgt_season.empty:
                continue
            start, end = tgt_season["date"].min(), tgt_season["date"].max()
            cutoff = start
            while cutoff <= end:
                target = tgt_season[
                    (tgt_season["date"] >= cutoff)
                    & (tgt_season["date"] < cutoff + pd.Timedelta(days=STEP_DAYS))
                ]
                if len(target):
                    train = lg[
                        (lg["date"] < cutoff)
                        & (lg["date"] >= cutoff - pd.Timedelta(days=WINDOW_DAYS))
                    ]
                    p = fit_predict_block(train, target, cutoff, omega)
                    n_fits += 1
                    if p is not None:
                        y = np.where(
                            target["fthg"] > target["ftag"],
                            0,
                            np.where(target["fthg"] == target["ftag"], 1, 2),
                        ).astype(np.int64)
                        ll = M.log_loss_multiclass(p, y)
                        ll_all.append(ll)
                        per_league.setdefault(league, []).append(ll)
                cutoff = cutoff + pd.Timedelta(days=STEP_DAYS)
    allv = np.concatenate(ll_all)
    return {
        "omega": omega,
        "n_predictions": int(len(allv)),
        "n_fits": n_fits,
        "log_loss_1x2": float(allv.mean()),
        "by_league": {k: float(np.concatenate(v).mean()) for k, v in per_league.items()},
    }


def run_tune(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    df = load_history(cfg)
    results: dict[float, dict[str, Any]] = {}

    def score(omega: float) -> float:
        r = walk_forward(df, omega)
        results[omega] = r
        return float(r["log_loss_1x2"])

    tuned = tune_interior(
        "dc_v2_omega", score, OMEGA_GRID, lower_bound=0.0, widen_high=lambda x: x * 2.0
    )
    omega = require_tuned(tuned)
    prov = provenance(CMD, cfg, warehouse_version(cfg))
    base = results[0.0]["log_loss_1x2"]
    out = {
        "provenance": prov,
        "decision": "D-065",
        "data_rule": "matches strictly before 2024-07-01; seasons 2022/23 and 2023/24 predicted",
        "max_date_read": str(df["date"].max().date()),
        "step_days": STEP_DAYS,
        "window_days": WINDOW_DAYS,
        "half_life_days": HALF_LIFE_DAYS,
        "omega_tuning": tuned.as_record(),
        "omega_star": omega,
        "results_by_omega": {f"{k:g}": v for k, v in sorted(results.items())},
        "log_loss_at_omega_star": results[omega]["log_loss_1x2"],
        "log_loss_goals_only": base,
        "improvement_vs_goals_only": base - results[omega]["log_loss_1x2"],
        "rule": "omega* = 0 means no v2 is run (D-065)",
        "v2_runs": bool(omega > 0.0),
    }
    path = resolve_path(cfg, "metrics_dir") / "dc_v2_tuning.json"
    write_json(path, out)
    recs = []
    for k, v in sorted(results.items()):
        recs.append(
            make_record(
                f"dc_v2::omega::{k:g}",
                cfg,
                prov["data_version"],
                CMD,
                feature_set="goals + shots on target (shared strengths), football-data columns",
                model="dc_opening_v2",
                hyperparameters={"omega": k, "half_life_days": HALF_LIFE_DAYS},
                validation_window="walk-forward 28-day refits, 2022/23 and 2023/24, five leagues",
                metrics={"log_loss_1x2": v["log_loss_1x2"], "n": v["n_predictions"]},
                calibration={},
                notes="D-065 tuning grid (D-040); only data before 2024/25",
                status="promoted"
                if k == omega and omega > 0
                else "baseline"
                if k == 0
                else "rejected",
            )
        )
    append_records(recs)
    log.info("omega* = %s (goals-only %.5f -> %.5f)", omega, base, results[omega]["log_loss_1x2"])
    return path
