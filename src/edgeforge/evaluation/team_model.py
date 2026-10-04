"""Phase 3: Dixon-Coles team model (decay tuned on tuning windows only), comparison A,
post-hoc calibration decisions, the promoted-team cold-start fix, and persistence.

Blocks (D-033): pillar_a tunes on 2019/20-2023/24 and is tested on 2024/25 (primary) and 2025/26
(secondary); team_2015_16 tunes on 2013/14-2014/15 and is tested on matchweeks 20-38 of 2015/16
(the whole 2015/16 season is also predicted and persisted for Phases 4-7).
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from edgeforge.config import PROJECT_ROOT, load_config, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.baselines_team import (
    _league_probs,
    outcomes,
    poisson_lambdas,
)
from edgeforge.evaluation.calibration import calibrate_1x2, calibrate_binary, platt_parameters
from edgeforge.evaluation.common import (
    binary_metrics,
    class_metrics,
    connect,
    load_fd_clock,
    warehouse_version,
    write_json,
)
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.segments import add_segments, load_promoted
from edgeforge.evaluation.splits import SplitGuard, load_guard, load_split_ids
from edgeforge.features.pit import register_clocks
from edgeforge.models.dixon_coles import RIDGE, dc_grid, fit_dixon_coles
from edgeforge.pricing.markets import OU_LINES, market_probs

log = logging.getLogger(__name__)
WINDOW_DAYS = 1825  # five years of history, then exponential decay on top
HALF_LIVES: tuple[int | None, ...] = (90, 180, 270, 365, 540, 730, None)  # days; None = no decay
KAPPAS = (0.0, 2.0, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0)  # widened after a grid-edge optimum
MIN_TRAIN = 150
N_BOOT = 1000
MARKETS = ["1x2"] + [f"over_{x}" for x in OU_LINES] + ["btts"]
MODELS_DIR = PROJECT_ROOT / "artifacts" / "models"


def xi_of(half_life: int | None) -> float:
    return 0.0 if half_life is None else float(np.log(2.0) / half_life)


@dataclass
class LeagueData:
    df: pd.DataFrame
    names: list[str]
    h: np.ndarray
    a: np.ndarray
    hg: np.ndarray
    ag: np.ndarray
    ko_late: np.ndarray
    done: np.ndarray


def league_data(fd_all: pd.DataFrame) -> dict[str, LeagueData]:
    out = {}
    for lg, g in fd_all.groupby("league"):
        g = g.sort_values("ko_late").reset_index(drop=True)
        names = sorted(set(g["home"]) | set(g["away"]))
        idx = {t: i for i, t in enumerate(names)}
        out[str(lg)] = LeagueData(
            df=g,
            names=names,
            h=g["home"].map(idx).to_numpy(),
            a=g["away"].map(idx).to_numpy(),
            hg=g["fthg"].to_numpy(np.int64),
            ag=g["ftag"].to_numpy(np.int64),
            ko_late=g["ko_late"].to_numpy("datetime64[ns]"),
            done=g["done_ts"].to_numpy("datetime64[ns]"),
        )
    return out


class PromotedPrior:
    """Average first-season rating of promoted teams, from seasons completed by each cutoff."""

    def __init__(self, ld: dict[str, LeagueData], promoted: pd.DataFrame) -> None:
        self.entries: list[tuple[np.datetime64, list[float], list[float]]] = []
        prom = promoted[promoted["promoted"].fillna(False).astype(bool)]
        for lg, L in ld.items():
            for season, g in L.df.groupby("season"):
                if int(g["season_start_year"].iloc[0]) < 2005 or len(g) < 100:
                    continue
                teams = sorted(set(g["home"]) | set(g["away"]))
                idx = {t: i for i, t in enumerate(teams)}
                m = fit_dixon_coles(
                    g["home"].map(idx).to_numpy(),
                    g["away"].map(idx).to_numpy(),
                    g["fthg"].to_numpy(np.int64),
                    g["ftag"].to_numpy(np.int64),
                    np.zeros(len(g)),
                    teams,
                    xi=0.0,
                    fit_rho=False,
                )
                pt = prom[(prom["league"] == lg) & (prom["season"] == season)]["team"]
                a = [float(m.attack[idx[t]]) for t in pt if t in idx]
                d = [float(m.defence[idx[t]]) for t in pt if t in idx]
                if a:
                    self.entries.append((g["done_ts"].max().to_datetime64(), a, d))

    def at(self, cutoff: pd.Timestamp) -> dict[str, tuple[float, float]] | None:
        a, d = [], []
        for end, aa, dd in self.entries:
            if end <= cutoff.to_datetime64():
                a += aa
                d += dd
        if len(a) < 10:
            return None
        return {"__mean__": (float(np.mean(a)), float(np.mean(d)))}


def walk_forward(
    ld: dict[str, LeagueData],
    ev: pd.DataFrame,
    half_life: int | None,
    guard: SplitGuard,
    promoted: pd.DataFrame | None = None,
    prior: PromotedPrior | None = None,
    kappa: float = 0.0,
    params: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Weekly-refit Dixon-Coles predictions for every match in `ev` (needs model_cutoff, season)."""
    xi = xi_of(half_life)
    prom_set: dict[tuple[str, str], set[str]] = {}
    if promoted is not None:
        pr = promoted[promoted["promoted"].fillna(False).astype(bool)]
        for (lg, s), g in pr.groupby(["league", "season"]):
            prom_set[(str(lg), str(s))] = set(g["team"])
    rows = []
    groups = ev.groupby(["league", "model_cutoff", "season"])
    for (league, cutoff, season), g in groups:
        L = ld[str(league)]
        ct = pd.Timestamp(str(cutoff))
        c64 = ct.to_datetime64()
        mask = (L.done <= c64) & (L.ko_late >= c64 - np.timedelta64(WINDOW_DAYS, "D"))
        if mask.sum() < MIN_TRAIN:
            continue
        guard.check_fit_before([pd.Timestamp(L.done[mask].max())], ct, f"dc {league} {cutoff}")
        local = sorted(
            {L.names[i] for i in L.h[mask]}
            | {L.names[i] for i in L.a[mask]}
            | set(g["home"])
            | set(g["away"])
        )
        lut = np.full(len(L.names), -1)
        for t in local:
            if t in L.names:
                lut[L.names.index(t)] = local.index(t)
        age = (c64 - L.ko_late[mask]) / np.timedelta64(1, "D")
        pm: dict[str, tuple[float, float]] | None = None
        if kappa > 0 and prior is not None:
            base = prior.at(ct)
            if base is not None:
                proms = prom_set.get((str(league), str(season)), set())
                pm = {t: base["__mean__"] for t in local if t in proms}
        m = fit_dixon_coles(
            lut[L.h[mask]],
            lut[L.a[mask]],
            L.hg[mask],
            L.ag[mask],
            age.astype(float),
            local,
            xi,
            prior_mean=pm,
            prior_strength=kappa,
        )
        lam_h, lam_a = m.lambdas(g["home"], g["away"])
        rows.append(
            pd.DataFrame(
                {"match_id": g["match_id"].to_numpy(), "lam_h": lam_h, "lam_a": lam_a, "rho": m.rho}
            )
        )
        if params is not None:
            for t, i in m.teams.items():
                params.append(
                    {
                        "league": league,
                        "cutoff": ct,
                        "team": t,
                        "attack": m.attack[i],
                        "defence": m.defence[i],
                        "intercept": m.intercept,
                        "home_adv": m.home_adv,
                        "rho": m.rho,
                        "xi": xi,
                        "n_matches": m.n_matches,
                        "promoted_prior": bool(pm and t in pm),
                    }
                )
    return pd.concat(rows, ignore_index=True)


def dc_probs(
    pred: pd.DataFrame, ev: pd.DataFrame, cols: tuple[str, str, str] = ("lam_h", "lam_a", "rho")
) -> dict[str, np.ndarray]:
    m = ev[["match_id"]].merge(pred, on="match_id", how="left")
    ok = m[cols[0]].notna().to_numpy()
    grid = dc_grid(
        m[cols[0]].fillna(1.0).to_numpy(),
        m[cols[1]].fillna(1.0).to_numpy(),
        m[cols[2]].fillna(0.0).to_numpy(),
    )
    out = {}
    for k, v in market_probs(grid).items():
        v = v.astype(float).copy()
        v[~ok] = np.nan
        out[k] = v
    return out


def static_probs(
    fd_all: pd.DataFrame, ev: pd.DataFrame, guard: SplitGuard
) -> dict[str, np.ndarray]:
    from edgeforge.evaluation.baselines_team import _all_probs_from_lambda

    return _all_probs_from_lambda(poisson_lambdas(fd_all, ev, guard), ev)


def score(
    probs: dict[str, np.ndarray], y: dict[str, Any], mask: np.ndarray | None = None
) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for mk in MARKETS:
        p = probs[mk]
        m = ~np.isnan(p).any(axis=1) if p.ndim == 2 else ~np.isnan(p)
        if mask is not None:
            m = m & mask
        if m.any():
            res[mk] = (
                class_metrics(p[m], y["1x2"][m]) if mk == "1x2" else binary_metrics(p[m], y[mk][m])
            )
    return res


def loss_vec(p: np.ndarray, y: dict[str, Any], mk: str, kind: str = "log_loss") -> np.ndarray:
    if mk == "1x2":
        return M.rps(p, y["1x2"]) if kind == "rps" else M.log_loss_multiclass(p, y["1x2"])
    return M.log_loss_binary(p, y[mk])


def compare(
    a: dict[str, np.ndarray], b: dict[str, np.ndarray], y: dict[str, Any], seed: int
) -> list[dict[str, Any]]:
    out = []
    for mk in MARKETS:
        for kind in ("log_loss", "rps") if mk == "1x2" else ("log_loss",):
            pa, pb = a[mk], b[mk]
            ok = ~(np.isnan(pa).any(axis=1) if pa.ndim == 2 else np.isnan(pa))
            ok &= ~(np.isnan(pb).any(axis=1) if pb.ndim == 2 else np.isnan(pb))
            if ok.sum() < 30:
                continue
            la = loss_vec(
                pa[ok], {k: (v[ok] if hasattr(v, "__len__") else v) for k, v in y.items()}, mk, kind
            )
            lb = loss_vec(
                pb[ok], {k: (v[ok] if hasattr(v, "__len__") else v) for k, v in y.items()}, mk, kind
            )
            out.append(
                {
                    "market": mk,
                    "loss": kind,
                    **M.paired_bootstrap(la, lb, np.flatnonzero(ok), N_BOOT, seed),
                }
            )
    return out


def ev_frame(fd_all: pd.DataFrame, ids: list[str]) -> pd.DataFrame:
    return fd_all[fd_all["match_id"].isin(set(ids))].sort_values("match_id").reset_index(drop=True)


def tune_half_life(
    ld: dict[str, LeagueData], ev: pd.DataFrame, guard: SplitGuard, label: str
) -> tuple[int | None, list[dict[str, Any]], dict[int | None, pd.DataFrame]]:
    y = outcomes(ev)
    guard.check_tuning(ev["match_id"].tolist(), f"decay tuning {label}")
    grid, preds = [], {}
    for hl in HALF_LIVES:
        pred = walk_forward(ld, ev, hl, guard)
        probs = dc_probs(pred, ev)
        sc = score(probs, y)
        preds[hl] = pred
        grid.append(
            {
                "half_life_days": hl,
                "n": sc["1x2"]["n"],
                "log_loss_1x2": sc["1x2"]["log_loss"],
                "rps_1x2": sc["1x2"]["rps"],
                "metrics": sc,
            }
        )
        log.info(
            "%s half-life %s: 1X2 log loss %.5f (n=%d)",
            label,
            hl,
            sc["1x2"]["log_loss"],
            sc["1x2"]["n"],
        )
    best = min(grid, key=lambda g: g["log_loss_1x2"])
    return best["half_life_days"], grid, preds


def tune_kappa(
    ld: dict[str, LeagueData],
    ev: pd.DataFrame,
    half_life: int | None,
    guard: SplitGuard,
    promoted: pd.DataFrame,
    prior: PromotedPrior,
    fd_all: pd.DataFrame,
    label: str,
) -> tuple[float, list[dict[str, Any]]]:
    seg = add_segments(ev, fd_all, promoted)
    mask = seg["promoted_first10"].to_numpy()
    y = outcomes(ev)
    grid = []
    for k in KAPPAS:
        pred = walk_forward(ld, ev, half_life, guard, promoted, prior, k)
        probs = dc_probs(pred, ev)["1x2"]
        ok = mask & ~np.isnan(probs).any(axis=1)
        ll = float(M.log_loss_multiclass(probs[ok], y["1x2"][ok]).mean())
        ll_all = float(
            M.log_loss_multiclass(
                probs[~np.isnan(probs).any(axis=1)], y["1x2"][~np.isnan(probs).any(axis=1)]
            ).mean()
        )
        grid.append(
            {
                "kappa": k,
                "log_loss_promoted_first10": ll,
                "n_promoted_first10": int(ok.sum()),
                "log_loss_all": ll_all,
            }
        )
        log.info(
            "%s kappa %s: promoted-first-10 log loss %.5f (n=%d), all %.5f",
            label,
            k,
            ll,
            ok.sum(),
            ll_all,
        )
    best = min(grid, key=lambda g: g["log_loss_promoted_first10"])
    return float(best["kappa"]), grid


def calibration_study(
    tune_probs: dict[str, np.ndarray],
    tune_y: dict[str, Any],
    test_probs: dict[str, np.ndarray],
    test_y: dict[str, Any],
    seed: int,
) -> list[dict[str, Any]]:
    out = []
    for mk in MARKETS:
        pt, pe = tune_probs[mk], test_probs[mk]
        okt = ~(np.isnan(pt).any(axis=1) if pt.ndim == 2 else np.isnan(pt))
        oke = ~(np.isnan(pe).any(axis=1) if pe.ndim == 2 else np.isnan(pe))
        for method in ("platt", "isotonic"):
            if mk == "1x2":
                cal = np.full_like(pe, np.nan)
                cal[oke] = calibrate_1x2(pt[okt], tune_y["1x2"][okt], pe[oke], method)
                la = M.log_loss_multiclass(cal[oke], test_y["1x2"][oke])
                lb = M.log_loss_multiclass(pe[oke], test_y["1x2"][oke])
            else:
                cal = np.full_like(pe, np.nan)
                cal[oke] = calibrate_binary(pt[okt], tune_y[mk][okt], pe[oke], method)
                la = M.log_loss_binary(cal[oke], test_y[mk][oke])
                lb = M.log_loss_binary(pe[oke], test_y[mk][oke])
            b = M.paired_bootstrap(la, lb, np.flatnonzero(oke), N_BOOT, seed)
            out.append(
                {
                    "market": mk,
                    "method": method,
                    "n_test": int(oke.sum()),
                    "raw_log_loss": float(lb.mean()),
                    "calibrated_log_loss": float(la.mean()),
                    **b,
                    "keep": bool(b["mean_diff"] < 0 and b["ci_high"] < 0),
                }
            )
    return out


def run_team_model(cfg: dict[str, Any] | None = None) -> Path:
    from edgeforge.provenance import provenance

    cfg = cfg or load_config("data")
    con = connect(cfg)
    guard = load_guard()
    fd_all = load_fd_clock(con)
    register_clocks(con, fd_all, None)
    promoted = load_promoted(con)
    ld = league_data(fd_all)
    prior = PromotedPrior(ld, promoted)
    log.info("promoted-prior seasons available: %d", len(prior.entries))
    a_ids = load_split_ids("pillar_a")
    t_ids = load_split_ids("team_2015_16")
    seed = int(cfg["seed"])
    version = warehouse_version(cfg)
    result: dict[str, Any] = {}
    persist: list[pd.DataFrame] = []
    params: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []

    blocks: dict[str, dict[str, Any]] = {
        "pillar_a": {
            "tune": a_ids["burn_in_tune_2019_23"],
            "primary": "primary_2024_25",
            "tests": {
                "primary_2024_25": a_ids["test_primary_2024_25"],
                "secondary_2025_26": a_ids["test_secondary_2025_26"],
            },
        },
        "team_2015_16": {
            "tune": t_ids["decay_tuning_2013_14"],
            "primary": "mw20_38",
            "tests": {
                "mw20_38": t_ids["test_2015_16_mw20_38"],
                "all_2015_16": t_ids["eval_2015_16_all"],
            },
        },
    }
    for bname, spec in blocks.items():
        log.info("=== block %s", bname)
        ev_tune = ev_frame(fd_all, spec["tune"])
        y_tune = outcomes(ev_tune)
        best_hl, grid, preds = tune_half_life(ld, ev_tune, guard, bname)
        # reference models on the tuning window
        static_t = static_probs(fd_all, ev_tune, guard)
        league_t = _league_probs(con, ev_tune)
        ref = {"static_poisson": score(static_t, y_tune), "league_avg": score(league_t, y_tune)}
        dc_tune_probs = dc_probs(preds[best_hl], ev_tune)
        kappa, kgrid = tune_kappa(ld, ev_tune, best_hl, guard, promoted, prior, fd_all, bname)
        fix_helps_tune = kappa > 0
        block_res: dict[str, Any] = {
            "tuning_window_matches": len(ev_tune),
            "decay_grid": [{k: v for k, v in g.items() if k != "metrics"} for g in grid],
            "chosen_half_life_days": best_hl,
            "tuning_window_reference": {k: v["1x2"] for k, v in ref.items()},
            "tuning_window_dc_chosen": score(dc_tune_probs, y_tune),
            "kappa_grid": kgrid,
            "chosen_kappa": kappa,
            "tests": {},
        }
        for g in grid:
            chosen = g["half_life_days"] == best_hl
            beats = g["log_loss_1x2"] < ref["static_poisson"]["1x2"]["log_loss"]
            records.append(
                make_record(
                    f"team_model::{bname}::dc_hl{g['half_life_days']}",
                    cfg,
                    version,
                    "edgeforge team-model run",
                    feature_set="football-data results, weekly Monday refit, opening state",
                    model="dixon_coles_decay",
                    hyperparameters={
                        "half_life_days": g["half_life_days"],
                        "window_days": WINDOW_DAYS,
                        "ridge": RIDGE,
                    },
                    validation_window=f"{bname} tuning window",
                    metrics=g["metrics"],
                    calibration={"ece_1x2": g["metrics"]["1x2"]["ece"]},
                    notes=(
                        "Chosen: lowest tuning-window 1X2 log loss." if chosen else "Not chosen."
                    )
                    + f" Static Poisson tuning 1X2 log loss {ref['static_poisson']['1x2']['log_loss']:.5f}.",
                    status="promoted" if (chosen and beats) else "rejected",
                )
            )
        for k in kgrid:
            records.append(
                make_record(
                    f"team_model::{bname}::coldstart_kappa{k['kappa']}",
                    cfg,
                    version,
                    "edgeforge team-model run",
                    feature_set="football-data results; promoted-team prior toward historical first-season mean",
                    model="dixon_coles_decay+promoted_prior",
                    hyperparameters={"half_life_days": best_hl, "kappa": k["kappa"]},
                    validation_window=f"{bname} tuning window, promoted teams' first 10 matches",
                    metrics=k,
                    calibration={},
                    notes="kappa 0 = no fix.",
                    status="promoted" if (k["kappa"] == kappa and kappa > 0) else "rejected",
                )
            )
        tune_cal_probs = dc_tune_probs
        for tname, ids in spec["tests"].items():
            ev = ev_frame(fd_all, ids)
            y = outcomes(ev)
            is_persist = tname in ("all_2015_16", "primary_2024_25", "secondary_2025_26")
            plist: list[dict[str, Any]] = []
            pred = walk_forward(ld, ev, best_hl, guard, params=plist if is_persist else None)
            params += [dict(r, block=bname, test=tname) for r in plist]
            pred_fix = (
                walk_forward(ld, ev, best_hl, guard, promoted, prior, kappa) if kappa > 0 else pred
            )
            models = {
                "league_avg": _league_probs(con, ev),
                "static_poisson": static_probs(fd_all, ev, guard),
                "dixon_coles": dc_probs(pred, ev),
                "dixon_coles_coldstart_fix": dc_probs(pred_fix, ev),
            }
            seg = add_segments(ev, fd_all, promoted)
            tres: dict[str, Any] = {
                "n_matches": len(ev),
                "per_model": {m: score(p, y) for m, p in models.items()},
                "comparison_A": {
                    "dc_vs_static_poisson": compare(
                        models["dixon_coles"], models["static_poisson"], y, seed
                    ),
                    "dc_vs_league_avg": compare(
                        models["dixon_coles"], models["league_avg"], y, seed
                    ),
                    "static_poisson_vs_league_avg": compare(
                        models["static_poisson"], models["league_avg"], y, seed
                    ),
                },
                "calibration": calibration_study(
                    tune_cal_probs, y_tune, models["dixon_coles"], y, seed
                ),
            }
            tres["calibration_kept"] = [
                f"{c['market']}:{c['method']}" for c in tres["calibration"] if c["keep"]
            ]
            fm = seg["promoted_first10"].to_numpy()
            ok = fm & ~np.isnan(models["dixon_coles"]["1x2"]).any(axis=1)
            if ok.sum() > 20:
                la = M.log_loss_multiclass(
                    models["dixon_coles_coldstart_fix"]["1x2"][ok], y["1x2"][ok]
                )
                lb = M.log_loss_multiclass(models["dixon_coles"]["1x2"][ok], y["1x2"][ok])
                tres["coldstart_fix_vs_none_promoted_first10"] = {
                    "n": int(ok.sum()),
                    "kappa": kappa,
                    **M.paired_bootstrap(la, lb, np.flatnonzero(ok), N_BOOT, seed),
                }
            block_res["tests"][tname] = tres
            if tname == spec["primary"]:
                adopted = [c for c in tres["calibration"] if c["keep"]]
                platt = {}
                for c in adopted:
                    if c["method"] == "platt":
                        pt = dc_tune_probs[c["market"]]
                        okt = ~(np.isnan(pt).any(axis=1) if pt.ndim == 2 else np.isnan(pt))
                        if c["market"] != "1x2":
                            platt[c["market"]] = platt_parameters(pt[okt], y_tune[c["market"]][okt])
                block_res["calibration_decision"] = {
                    "rule": "adopt a calibrator for a market only if, on the block's primary test window, "
                    "held-out log loss improves with a paired-bootstrap CI that excludes zero; other windows "
                    "are supporting checks. 56 market x method x window tests were run, so isolated hits "
                    "are expected by chance and the verdicts differ across windows.",
                    "primary_window": tname,
                    "adopted": [f"{c['market']}:{c['method']}" for c in adopted],
                    "platt_parameters_fitted_on_tuning_window": platt,
                    "1x2_calibration_helps": any(
                        c["keep"] for c in tres["calibration"] if c["market"] == "1x2"
                    ),
                }
            for mname in ("dixon_coles", "dixon_coles_coldstart_fix"):
                if mname == "dixon_coles_coldstart_fix" and kappa == 0:
                    continue
                records.append(
                    make_record(
                        f"team_model::{bname}::{tname}::{mname}",
                        cfg,
                        version,
                        "edgeforge team-model run",
                        feature_set="football-data results, weekly Monday refit, opening state",
                        model=mname,
                        hyperparameters={
                            "half_life_days": best_hl,
                            "kappa": kappa if "fix" in mname else 0.0,
                            "window_days": WINDOW_DAYS,
                        },
                        validation_window=f"{bname} {tname}",
                        metrics=tres["per_model"][mname],
                        calibration={"ece_1x2": tres["per_model"][mname]["1x2"]["ece"]},
                        notes="Held-out test window; hyper-parameters chosen on the tuning window.",
                        status="promoted"
                        if mname == "dixon_coles"
                        else ("promoted" if fix_helps_tune else "rejected"),
                    )
                )
            for c in tres["calibration"]:
                records.append(
                    make_record(
                        f"team_model::{bname}::{tname}::calib_{c['market']}_{c['method']}",
                        cfg,
                        version,
                        "edgeforge team-model run",
                        feature_set="Dixon-Coles probabilities",
                        model=f"{c['method']}_calibration",
                        hyperparameters={
                            "market": c["market"],
                            "fitted_on": f"{bname} tuning window",
                        },
                        validation_window=f"{bname} {tname}",
                        metrics={
                            k: c[k]
                            for k in (
                                "raw_log_loss",
                                "calibrated_log_loss",
                                "mean_diff",
                                "ci_low",
                                "ci_high",
                            )
                        },
                        calibration={},
                        notes="Kept only if held-out log loss improves with CI excluding zero.",
                        status="promoted" if c["keep"] else "rejected",
                    )
                )
            if is_persist:
                p = pred.rename(columns={"lam_h": "lam_h", "lam_a": "lam_a"})
                pf = pred_fix.rename(
                    columns={"lam_h": "lam_h_fix", "lam_a": "lam_a_fix", "rho": "rho_fix"}
                )
                persist.append(
                    p.merge(pf, on="match_id", how="left").assign(block=bname, test=tname)
                )
        # tuning-window predictions persisted too (needed for model-vs-market tuning-window fits)
        persist.append(preds[best_hl].assign(block=bname, test="tune"))
        result[bname] = block_res

    con.close()
    payload = {
        "provenance": provenance("edgeforge team-model run", cfg, version),
        "window_days": WINDOW_DAYS,
        "ridge": RIDGE,
        "n_boot": N_BOOT,
        "blocks": result,
    }
    metrics_dir = resolve_path(cfg, "metrics_dir")
    write_json(metrics_dir / "team_model.json", payload)
    proc = resolve_path(cfg, "processed_dir")
    pd.concat(persist, ignore_index=True).to_parquet(proc / "team_model_preds.parquet", index=False)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    pdf = pd.DataFrame(params)
    pdf.to_parquet(MODELS_DIR / "team_dc_params.parquet", index=False)
    n = append_records(records)
    log.info("registry: %d new records; %d parameter rows persisted", n, len(pdf))
    return metrics_dir / "team_model.json"
