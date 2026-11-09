"""D-063 B2: a Gaussian-copula baseline for the SGA joint probability.

Same legs and marginals as the naive product (the simulator's standalone leg probabilities), one
equicorrelation per template estimated on the tuning window (matchweeks 10-19) only, compared with
the simulated joint and the naive product on the Phase 5 test instances. Pre-registered in D-063
before any result existed.
"""

import logging
import pickle
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.special import ndtr, ndtri

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.common import binary_metrics, write_json
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
    fit_shots_given_goals,
    load_context,
    realised_players,
)
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.splits import load_guard
from edgeforge.evaluation.stats import benjamini_hochberg
from edgeforge.evaluation.tuning import require_tuned, tune_interior
from edgeforge.provenance import provenance
from edgeforge.sga.engine import (
    SGAError,
    indicator_matrix,
    leg_ok_matrix,
    price_from_indicators,
    validate_sga,
)
from edgeforge.simulation.engine import ShotsGivenGoals, simulate

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]
CMD = "edgeforge phase5 copula"
STATES = ("lineups", "opening")
MAX_LEGS = 5
MIN_TUNE_INSTANCES = 30
RHO_TOP = 0.95
_GH_X, _GH_W = np.polynomial.hermite_e.hermegauss(80)
_GL_X, _GL_W = np.polynomial.legendre.leggauss(64)


# ----------------------------------------------------------------------------- copula maths


def _copula_nonneg(z: FloatArray, rho: float) -> FloatArray:
    """P(Z_j <= z_j for all j), equicorrelation rho >= 0, by one-dimensional Gauss-Hermite."""
    if rho <= 0.0:
        return np.asarray(np.prod(ndtr(z), axis=1))
    a = (z[:, :, None] - np.sqrt(rho) * _GH_X[None, None, :]) / np.sqrt(1.0 - rho)
    return np.asarray((np.prod(ndtr(a), axis=1) * _GH_W[None, :]).sum(axis=1) / np.sqrt(2 * np.pi))


def _bivariate(a: FloatArray, b: FloatArray, rho: float) -> FloatArray:
    """Phi2(a, b; rho) = Phi(a) Phi(b) + integral_0^rho phi2(a, b; r) dr (any rho in (-1, 1))."""
    r = rho / 2.0 * (_GL_X + 1.0)
    w = rho / 2.0 * _GL_W
    aa, bb, rr = a[:, None], b[:, None], r[None, :]
    dens = np.exp(-(aa**2 - 2 * rr * aa * bb + bb**2) / (2 * (1 - rr**2))) / (
        2 * np.pi * np.sqrt(1 - rr**2)
    )
    return np.asarray(ndtr(a) * ndtr(b) + (dens * w[None, :]).sum(axis=1))


def copula_prob(p: FloatArray, rho: float, n_sims: int) -> FloatArray:
    """Gaussian-copula joint probability from marginals p [n, k] (clipped as in Phase 5)."""
    floor = 1.0 / (2.0 * n_sims)
    z = ndtri(np.clip(p, floor, 1.0 - floor))
    if rho < 0.0:
        if z.shape[1] != 2:
            raise ValueError("negative equicorrelation is only supported for two legs")
        out = _bivariate(z[:, 0], z[:, 1], rho)
    else:
        out = _copula_nonneg(z, rho)
    return np.clip(out, floor, 1.0 - floor)


def rho_grid(k: int) -> list[float]:
    lo = -RHO_TOP if k == 2 else 0.0
    return [float(x) for x in np.round(np.arange(lo, RHO_TOP + 1e-9, 0.05), 2)]


# ----------------------------------------------------------------------------- simulation


def _model(ctx: Phase5Context) -> ShotsGivenGoals:
    train = fit_shots_given_goals(ctx.d, ctx.shots)
    load_guard().check_tuning(
        train["sb_match_id"].unique().tolist(), "conditional team-shot model (tune window only)"
    )
    return ShotsGivenGoals.fit(train)


def collect_block(
    ctx: Phase5Context,
    model: ShotsGivenGoals,
    state: str,
    split: str,
    n_sims: int,
    seed: int,
    cache_dir: Path,
) -> pd.DataFrame:
    path = cache_dir / f"phase5_copula_{split}_{state}.pkl"
    if path.exists():
        with path.open("rb") as fh:
            cached: pd.DataFrame = pickle.load(fh)
        return cached
    inputs, rows, prices = build_inputs(ctx, state, split)
    s_unc = prices["_p_appear"] * prices["anytime_scorer"]
    realised = realised_players(rows)
    scores = {
        int(m): (int(h), int(a))
        for m, h, a in zip(
            ctx.scores["sb_match_id"],
            ctx.scores["home_score"],
            ctx.scores["away_score"],
            strict=True,
        )
    }
    recs: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for k, (m, inp) in enumerate(sorted(inputs.items())):
        if m not in scores:
            continue
        res = simulate(inp, model, n_sims, seed)
        idx = inp.row_index
        rank = rank_players(inp, s_unc[idx])
        team_map = {int(p): int(t) for p, t in zip(inp.player_id, inp.team, strict=True)}
        for tpl, tag, sga in template_instances(rank):
            try:
                validate_sga(sga, team_map)
            except SGAError:
                continue
            ind, ok = indicator_matrix(sga, res)
            pr = price_from_indicators(ind, ok, leg_ok_matrix(sga, res))
            legs = list(pr["leg_probabilities"])  # type: ignore[call-overload]
            row: dict[str, Any] = {
                "sb_match_id": m,
                "state": state,
                "split": split,
                "template": tpl,
                "side": tag,
                "n_legs": TEMPLATE_META[tpl][0],
                "p_sim_raw": pr["joint"],
                "p_naive_raw": pr["naive_product"],
                "n_eff": pr["n_effective_sims"],
                "y": evaluate_instance_outcome(sga, scores[m], realised.get(m, {})),
            }
            for j in range(MAX_LEGS):
                row[f"leg{j + 1}"] = legs[j] if j < len(legs) else np.nan
            recs.append(row)
        if (k + 1) % 50 == 0:
            log.info(
                "%s %s: %d/%d matches, %.0fs", split, state, k + 1, len(inputs),
                time.perf_counter() - t0,
            )  # fmt: skip
    df = pd.DataFrame(recs)
    with path.open("wb") as fh:
        pickle.dump(df, fh)
    log.info("%s %s: %d instances in %.0fs", split, state, len(df), time.perf_counter() - t0)
    return df


# ----------------------------------------------------------------------------- estimation


def _legs(df: pd.DataFrame, k: int) -> FloatArray:
    return df[[f"leg{j + 1}" for j in range(k)]].to_numpy(float)


def estimate_rho(tune: pd.DataFrame, n_sims: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tpl, (k, _rel) in TEMPLATE_META.items():
        sub = tune[(tune["template"] == tpl) & tune["y"].notna()]
        p = _legs(sub, k)
        ok = np.isfinite(p).all(axis=1)
        sub, p = sub[ok], p[ok]
        if len(sub) < MIN_TUNE_INSTANCES:
            out[tpl] = {
                "rho": 0.0,
                "n_tune_instances": int(len(sub)),
                "flag": "fewer than 30: rho = 0",
            }
            continue
        y = sub["y"].to_numpy(float)

        def score(rho: float, p: FloatArray = p, y: FloatArray = y) -> float:
            q = copula_prob(p, rho, n_sims)
            return float(M.log_loss_binary(q, y).mean())

        grid = rho_grid(k)
        tuned = tune_interior(
            f"copula_rho_{tpl}",
            score,
            grid,
            lower_bound=grid[0],
            upper_bound=RHO_TOP,
            widen_low=lambda x: x,
            widen_high=lambda x: x,
        )
        out[tpl] = {
            "rho": require_tuned(tuned),
            "n_tune_instances": int(len(sub)),
            "log_loss_tune_at_rho": score(tuned.value),
            "log_loss_tune_at_zero": score(0.0),
            "tuning": tuned.as_record(),
            "flag": "at a grid bound" if tuned.at_natural_bound else "interior",
        }
    return out


# ----------------------------------------------------------------------------- evaluation


def _paired_family(
    inst: pd.DataFrame, a: str, b: str, seed: int, name: str, label_a: str, label_b: str
) -> dict[str, Any]:
    out: dict[str, Any] = {"by_template_state": {}}
    keys: list[str] = []
    ps: list[float] = []
    for state in STATES:
        for tpl in TEMPLATE_META:
            sub = inst[(inst["state"] == state) & (inst["template"] == tpl) & inst["y"].notna()]
            key = f"{tpl}|{state}"
            if len(sub) < 30:
                out["by_template_state"][key] = {"n": int(len(sub)), "note": "too few instances"}
                continue
            y = sub["y"].to_numpy(float)
            pa, pb = sub[a].to_numpy(float), sub[b].to_numpy(float)
            s = cluster_diff_summary(
                M.log_loss_binary(pa, y) - M.log_loss_binary(pb, y),
                sub["sb_match_id"].to_numpy(),
                seed,
            )
            out["by_template_state"][key] = {
                "template": tpl,
                "state": state,
                "n_instances": int(len(sub)),
                "log_loss_a": float(M.log_loss_binary(pa, y).mean()),
                "log_loss_b": float(M.log_loss_binary(pb, y).mean()),
                "log_loss_diff_a_minus_b": s,
            }
            keys.append(key)
            ps.append(s["p"])
    q, keep = benjamini_hochberg(ps, 0.10)
    for k, qq, kk in zip(keys, q, keep, strict=True):
        rec = out["by_template_state"][k]
        rec["q_bh"], rec["survives_bh_10pct"] = qq, bool(kk)
        est = rec["log_loss_diff_a_minus_b"]["estimate"]
        rec["verdict"] = (
            (f"{label_a} better" if est < 0 else f"{label_b} better")
            if kk
            else "not distinguishable"
        )
    out["bh_family"] = {
        "name": name,
        "a": label_a,
        "b": label_b,
        "n_tests": len(keys),
        "fdr": 0.10,
        "survivors_a_better": [
            k
            for k, kk in zip(keys, keep, strict=True)
            if kk and out["by_template_state"][k]["log_loss_diff_a_minus_b"]["estimate"] < 0
        ],
        "survivors_b_better": [
            k
            for k, kk in zip(keys, keep, strict=True)
            if kk and out["by_template_state"][k]["log_loss_diff_a_minus_b"]["estimate"] > 0
        ],
    }
    return out


def _pooled(inst: pd.DataFrame, seed: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for state in STATES:
        for grp, mask in (
            ("all", inst["n_legs"] >= 2),
            ("2_legs", inst["n_legs"] == 2),
            ("3plus_legs", inst["n_legs"] >= 3),
        ):
            sub = inst[(inst["state"] == state) & inst["y"].notna() & mask]
            if len(sub) < 30:
                continue
            y = sub["y"].to_numpy(float)
            cl = sub["sb_match_id"].to_numpy()
            rec: dict[str, Any] = {"n": int(len(sub))}
            for name in ("p_sim", "p_copula", "p_naive"):
                p = sub[name].to_numpy(float)
                bm = binary_metrics(p, y)
                rec[name] = {
                    "log_loss": float(M.log_loss_binary(p, y).mean()),
                    "brier": float(M.brier_binary(p, y).mean()),
                    "ece": bm.get("ece"),
                }
            ll = {
                n: M.log_loss_binary(sub[n].to_numpy(float), y)
                for n in ("p_sim", "p_copula", "p_naive")
            }
            rec["diff_sim_minus_copula"] = cluster_diff_summary(
                ll["p_sim"] - ll["p_copula"], cl, seed
            )
            rec["diff_copula_minus_naive"] = cluster_diff_summary(
                ll["p_copula"] - ll["p_naive"], cl, seed
            )
            out[f"{state}|{grp}"] = rec
    return out


def run_copula(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    p5 = load_config("phase5")
    n_sims = int(p5["n_sims"])
    seed = int(cfg["seed"])
    metrics_dir = resolve_path(cfg, "metrics_dir")
    cache_dir = resolve_path(cfg, "processed_dir")
    ctx = load_context(cfg)
    model = _model(ctx)
    tune = pd.concat(
        [collect_block(ctx, model, s, "tune", n_sims, seed, cache_dir) for s in STATES],
        ignore_index=True,
    )
    rho = estimate_rho(tune, n_sims)  # tuning window only: nothing from the test window yet
    test = pd.concat(
        [collect_block(ctx, model, s, "test", n_sims, seed, cache_dir) for s in STATES],
        ignore_index=True,
    )
    floor = 1.0 / (2.0 * n_sims)
    test["p_sim"] = test["p_sim_raw"].clip(floor, 1 - floor)
    test["p_naive"] = test["p_naive_raw"].clip(floor, 1 - floor)
    # ---- determinism check against the committed Phase 5 instances
    ref = pd.read_parquet(cache_dir / "phase5_sga_instances.parquet")
    key = ["sb_match_id", "state", "template", "side"]
    mg = test.merge(ref[[*key, "p_sim", "p_naive", "y"]], on=key, suffixes=("", "_ref"))
    same_n = len(mg) == len(test) == len(ref)
    d_sim = float(np.nanmax(np.abs(mg["p_sim"] - mg["p_sim_ref"]))) if len(mg) else float("nan")
    d_naive = (
        float(np.nanmax(np.abs(mg["p_naive"] - mg["p_naive_ref"]))) if len(mg) else float("nan")
    )
    same_y = bool((mg["y"].fillna(-1) == mg["y_ref"].fillna(-1)).all())
    check = {
        "n_test_instances": int(len(test)),
        "n_committed_instances": int(len(ref)),
        "same_instance_set": bool(same_n),
        "max_abs_diff_p_sim": d_sim,
        "max_abs_diff_p_naive": d_naive,
        "same_outcomes": same_y,
    }
    if not (same_n and same_y and d_sim < 1e-9 and d_naive < 1e-9):
        write_json(metrics_dir / "phase5_copula_determinism_failure.json", check)
        raise RuntimeError(f"re-simulation does not reproduce Phase 5: {check}")
    # ---- copula prices on the test window with the tuning-window correlations
    test["p_copula"] = np.nan
    for tpl, (k, _rel) in TEMPLATE_META.items():
        m = (test["template"] == tpl).to_numpy()
        if not m.any():
            continue
        p = _legs(test[m], k)
        ok = np.isfinite(p).all(axis=1)
        vals = np.full(len(p), np.nan)
        if ok.any():
            vals[ok] = copula_prob(p[ok], float(rho[tpl]["rho"]), n_sims)
        test.loc[m, "p_copula"] = vals
    settled = test[test["y"].notna()]
    n_settled_missing = int(settled["p_copula"].isna().sum())
    fam_s = _paired_family(
        test, "p_sim", "p_copula", seed, "S: simulated joint vs copula", "simulated", "copula"
    )
    fam_c = _paired_family(
        test, "p_copula", "p_naive", seed, "C: copula vs naive product", "copula", "naive"
    )
    pooled = _pooled(test[test["p_copula"].notna()], seed)
    prov = provenance(CMD, cfg, ctx.version)
    out = {
        "provenance": prov,
        "decision": "D-063 (B2)",
        "n_sims": n_sims,
        "rho_by_template": rho,
        "n_tune_instances_total": int(len(tune)),
        "determinism_check_against_phase5": check,
        "n_settled_instances_without_copula_price": n_settled_missing,
        "family_S_simulated_vs_copula": fam_s,
        "family_C_copula_vs_naive": fam_c,
        "pooled": pooled,
        "summary": {
            "family_S_cells": fam_s["bh_family"]["n_tests"],
            "simulated_better_than_copula": len(fam_s["bh_family"]["survivors_a_better"]),
            "copula_better_than_simulated": len(fam_s["bh_family"]["survivors_b_better"]),
            "family_C_cells": fam_c["bh_family"]["n_tests"],
            "copula_better_than_naive": len(fam_c["bh_family"]["survivors_a_better"]),
            "naive_better_than_copula": len(fam_c["bh_family"]["survivors_b_better"]),
        },
    }
    write_json(metrics_dir / "phase5_copula.json", out)
    test.drop(columns=["p_sim_raw", "p_naive_raw"]).to_parquet(
        cache_dir / "phase5_copula_instances.parquet", index=False
    )
    recs = []
    for fam_name, fam in (("S", fam_s), ("C", fam_c)):
        for k, v in fam["by_template_state"].items():
            if "q_bh" not in v:
                continue
            recs.append(
                make_record(
                    f"phase5::copula::family{fam_name}::{k}",
                    cfg,
                    ctx.version,
                    CMD,
                    feature_set="naive-product legs; equicorrelation per template (tune mw10-19)",
                    model="gaussian_copula_equicorrelation",
                    hyperparameters={"rho": rho[v["template"]]["rho"], "n_sims": n_sims},
                    validation_window="player_2015_16: rho on tune mw10-19, test mw20-38",
                    metrics={
                        "log_loss_diff_a_minus_b": v["log_loss_diff_a_minus_b"],
                        "q_bh": v["q_bh"],
                        "n": v["n_instances"],
                    },
                    calibration={},
                    notes=f"D-063 family {fam_name}: verdict {v['verdict']}",
                    status="promoted"
                    if v["verdict"].endswith("better") and v["verdict"].startswith("simulated")
                    else "rejected",
                )
            )
    append_records(recs)
    log.info("copula summary: %s", out["summary"])
    return metrics_dir / "phase5_copula.json"
