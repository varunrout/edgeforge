"""Phase 4 runner: participation, minutes, team shots, player markets (lineups and opening).

`edgeforge phase4 run` executes every stage on the 2015/16 player block (D-033: tune mw10-19,
test mw20-38), writes metrics with provenance, figures and one registry record per candidate.
Every tuning step goes through `tune_interior` (D-040) before any test-window metric exists.
"""

import logging
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation.common import FIGURES_DIR, warehouse_version, write_json
from edgeforge.evaluation.phase4 import (
    evaluate_minutes,
    minutes_predictions,
    stage_minutes,
    stage_starters,
    stage_team_shots,
)
from edgeforge.evaluation.phase4_data import load_p4
from edgeforge.evaluation.phase4_props import (
    MARKETS,
    coherence,
    evaluate_lineups,
    evaluate_opening,
    lineup_inputs,
    opening_inputs,
    tune_player_params,
)
from edgeforge.evaluation.phase4_recal import recalibrate_participation
from edgeforge.evaluation.plots import hist_plot, pit_plot, reliability_plot
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.splits import load_guard

log = logging.getLogger(__name__)
CMD = "edgeforge phase4 run"
WINDOW = "player_2015_16: tune mw10-19"
TEST_WINDOW = "player_2015_16: test mw20-38"


def _grid_records(
    rec: dict[str, Any],
    cfg: dict[str, Any],
    version: str,
    model: str,
    feature_set: str,
    hp_name: str,
    note: str,
) -> list[dict[str, Any]]:
    """One registry record per evaluated grid value of a tuned hyper-parameter."""
    out = []
    for e in rec["evaluated"]:
        chosen = e["value"] == rec["value"]
        out.append(
            make_record(
                f"phase4::{rec['name']}::{e['value']:g}",
                cfg,
                version,
                CMD,
                feature_set=feature_set,
                model=model,
                hyperparameters={hp_name: e["value"]},
                validation_window=WINDOW,
                metrics={"tuning_score": e["score"], "n_widenings": rec["n_widenings"]},
                calibration={},
                notes=(note + (" Chosen (interior optimum)." if chosen else " Not chosen.")),
                status="promoted" if chosen else "rejected",
            )
        )
    return out


def _rel(
    path: Path, curves: dict[str, list[dict[str, float]]], title: str, n: int | None = None
) -> None:
    reliability_plot(curves, path, title, f"n={n}" if n is not None else "")


def run_phase4(cfg: dict[str, Any] | None = None) -> Path:
    from edgeforge.provenance import provenance

    cfg = cfg or load_config("data")
    seed = int(cfg["seed"])
    version = warehouse_version(cfg)
    guard = load_guard()
    d = load_p4(cfg)
    prov = provenance(CMD, cfg, version)
    metrics_dir = resolve_path(cfg, "metrics_dir")
    records: list[dict[str, Any]] = []

    # ---- 1. starter probability (opening state)
    log.info("stage 1: starter model")
    r_start, start_pred = stage_starters(d, guard, seed)
    records += _grid_records(
        r_start["tuned"], cfg, version, "multinomial_logistic_start_bench_out",
        "recency, minutes trend, congestion, position (opening state)", "C",
        "Starter model tuning (3-class log loss, tune window).",
    )  # fmt: skip
    records.append(
        make_record(
            "phase4::starter_model::test", cfg, version, CMD,
            feature_set="opening-state starter features", model="multinomial_logistic",
            hyperparameters={"C": r_start["tuned"]["value"]}, validation_window=TEST_WINDOW,
            metrics={"start": r_start["start_model"], "paired": r_start["paired_vs_baseline_log_loss"]},
            calibration={"ece": r_start["start_model"].get("ece")},
            notes="Evaluated once on the test window after the interior check.", status="promoted",
        )
    )  # fmt: skip
    records.append(
        make_record(
            "phase4::starter_baseline_started_last_match::test", cfg, version, CMD,
            feature_set="started last match", model="started_last_match_rate_map",
            hyperparameters={}, validation_window=TEST_WINDOW,
            metrics=r_start["started_last_match_baseline"], calibration={}, notes="Baseline.",
            status="baseline",
        )
    )  # fmt: skip
    _rel(
        FIGURES_DIR / "phase4_starter_reliability.png",
        {"start model": r_start["reliability_start_model"],
         "started last match": r_start["reliability_baseline"]},
        "P(start) reliability, test mw20-38", r_start["n_test_candidate_rows"],
    )  # fmt: skip

    # ---- 2. minutes model
    log.info("stage 2: minutes model")
    rec_min, ctx = stage_minutes(d, guard)
    records += _grid_records(
        rec_min["starter"], cfg, version, "discrete_hazard_starter_exit",
        "minutes history, position, congestion", "C", "Starter exit hazard tuning.",
    )  # fmt: skip
    records += _grid_records(
        rec_min["bench"], cfg, version, "discrete_hazard_bench_entry",
        "minutes history, position, congestion", "C", "Bench entry hazard tuning.",
    )  # fmt: skip
    mp = minutes_predictions(
        d, ctx["starters"], ctx["bench"], ctx["c_start"], ctx["c_bench"], guard
    )
    mp, recal = recalibrate_participation(ctx["starters"], ctx["bench"], mp, seed)
    for nm, rp in recal.items():
        records.append(
            make_record(
                f"phase4::recalibration_d044::{nm}", cfg, version, CMD,
                feature_set="level Platt recalibration of the hazard-model event probability",
                model="platt_level", hyperparameters={"rule": rp["rule"]},
                validation_window=TEST_WINDOW,
                metrics={"choose": rp["choose_window_log_loss_diff_recal_minus_raw"],
                         "test": rp["test_log_loss_diff_recal_minus_raw"]},
                calibration={"ece_raw": rp["test_raw"]["ece"],
                             "ece_recal": rp["test_recalibrated"]["ece"]},
                notes="D-044: adopted only if chosen inside the tuning window; test reported either way.",
                status="promoted" if rp["adopted"] else "rejected",
            )
        )  # fmt: skip
    tr_ids = d.ids["burn"] + d.ids["tune"]
    l_bar = float(d.l_bar_by_match.loc[tr_ids].mean())
    r_min = evaluate_minutes(d, ctx["starters"], ctx["bench"], mp, l_bar, guard, seed)
    pit_hist = r_min.pop("_pit_hist")
    pit_plot({"starter minutes PIT": pit_hist}, FIGURES_DIR / "phase4_minutes_pit.png",
             "Starter exit-time randomised PIT, test")  # fmt: skip
    records.append(
        make_record(
            "phase4::minutes_model::test", cfg, version, CMD,
            feature_set="minutes history", model="discrete_hazards",
            hyperparameters={"C_start": ctx["c_start"], "C_bench": ctx["c_bench"]},
            validation_window=TEST_WINDOW, metrics=r_min, calibration={},
            notes="Starter exit and bench entry evaluated against group-empirical baselines.",
            status="promoted",
        )
    )  # fmt: skip

    # ---- 3. team shot model
    log.info("stage 3: team shots")
    r_ts, shots = stage_team_shots(d, guard, seed)
    tuned_hl = r_ts.pop("tuned_half_life")
    records += _grid_records(
        tuned_hl, cfg, version, "dixon_coles_shot_rating_mapped",
        "football-data HS/AS ratings", "half_life_days", "Team shot rating half-life.",
    )  # fmt: skip
    chosen = r_ts["tuning_window_log_lik"]["chosen"]
    for fam in ("poisson", "negative_binomial"):
        records.append(
            make_record(
                f"phase4::team_shots::{fam}", cfg, version, CMD,
                feature_set="football-data HS/AS ratings mapped to StatsBomb shots",
                model=fam, hyperparameters={"half_life_days": tuned_hl["value"]},
                validation_window=WINDOW,
                metrics={"tune_log_lik": r_ts["tuning_window_log_lik"][fam],
                         "test_mean_log_lik": r_ts["test_mean_log_lik"][fam]},
                calibration={}, notes="Poisson vs negative binomial, chosen on tune log likelihood.",
                status="promoted" if fam == chosen else "rejected",
            )
        )  # fmt: skip
    pit_plot({"team shots PIT": r_ts["pit_histogram_10"]}, FIGURES_DIR / "phase4_team_shots_pit.png",
             "Team shots randomised PIT, test")  # fmt: skip

    # ---- 4./5. player markets in the lineups state
    log.info("stage 4: player parameters")
    tune_rows = lineup_inputs(d, mp, shots, "tune", l_bar)
    params, tuned = tune_player_params(d, tune_rows, l_bar, guard)
    for name, t in tuned.items():
        records += _grid_records(
            t.as_record(), cfg, version, "player_shots_sot_goals",
            "rolling player history shrunk to position group", name, f"Player model {name}.",
        )  # fmt: skip
    test_rows = lineup_inputs(d, mp, shots, "test", l_bar)
    base = _baseline_cfg()
    r_line, full, base_p = evaluate_lineups(d, test_rows, params, l_bar, base, seed)
    for m, v in r_line["comparison_E_full_distribution_vs_phase2_baseline"]["markets"].items():
        _rel(FIGURES_DIR / f"phase4_reliability_lineups_{m}.png",
             {"player model": v["reliability_new"], "phase 2 rolling baseline": v["reliability_baseline"]},
             f"{m}, lineups state, test mw20-38", v["n"])  # fmt: skip
        records.append(
            make_record(
                f"phase4::lineups::{m}", cfg, version, CMD,
                feature_set="lineups-state player features", model="player_nb_betabinom_thinning",
                hyperparameters={"shot_params": params.__dict__}, validation_window=TEST_WINDOW,
                metrics={"vs_baseline": v["log_loss_diff_new_minus_baseline"],
                         "q_bh": v["q_bh"], "n": v["n"]},
                calibration={"ece": v["new"].get("ece")},
                notes="Comparison E vs Phase 2 rolling per-90 baseline; BH 10% across six markets.",
                status="promoted" if v["survives_bh_10pct"] and
                v["log_loss_diff_new_minus_baseline"]["estimate"] < 0 else "rejected",
            )
        )  # fmt: skip
    records.append(
        make_record(
            "phase4::lineups::baseline_phase2", cfg, version, CMD,
            feature_set="rolling per-90 x plug-in minutes", model="phase2_rolling",
            hyperparameters={"window_matches": base[0], "shrink_minutes": base[1]},
            validation_window=TEST_WINDOW, metrics={}, calibration={}, notes="Baseline.",
            status="baseline",
        )
    )  # fmt: skip

    # ---- 6. opening-state props
    log.info("stage 6: opening-state props")
    cand = opening_inputs(d, mp, start_pred, shots, "test", l_bar)
    r_open, _, _ = evaluate_opening(d, cand, params, l_bar, base, seed)
    for m, v in r_open["opening_model_vs_opening_baseline"]["markets"].items():
        _rel(FIGURES_DIR / f"phase4_reliability_opening_{m}.png",
             {"opening mixture": v["reliability_new"], "opening baseline": v["reliability_baseline"]},
             f"{m}, opening state, test mw20-38", v["n"])  # fmt: skip
        records.append(
            make_record(
                f"phase4::opening::{m}", cfg, version, CMD,
                feature_set="opening-state mixture over start/bench/out", model="opening_mixture",
                hyperparameters={"shot_params": params.__dict__}, validation_window=TEST_WINDOW,
                metrics={"vs_baseline": v["log_loss_diff_new_minus_baseline"],
                         "q_bh": v["q_bh"], "n": v["n"]},
                calibration={"ece": v["new"].get("ece")},
                notes="Conditional on appearance; vs opening-state rolling baseline.",
                status="promoted" if v["survives_bh_10pct"] and
                v["log_loss_diff_new_minus_baseline"]["estimate"] < 0 else "rejected",
            )
        )  # fmt: skip

    # ---- 7. plug-in vs full distribution is inside r_line; 8. coherence
    pr = pd.read_parquet(resolve_path(cfg, "processed_dir") / "team_model_preds.parquet")
    pr = pr[(pr["block"] == "team_2015_16") & (pr["test"] == "all_2015_16")]
    coh = coherence(d, test_rows, params, l_bar, pr)
    ratios = coh.pop("_ratios")
    hist_plot(np.asarray(ratios), FIGURES_DIR / "phase4_coherence_ratio.png",
              "Sum of players' expected goals / Dixon-Coles team goals, per match",
              "ratio", ref=1.0)  # fmt: skip

    write_json(metrics_dir / "phase4_participation.json",
               {"provenance": prov, "starter_model": r_start, "minutes_model": r_min,
                "minutes_tuning": rec_min, "recalibration_d044": recal})  # fmt: skip
    write_json(metrics_dir / "phase4_shots.json",
               {"provenance": prov, "team_shots": r_ts, "team_shots_half_life_tuning": tuned_hl,
                "player_parameters": {k: v.as_record() for k, v in tuned.items()},
                "chosen_player_parameters": params.__dict__})  # fmt: skip
    write_json(metrics_dir / "phase4_markets.json",
               {"provenance": prov, "lineups_state": r_line, "opening_state": r_open,
                "coherence": coh, "phase2_baseline_config":
                {"window_matches": base[0], "shrink_minutes": base[1]}, "markets": MARKETS})  # fmt: skip
    cache = {"start_pred": start_pred, "mp": mp, "shots": shots, "params": params, "l_bar": l_bar,
             "base_cfg": base, "version": version, "git_sha": prov["git_sha"]}  # fmt: skip
    with (resolve_path(cfg, "processed_dir") / "phase4_cache.pkl").open("wb") as fh:
        pickle.dump(cache, fh)
    n = append_records(records)
    log.info("registry: %d new records", n)
    return metrics_dir / "phase4_markets.json"


def _baseline_cfg() -> tuple[int, float]:
    import json

    b = json.loads(
        (
            Path(__file__).resolve().parents[3] / "artifacts" / "metrics" / "baseline_player.json"
        ).read_text(encoding="utf-8")
    )["chosen"]
    return int(b["window_matches"]), float(b["shrink_minutes"])
