"""Team-market baselines: league average, static Poisson, de-vigged closing market.

Blocks (D-033, D-025): pillar_a_primary (2024/25), pillar_a_secondary (2025/26),
team_2015_16 (all) and team_2015_16_test (matchweek 20-38). Nothing here is tuned; every model is
refitted weekly (Monday cutoff <= the match's opening as-of time) and sees only completed matches.
"""

import logging
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from numpy.typing import NDArray

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.evaluation import plots
from edgeforge.evaluation.common import (
    FIGURES_DIR,
    binary_metrics,
    class_metrics,
    connect,
    load_fd_clock,
    warehouse_version,
    write_json,
)
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.splits import SplitGuard, load_guard, load_split_ids
from edgeforge.features.pit import eligible_history, league_frequency_features, register_clocks
from edgeforge.models.poisson_static import RIDGE_ALPHA, fit_static_poisson
from edgeforge.pricing.devig import devig
from edgeforge.pricing.markets import OU_LINES, market_probs, score_grid

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]
WINDOW_DAYS = 1095  # trailing three years, fixed a priori (not tuned)
METHODS = ("proportional", "power", "shin")
N_BOOT = 1000


def poisson_lambdas(fd_all: pd.DataFrame, ev: pd.DataFrame, guard: SplitGuard) -> pd.DataFrame:
    """Weekly-refit static Poisson intensities for every evaluation match."""
    parts = []
    by_league = {lg: g for lg, g in fd_all.groupby("league")}
    for (league, cutoff), g in ev.groupby(["league", "model_cutoff"]):
        ct = pd.Timestamp(str(cutoff))
        hist = eligible_history(by_league[str(league)], ct, WINDOW_DAYS)
        guard.check_fit_before(hist["done_ts"], ct, f"poisson {league} {cutoff}")
        if len(hist) < 100:
            continue
        model = fit_static_poisson(hist)
        lam_h, lam_a = model.predict(g["home"], g["away"])
        parts.append(
            pd.DataFrame({"match_id": g["match_id"].to_numpy(), "lam_h": lam_h, "lam_a": lam_a})
        )
    return pd.concat(parts, ignore_index=True)


def market_odds(
    con: duckdb.DuckDBPyConnection, match_ids: list[str], bookmaker: str
) -> pd.DataFrame:
    con.register("ev_ids", pd.DataFrame({"id": match_ids}))
    return con.execute(
        """
        SELECT match_id,
               max(price) FILTER (WHERE market = '1X2' AND selection = 'home') AS h,
               max(price) FILTER (WHERE market = '1X2' AND selection = 'draw') AS d,
               max(price) FILTER (WHERE market = '1X2' AND selection = 'away') AS a,
               max(price) FILTER (WHERE market = 'OU' AND selection = 'over') AS o,
               max(price) FILTER (WHERE market = 'OU' AND selection = 'under') AS u
        FROM odds
        WHERE snapshot = 'close' AND bookmaker = ? AND match_id IN (SELECT id FROM ev_ids)
        GROUP BY match_id
        """,
        [bookmaker],
    ).df()


def outcomes(ev: pd.DataFrame) -> dict[str, Any]:
    hg, ag = ev["fthg"].to_numpy(), ev["ftag"].to_numpy()
    y = np.where(hg > ag, 0, np.where(hg == ag, 1, 2)).astype(np.int64)
    out: dict[str, Any] = {"1x2": y, "btts": ((hg > 0) & (ag > 0)).astype(float)}
    for line in OU_LINES:
        out[f"over_{line}"] = ((hg + ag) > line).astype(float)
    return out


def _all_probs_from_lambda(lam: pd.DataFrame, ev: pd.DataFrame) -> dict[str, FloatArray]:
    m = ev[["match_id"]].merge(lam, on="match_id", how="left")
    ok = m["lam_h"].notna().to_numpy()
    probs = market_probs(
        score_grid(m["lam_h"].fillna(1.0).to_numpy(), m["lam_a"].fillna(1.0).to_numpy())
    )
    out: dict[str, FloatArray] = {}
    for k, v in probs.items():
        v = v.astype(float).copy()
        v[~ok] = np.nan
        out[k] = v
    return out


def _league_probs(con: duckdb.DuckDBPyConnection, ev: pd.DataFrame) -> dict[str, FloatArray]:
    tgt = pd.DataFrame(
        {"match_id": ev["match_id"], "league": ev["league"], "cutoff_ts": ev["model_cutoff"]}
    )
    f = league_frequency_features(con, tgt, WINDOW_DAYS)
    f = ev[["match_id"]].merge(f, on="match_id", how="left")
    out: dict[str, FloatArray] = {"1x2": f[["home_win", "draw", "away_win"]].to_numpy(float)}
    for line in OU_LINES:
        out[f"over_{line}"] = f[f"over_{str(line).replace('.', '_')}"].to_numpy(float)
    out["btts"] = f["btts"].to_numpy(float)
    return out


def _market_probs(
    con: duckdb.DuckDBPyConnection, ev: pd.DataFrame, bookmaker: str, method: str
) -> tuple[dict[str, FloatArray], int]:
    odds = ev[["match_id"]].merge(market_odds(con, ev["match_id"].tolist(), bookmaker), how="left")
    fb = 0
    out: dict[str, FloatArray] = {}
    x = odds[["h", "d", "a"]].to_numpy(float)
    ok = ~np.isnan(x).any(axis=1)
    p3 = np.full((len(odds), 3), np.nan)
    if ok.any():
        p3[ok], f1 = devig(x[ok], method)
        fb += f1
    out["1x2"] = p3
    y = odds[["o", "u"]].to_numpy(float)
    ok2 = ~np.isnan(y).any(axis=1)
    p2 = np.full(len(odds), np.nan)
    if ok2.any():
        pp, f2 = devig(y[ok2], method)
        p2[ok2] = pp[:, 0]
        fb += f2
    out["over_2.5"] = p2
    return out, fb


def _model_metrics(
    probs: dict[str, FloatArray], y: dict[str, Any], mask: NDArray[np.bool_] | None = None
) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for mk, p in probs.items():
        m = ~np.isnan(p).any(axis=1) if p.ndim == 2 else ~np.isnan(p)
        if mask is not None:
            m = m & mask
        if not m.any():
            continue
        res[mk] = (
            class_metrics(p[m], y["1x2"][m]) if mk == "1x2" else binary_metrics(p[m], y[mk][m])
        )
    return res


def _losses(p: FloatArray, y: Any, market: str, kind: str) -> FloatArray:
    if market == "1x2":
        return M.rps(p, y) if kind == "rps" else M.log_loss_multiclass(p, y)
    return M.log_loss_binary(p, y)


def _compare(
    a: dict[str, FloatArray],
    b: dict[str, FloatArray],
    y: dict[str, Any],
    market: str,
    kind: str,
    seed: int,
) -> dict[str, float] | None:
    pa, pb = a.get(market), b.get(market)
    if pa is None or pb is None:
        return None
    ok = ~(np.isnan(pa).any(axis=1) if pa.ndim == 2 else np.isnan(pa))
    ok &= ~(np.isnan(pb).any(axis=1) if pb.ndim == 2 else np.isnan(pb))
    if ok.sum() < 30:
        return None
    ya = y["1x2"] if market == "1x2" else y[market]
    la, lb = _losses(pa[ok], ya[ok], market, kind), _losses(pb[ok], ya[ok], market, kind)
    return M.paired_bootstrap(la, lb, np.flatnonzero(ok), N_BOOT, seed)


def evaluate_block(
    con: duckdb.DuckDBPyConnection,
    fd_all: pd.DataFrame,
    ev: pd.DataFrame,
    guard: SplitGuard,
    bookmakers: list[str],
    seed: int,
    figure_stem: str | None,
) -> dict[str, Any]:
    ev = ev.sort_values("match_id").reset_index(drop=True)
    y = outcomes(ev)
    lam = poisson_lambdas(fd_all, ev, guard)
    models: dict[str, dict[str, FloatArray]] = {
        "league_avg": _league_probs(con, ev),
        "static_poisson": _all_probs_from_lambda(lam, ev),
    }
    fallbacks: dict[str, int] = {}
    for bk in bookmakers:
        for meth in METHODS:
            probs, fb = _market_probs(con, ev, bk, meth)
            models[f"market_{bk}_{meth}"] = probs
            fallbacks[f"market_{bk}_{meth}"] = fb
    result: dict[str, Any] = {
        "n_matches": len(ev),
        "per_model": {},
        "market_fallbacks_to_proportional": fallbacks,
        "comparisons": [],
    }
    # per-model metrics on each model's own available set
    for name, probs in models.items():
        result["per_model"][name] = _model_metrics(probs, y)
    # per-model metrics on the common set where a given bookmaker's 1X2 exists
    for bk in bookmakers:
        have = ~np.isnan(models[f"market_{bk}_proportional"]["1x2"]).any(axis=1)
        result[f"common_set_{bk}"] = {
            "n_matches": int(have.sum()),
            "per_model": {
                name: _model_metrics(probs, y, have)
                for name, probs in models.items()
                if not name.startswith("market_") or name.startswith(f"market_{bk}_")
            },
        }
    # paired bootstrap comparisons (loss_a - loss_b; negative favours A)
    pairs: list[tuple[str, str]] = [("static_poisson", "league_avg")]
    for bk in bookmakers:
        pairs += [(f"market_{bk}_proportional", "static_poisson")]
        pairs += [(f"market_{bk}_power", f"market_{bk}_proportional")]
        pairs += [(f"market_{bk}_shin", f"market_{bk}_proportional")]
    for a, b in pairs:
        for market, kind in (("1x2", "log_loss"), ("1x2", "rps"), ("over_2.5", "log_loss")):
            cmp_ = _compare(models[a], models[b], y, market, kind, seed)
            if cmp_:
                result["comparisons"].append(
                    {"a": a, "b": b, "market": market, "loss": kind, **cmp_}
                )
    cmp_btts = _compare(models["static_poisson"], models["league_avg"], y, "btts", "log_loss", seed)
    if cmp_btts:
        result["comparisons"].append(
            {
                "a": "static_poisson",
                "b": "league_avg",
                "market": "btts",
                "loss": "log_loss",
                **cmp_btts,
            }
        )
    # goal-count diagnostics for the Poisson model
    m = ev[["match_id"]].merge(lam, on="match_id", how="left")
    ok = m["lam_h"].notna().to_numpy()
    diag: dict[str, Any] = {}
    for side, lamv, goals in (
        ("home", m["lam_h"].to_numpy(float), ev["fthg"].to_numpy()),
        ("away", m["lam_a"].to_numpy(float), ev["ftag"].to_numpy()),
        ("total", (m["lam_h"] + m["lam_a"]).to_numpy(float), (ev["fthg"] + ev["ftag"]).to_numpy()),
    ):
        lv, gv = lamv[ok], goals[ok].astype(np.int64)
        pit = M.pit_values(lv, gv, seed)
        diag[side] = {
            "pit_histogram_10": M.pit_histogram(pit),
            "coverage_50": M.central_interval_coverage(lv, gv, 0.5),
            "coverage_80": M.central_interval_coverage(lv, gv, 0.8),
            "coverage_95": M.central_interval_coverage(lv, gv, 0.95),
            **M.mae_rmse(lv, gv.astype(float)),
            "mean_pred": float(lv.mean()),
            "mean_obs": float(gv.mean()),
        }
    result["poisson_goal_count_diagnostics"] = diag
    # figures
    if figure_stem:
        fig_bk: str | None = bookmakers[0] if bookmakers else None
        have = (
            ~np.isnan(models[f"market_{fig_bk}_proportional"]["1x2"]).any(axis=1)
            if fig_bk
            else np.ones(len(ev), bool)
        )
        curves = {}
        for name in ["league_avg", "static_poisson"] + (
            [f"market_{fig_bk}_shin"] if fig_bk else []
        ):
            p = models[name]["1x2"]
            m_ok = have & ~np.isnan(p).any(axis=1)
            curves[name] = M.multiclass_reliability(p[m_ok], y["1x2"][m_ok])
        plots.reliability_plot(
            curves,
            FIGURES_DIR / f"reliability_1x2_{figure_stem}.png",
            f"1X2 reliability: {figure_stem}",
            f"matches with {fig_bk or 'all'} odds: {int(have.sum())}",
        )
        plots.pit_plot(
            {s: d["pit_histogram_10"] for s, d in diag.items()},
            FIGURES_DIR / f"pit_goals_{figure_stem}.png",
            f"Poisson goal PIT: {figure_stem}",
        )
    result["_internal_lambda"] = lam
    return result


def run_team_baselines(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    con = connect(cfg)
    guard = load_guard()
    fd_all = load_fd_clock(con)
    register_clocks(con, fd_all, None)
    a = load_split_ids("pillar_a")
    t = load_split_ids("team_2015_16")
    seed = int(cfg["seed"])
    blocks: dict[str, tuple[list[str], list[str], str | None]] = {
        "pillar_a_primary_2024_25": (a["test_primary_2024_25"], ["PS", "Avg"], "pillar_a_2024_25"),
        "pillar_a_secondary_2025_26": (
            a["test_secondary_2025_26"],
            ["PS", "Avg"],
            "pillar_a_2025_26",
        ),
        "team_2015_16_all": (t["eval_2015_16_all"], ["PS"], "team_2015_16"),
        "team_2015_16_test_mw20_38": (t["test_2015_16_mw20_38"], ["PS"], None),
    }
    out: dict[str, Any] = {}
    lambdas: list[pd.DataFrame] = []
    for name, (ids, bks, fig) in blocks.items():
        log.info("block %s: %d matches", name, len(ids))
        ev = fd_all[fd_all["match_id"].isin(set(ids))].copy()
        res = evaluate_block(con, fd_all, ev, guard, bks, seed, fig)
        lam = res.pop("_internal_lambda")
        if name == "team_2015_16_all":
            lambdas.append(lam)
        out[name] = res
    con.close()
    version = warehouse_version(cfg)
    from edgeforge.provenance import provenance

    payload = {
        "provenance": provenance("edgeforge baselines team", cfg, version),
        "window_days": WINDOW_DAYS,
        "ridge_alpha": RIDGE_ALPHA,
        "blocks": out,
    }
    metrics_dir = resolve_path(cfg, "metrics_dir")
    write_json(metrics_dir / "baseline_team.json", payload)
    if lambdas:
        proc = resolve_path(cfg, "processed_dir")
        lambdas[0].to_parquet(proc / "baseline_lambdas_team_2015_16.parquet", index=False)
    records = _registry_records(cfg, version, out)
    n = append_records(records)
    log.info("registry: %d new records", n)
    return metrics_dir / "baseline_team.json"


def _registry_records(
    cfg: dict[str, Any], version: str, out: dict[str, Any]
) -> list[dict[str, Any]]:
    recs = []
    windows = {
        "pillar_a_primary_2024_25": "Pillar A walk-forward, test 2024/25 (weekly refit)",
        "pillar_a_secondary_2025_26": "Pillar A walk-forward, secondary 2025/26 (weekly refit)",
        "team_2015_16_all": "2015/16 team block, weekly walk-forward over all 2015/16",
        "team_2015_16_test_mw20_38": "2015/16 team block, matchweek 20-38 test subset",
    }
    for block, res in out.items():
        for model, mm in res["per_model"].items():
            kind = "market" if model.startswith("market_") else model
            recs.append(
                make_record(
                    f"baseline::team::{block}::{model}",
                    cfg,
                    version,
                    "edgeforge baselines team",
                    feature_set="football-data results/odds, point-in-time (opening state)",
                    model=model,
                    hyperparameters={
                        "window_days": WINDOW_DAYS,
                        "ridge_alpha": RIDGE_ALPHA if kind == "static_poisson" else None,
                        "refit": "weekly Monday cutoff <= opening as-of",
                        "tuned": False,
                    },
                    validation_window=windows[block],
                    metrics=mm,
                    calibration={"ece_1x2": mm.get("1x2", {}).get("ece")},
                    notes="No tuned parameters. Market baselines are de-vigged closing odds; "
                    "metrics are on each model's own available set (see common_set_* in the "
                    "metrics file for aligned comparisons).",
                    status="baseline",
                )
            )
    return recs
