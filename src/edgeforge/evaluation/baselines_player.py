"""Player-prop baselines (lineups state, StatsBomb 2015/16, D-033/D-030).

Baseline: Poisson count with mean = rolling per-90 rate x expected minutes given appearance.
  - rate: last-N squad matches, shrunk toward the position-group league rate with k pseudo-minutes
  - expected minutes: mean minutes in prior starts (announced starters) or prior bench
    appearances (announced bench), fallback to the position-group mean
Choices (N, k) are made on the tuning window (matchweeks 10-19) only, with test-window matches
excluded from the history that tuning reads. The test window (20-38) is reported once.
Markets are settled on players who appear (void otherwise, D-030).
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.evaluation import plots
from edgeforge.evaluation.common import (
    FIGURES_DIR,
    binary_metrics,
    connect,
    warehouse_version,
    write_json,
)
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.splits import load_guard, load_split_ids
from edgeforge.features.pit import (
    assert_no_forbidden,
    player_features,
    position_group_rates,
    register_clocks,
    sb_clock,
)

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]
WINDOWS = (5, 10, 1000)
SHRINK_MINUTES = (0, 90, 270, 540, 1080, 2160)
STATS = {"shots": "shots", "sot": "sot", "goals": "goals"}
MARKETS = {  # market -> (stat, threshold)
    "shots_1plus": ("shots", 1),
    "shots_2plus": ("shots", 2),
    "sot_1plus": ("sot", 1),
    "sot_2plus": ("sot", 2),
    "anytime_scorer": ("goals", 1),
}
N_BOOT = 1000


def load_inputs(
    con: Any, ids: list[int], exclude: list[int], context: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Features, group rates and outcomes for `ids`, with `exclude` removed from all history."""
    feats = player_features(con, "lineups", ids, exclude)
    assert_no_forbidden(feats)
    gr = position_group_rates(con, "lineups", ids, exclude)
    con.register("o_ids", pd.DataFrame({"id": ids}))
    out = con.execute(
        "SELECT sb_match_id, player_id, appeared, shots, shots_on_target AS sot, goals "
        "FROM player_match WHERE sb_match_id IN (SELECT id FROM o_ids)"
    ).df()
    log.info("%s: %d feature rows", context, len(feats))
    return feats.merge(out, on=["sb_match_id", "player_id"]), gr


def predict_lambda(
    df: pd.DataFrame, gr: pd.DataFrame, n: int, k: float, position_average: bool = False
) -> pd.DataFrame:
    """Poisson means for shots, SoT and goals plus expected minutes."""
    g = gr.copy()
    for s in ("shots", "sot", "goals"):
        g[f"r_{s}"] = np.where(g["g_minutes"] > 0, g[f"g_{s}"] / g["g_minutes"] * 90.0, np.nan)
    g["gm_start"] = g["g_mins_started"] / g["g_n_started"].replace(0, np.nan)
    g["gm_bench"] = g["g_mins_bench_app"] / g["g_n_bench_app"].replace(0, np.nan)
    all_groups = (
        g.groupby("sb_match_id")[
            [
                "g_minutes",
                "g_shots",
                "g_sot",
                "g_goals",
                "g_mins_started",
                "g_n_started",
                "g_mins_bench_app",
                "g_n_bench_app",
            ]
        ]
        .sum()
        .reset_index()
    )
    for s in ("shots", "sot", "goals"):
        all_groups[f"r_{s}"] = all_groups[f"g_{s}"] / all_groups["g_minutes"] * 90.0
    all_groups["gm_start"] = all_groups["g_mins_started"] / all_groups["g_n_started"]
    all_groups["gm_bench"] = all_groups["g_mins_bench_app"] / all_groups["g_n_bench_app"]
    keep = ["sb_match_id", "r_shots", "r_sot", "r_goals", "gm_start", "gm_bench"]
    d = df.merge(
        g[keep + ["position_group"]], on=["sb_match_id", "position_group"], how="left"
    ).merge(all_groups[keep], on="sb_match_id", how="left", suffixes=("", "_all"))
    for c in keep[1:]:
        d[c] = d[c].fillna(d[f"{c}_all"])
    n_s, n_b = f"n_start_{n}", f"n_bench_app_{n}"
    start_mean = np.where(d[n_s] > 0, d[f"mins_started_{n}"] / d[n_s].replace(0, np.nan), np.nan)
    bench_mean = np.where(d[n_b] > 0, d[f"mins_bench_app_{n}"] / d[n_b].replace(0, np.nan), np.nan)
    exp_min = np.where(
        d["announced_starter"],
        np.where(np.isnan(start_mean), d["gm_start"], start_mean),
        np.where(np.isnan(bench_mean), d["gm_bench"], bench_mean),
    )
    out = d[["sb_match_id", "player_id"]].copy()
    out["exp_min"] = exp_min
    mins = d[f"mins_{n}"].to_numpy(float)
    for s in ("shots", "sot", "goals"):
        r_g = d[f"r_{s}"].to_numpy(float)
        if position_average:
            rate = r_g
        else:
            num = d[f"{s}_{n}"].to_numpy(float) + (k / 90.0) * r_g
            den = (mins + k) / 90.0
            with np.errstate(divide="ignore", invalid="ignore"):
                rate = np.where(den > 0, num / den, r_g)
        out[f"lam_{s}"] = rate * exp_min / 90.0
    return out


def _market_p(lam: FloatArray, thr: int) -> FloatArray:
    if thr == 1:
        return 1.0 - np.exp(-lam)
    return 1.0 - np.exp(-lam) * (1.0 + lam)


def score_config(
    df: pd.DataFrame, gr: pd.DataFrame, n: int, k: float, position_average: bool = False
) -> dict[str, Any]:
    lam = predict_lambda(df, gr, n, k, position_average)
    d = df.merge(lam, on=["sb_match_id", "player_id"])
    d = d[d["appeared"] & d["exp_min"].notna()]
    res: dict[str, Any] = {"n_rows": int(len(d)), "markets": {}}
    for mk, (stat, thr) in MARKETS.items():
        p = _market_p(d[f"lam_{stat}"].to_numpy(float), thr)
        y = (d[stat].to_numpy() >= thr).astype(float)
        res["markets"][mk] = binary_metrics(p, y)
    res["mean_log_loss"] = float(np.mean([m["log_loss"] for m in res["markets"].values()]))
    res["_frame"] = d
    return res


def _strip(r: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def run_player_baselines(cfg: dict[str, Any] | None = None) -> Path:
    from edgeforge.provenance import provenance

    cfg = cfg or load_config("data")
    con = connect(cfg)
    guard = load_guard()
    sbm = con.execute(
        "SELECT sb_match_id, league, match_week, match_date, kick_off FROM sb_matches"
    ).df()
    register_clocks(con, None, sb_clock(sbm))
    p = load_split_ids("player_2015_16")
    tune_ids, test_ids = p["tune_mw10_19"], p["test_mw20_38"]
    seed = int(cfg["seed"])

    # ---- tuning: history excludes every test-window match
    all_ids = sorted(r[0] for r in con.execute("SELECT sb_match_id FROM sb_matches").fetchall())
    history_read = [i for i in all_ids if i not in set(test_ids)]
    guard.check_tuning(history_read, "player baseline tuning history")
    guard.check_tuning(tune_ids, "player baseline tuning targets")
    df_t, gr_t = load_inputs(con, tune_ids, test_ids, "tune")
    grid: list[dict[str, Any]] = []
    for n in WINDOWS:
        for k in SHRINK_MINUTES:
            r = score_config(df_t, gr_t, n, k)
            grid.append({"n": n, "k": k, "tune": _strip(r)})
    best = min(grid, key=lambda g: g["tune"]["mean_log_loss"])
    n_best, k_best = int(best["n"]), float(best["k"])
    log.info("chosen on tune window: N=%s k=%s", n_best, k_best)

    # ---- test window (reported once)
    df_x, gr_x = load_inputs(con, test_ids, [], "test")
    res = score_config(df_x, gr_x, n_best, k_best)
    pos = score_config(df_x, gr_x, n_best, k_best, position_average=True)
    test_tune_rows = score_config(df_t, gr_t, n_best, k_best)
    dfm, dfp = res.pop("_frame"), pos.pop("_frame")
    test_tune_rows.pop("_frame")

    comparisons = []
    for mk, (stat, thr) in MARKETS.items():
        pa = _market_p(dfm[f"lam_{stat}"].to_numpy(float), thr)
        pb = _market_p(dfp[f"lam_{stat}"].to_numpy(float), thr)
        y = (dfm[stat].to_numpy() >= thr).astype(float)
        comparisons.append(
            {
                "a": "rolling_per90",
                "b": "position_average",
                "market": mk,
                "loss": "log_loss",
                **M.paired_bootstrap(
                    M.log_loss_binary(pa, y),
                    M.log_loss_binary(pb, y),
                    dfm["sb_match_id"].to_numpy(),
                    N_BOOT,
                    seed,
                ),
            }
        )
    counts: dict[str, Any] = {}
    pit_h: dict[str, list[float]] = {}
    for stat in ("shots", "sot", "goals"):
        lamv = dfm[f"lam_{stat}"].to_numpy(float)
        yv = dfm[stat].to_numpy().astype(np.int64)
        pit = M.pit_values(lamv, yv, seed)
        pit_h[stat] = M.pit_histogram(pit)
        counts[stat] = {
            "pit_histogram_10": pit_h[stat],
            "coverage_50": M.central_interval_coverage(lamv, yv, 0.5),
            "coverage_80": M.central_interval_coverage(lamv, yv, 0.8),
            "coverage_95": M.central_interval_coverage(lamv, yv, 0.95),
            **M.mae_rmse(lamv, yv.astype(float)),
            "mean_pred": float(lamv.mean()),
            "mean_obs": float(yv.mean()),
        }
    curves: dict[str, list[dict[str, float]]] = {}
    curves_pos: dict[str, list[dict[str, float]]] = {}
    for mk, (stat, thr) in MARKETS.items():
        y = (dfm[stat].to_numpy() >= thr).astype(float)
        curves[mk] = M.reliability_bins(_market_p(dfm[f"lam_{stat}"].to_numpy(float), thr), y)
        curves_pos[mk] = M.reliability_bins(_market_p(dfp[f"lam_{stat}"].to_numpy(float), thr), y)
    plots.reliability_plot(
        curves,
        FIGURES_DIR / "reliability_props_rolling_test.png",
        "Player props: rolling per-90, test mw20-38",
        f"appeared player-matches: {len(dfm)}",
    )
    plots.reliability_plot(
        curves_pos,
        FIGURES_DIR / "reliability_props_position_avg_test.png",
        "Player props: position average, test mw20-38",
        f"appeared player-matches: {len(dfm)}",
    )
    plots.pit_plot(
        pit_h, FIGURES_DIR / "pit_props_rolling_test.png", "Player prop counts PIT, test"
    )

    version = warehouse_version(cfg)
    payload = {
        "provenance": provenance("edgeforge baselines player", cfg, version),
        "state": "lineups",
        "settlement": "players who appear; void otherwise (D-030)",
        "grid_tune_window_mw10_19": [
            {
                "n": g["n"],
                "k": g["k"],
                "mean_log_loss": g["tune"]["mean_log_loss"],
                "n_rows": g["tune"]["n_rows"],
            }
            for g in grid
        ],
        "chosen": {"window_matches": n_best, "shrink_minutes": k_best},
        "tune_window_metrics_chosen": _strip(test_tune_rows),
        "test_window_rolling_per90": _strip(res),
        "test_window_position_average": _strip(pos),
        "comparisons_rolling_vs_position_average": comparisons,
        "count_diagnostics_rolling_test": counts,
        "n_boot": N_BOOT,
    }
    metrics_dir = resolve_path(cfg, "metrics_dir")
    write_json(metrics_dir / "baseline_player.json", payload)

    recs = []
    for g in grid:
        chosen = g["n"] == n_best and g["k"] == k_best
        recs.append(
            make_record(
                f"baseline::player::grid::N{g['n']}_k{g['k']}",
                cfg,
                version,
                "edgeforge baselines player",
                feature_set="player rolling per-90 + expected minutes, lineups state",
                model="rolling_per90_poisson",
                hyperparameters={"window_matches": g["n"], "shrink_minutes": g["k"]},
                validation_window="StatsBomb 2015/16 tune window matchweeks 10-19 (test excluded)",
                metrics=g["tune"],
                calibration={m: v["ece"] for m, v in g["tune"]["markets"].items()},
                notes="Grid candidate scored on the tuning window only."
                + (" Chosen: lowest mean log loss." if chosen else " Not chosen."),
                status="baseline" if chosen else "rejected",
            )
        )
    recs.append(
        make_record(
            "baseline::player::test::rolling_per90",
            cfg,
            version,
            "edgeforge baselines player",
            feature_set="player rolling per-90 + expected minutes, lineups state",
            model="rolling_per90_poisson",
            hyperparameters={"window_matches": n_best, "shrink_minutes": k_best},
            validation_window="StatsBomb 2015/16 test window matchweeks 20-38",
            metrics=_strip(res),
            calibration={m: v["ece"] for m, v in res["markets"].items()},
            notes="Settled on appearing players. Count diagnostics in baseline_player.json.",
            status="baseline",
        )
    )
    recs.append(
        make_record(
            "baseline::player::test::position_average",
            cfg,
            version,
            "edgeforge baselines player",
            feature_set="position-group league rate + same expected minutes, lineups state",
            model="position_average_poisson",
            hyperparameters={"window_matches": n_best},
            validation_window="StatsBomb 2015/16 test window matchweeks 20-38",
            metrics=_strip(pos),
            calibration={m: v["ece"] for m, v in pos["markets"].items()},
            notes="Reference for the rolling baseline: ignores the player's own history.",
            status="baseline",
        )
    )
    n_new = append_records(recs)
    log.info("registry: %d new records", n_new)
    con.close()
    return metrics_dir / "baseline_player.json"
