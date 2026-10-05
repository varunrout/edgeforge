"""Phase 5 runners: convergence study, full simulation + SGA validation, single-match example."""

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation.common import FIGURES_DIR, write_json
from edgeforge.evaluation.phase5 import (
    TEMPLATE_META,
    check_invariants,
    comparison_b,
    convergence_study,
    curated_examples,
    dependence_ratios,
    evaluate_instance_outcome,
    expected_player_goals_ratio,
    joint_validation,
    rank_players,
    simulated_marginals,
    team_marginal_z,
    template_instances,
)
from edgeforge.evaluation.phase5_inputs import (
    Phase5Context,
    build_inputs,
    fit_shots_given_goals,
    load_context,
    realised_players,
)
from edgeforge.evaluation.plots import forest_plot, hist_plot, line_plot, reliability_plot
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.splits import load_guard
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
CMD = "edgeforge phase5 run"
WINDOW = "player_2015_16: test mw20-38"


def _model(ctx: Phase5Context) -> ShotsGivenGoals:
    train = fit_shots_given_goals(ctx.d, ctx.shots)
    load_guard().check_tuning(
        train["sb_match_id"].unique().tolist(), "conditional team-shot model (tune window only)"
    )
    return ShotsGivenGoals.fit(train)


def _rankings(inputs: dict[int, Any], s_unc: np.ndarray) -> dict[int, dict[int, list[int]]]:
    return {m: rank_players(i, s_unc[i.row_index]) for m, i in inputs.items()}


def run_convergence(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    p5 = load_config("phase5")
    ctx = load_context(cfg)
    model = _model(ctx)
    inputs, rows, prices = build_inputs(ctx, "lineups")
    s_unc = prices["_p_appear"] * prices["anytime_scorer"]
    ranks = _rankings(inputs, s_unc)
    ids = sorted(inputs)[:: max(len(inputs) // int(p5["convergence_matches"]), 1)][
        : int(p5["convergence_matches"])
    ]
    res = convergence_study(
        inputs,
        ranks,
        model,
        [int(x) for x in p5["convergence_grid"]],
        ids,
        int(cfg["seed"]),
        int(p5["convergence_reference_n"]),
    )
    prov = provenance("edgeforge phase5 convergence", cfg, ctx.version)
    write_json(
        resolve_path(cfg, "metrics_dir") / "phase5_convergence.json",
        {"provenance": prov, "shots_given_goals": model.__dict__, **res},
    )
    series = {
        q: [(float(r["n_sims"]), float(r["mean_mc_se"])) for r in rs]
        for q, rs in res["summary"].items()
    }
    line_plot(
        series,
        FIGURES_DIR / "phase5_convergence_se.png",
        "Monte Carlo SE vs number of simulations (mean over matches)",
        "n sims",
        "SE",
    )
    series_e = {
        q: [(float(r["n_sims"]), float(r["mean_abs_error_vs_reference"])) for r in rs]
        for q, rs in res["summary"].items()
    }
    line_plot(
        series_e,
        FIGURES_DIR / "phase5_convergence_error.png",
        "Absolute error vs the reference run",
        "n sims",
        "abs error",
    )
    return resolve_path(cfg, "metrics_dir") / "phase5_convergence.json"


def run_phase5(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    p5 = load_config("phase5")
    n_sims = int(p5["n_sims"])
    seed = int(cfg["seed"])
    ctx = load_context(cfg)
    model = _model(ctx)
    version = ctx.version
    metrics_dir = resolve_path(cfg, "metrics_dir")
    demo_dir = resolve_path(cfg, "processed_dir") / "demo_sims"
    demo_dir.mkdir(parents=True, exist_ok=True)
    scores = {
        int(m): (int(h), int(a))
        for m, h, a in zip(
            ctx.scores["sb_match_id"],
            ctx.scores["home_score"],
            ctx.scores["away_score"],
            strict=True,
        )
    }
    records: list[dict[str, Any]] = []
    sim_out: dict[str, Any] = {"n_sims": n_sims, "shots_given_goals": model.__dict__, "states": {}}
    marg_out: dict[str, Any] = {}
    inst_all: list[pd.DataFrame] = []
    for state in ("lineups", "opening"):
        inputs, rows, prices = build_inputs(ctx, state)
        s_unc = prices["_p_appear"] * prices["anytime_scorer"]
        realised = realised_players(rows)
        sim_prices = {
            m: np.full(len(rows), np.nan) for m in (*prices, "_p_appear") if not m.startswith("_")
        }
        inv = {
            "goal_accounting": 0,
            "goals_le_sot_le_shots": 0,
            "off_pitch_records": 0,
            "team_shot_sum": 0,
        }
        zs: dict[str, list[float]] = {}
        ratios: dict[str, list[float]] = {"home": [], "away": []}
        recs: list[dict[str, Any]] = []
        n_invalid = 0
        t_start = time.perf_counter()
        for k, (m, inp) in enumerate(sorted(inputs.items())):
            if m not in scores:
                continue
            res = simulate(inp, model, n_sims, seed)
            for key, v in check_invariants(res).items():
                inv[key] += v
            for key, z in team_marginal_z(res).items():
                zs.setdefault(key, []).append(z)
            for side, rv in expected_player_goals_ratio(res).items():
                ratios[side].append(rv)
            marg = simulated_marginals(res)
            idx = inp.row_index
            for mk in sim_prices:
                sim_prices[mk][idx] = marg[mk]
            if k < int(p5["demo_matches"]):
                np.savez_compressed(
                    demo_dir / f"{state}_{m}.npz",
                    team_goals=res.team_goals, goals=res.goals, shots=res.shots, sot=res.sot,
                    on_pitch=res.on_pitch, player_id=inp.player_id, team=inp.team,
                )  # fmt: skip
            rank = rank_players(inp, s_unc[idx])
            team_map = {int(p): int(t) for p, t in zip(inp.player_id, inp.team, strict=True)}
            score = scores[m]
            for tpl, tag, sga in template_instances(rank):
                try:
                    validate_sga(sga, team_map)
                except SGAError:
                    n_invalid += 1
                    continue
                ind, ok = indicator_matrix(sga, res)
                pr = price_from_indicators(ind, ok, leg_ok_matrix(sga, res))
                nl, rel = TEMPLATE_META[tpl]
                recs.append({
                    "sb_match_id": m, "state": state, "template": tpl, "side": tag,
                    "n_legs": nl, "relationship": rel, "legs_json": sga.model_dump_json(),
                    "p_sim": pr["joint"], "p_naive": pr["naive_product"], "se": pr["joint_mc_se"],
                    "n_eff": pr["n_effective_sims"],
                    "y": evaluate_instance_outcome(sga, score, realised.get(m, {})),
                })  # fmt: skip
        secs = time.perf_counter() - t_start
        inst = pd.DataFrame(recs)
        # smoothing of rare-event estimates: never exactly 0 or 1
        floor = 1.0 / (2.0 * n_sims)
        inst["p_sim"] = inst["p_sim"].clip(floor, 1 - floor)
        inst["p_naive"] = inst["p_naive"].clip(floor, 1 - floor)
        inst_all.append(inst)
        cmpb = comparison_b(ctx, state, rows, prices, sim_prices, seed)
        marg_out[state] = cmpb
        for mk, v in cmpb["markets"].items():
            from edgeforge.evaluation.plots import reliability_plot as rp

            rp(
                {"simulator": v["reliability_new"], "Phase 4 standalone": v["reliability_baseline"]},
                FIGURES_DIR / f"phase5_comparisonB_{state}_{mk}.png",
                f"{mk}, {state}: simulator vs standalone", f"n={v['n']}",
            )  # fmt: skip
        zarr = {k: np.array(v) for k, v in zs.items()}
        sim_out["states"][state] = {
            "n_matches": len(inputs),
            "seconds_total": secs,
            "seconds_per_match": secs / max(len(inputs), 1),
            "invariant_violations_matches": inv,
            "invalid_sga_instances_rejected": n_invalid,
            "team_marginal_z": {
                k: {
                    "mean": float(v.mean()),
                    "sd": float(v.std()),
                    "share_abs_gt_3": float((np.abs(v) > 3).mean()),
                }
                for k, v in zarr.items()
            },
            "player_goals_over_dixon_coles": {
                "home_mean": float(np.mean(ratios["home"])),
                "away_mean": float(np.mean(ratios["away"])),
                "phase4_standalone_reference": "see phase4_markets.json coherence (home 0.984, away 1.060 before D-043/D-044 reruns)",
            },
        }
        hist_plot(
            np.concatenate(list(zarr.values())), FIGURES_DIR / f"phase5_team_marginal_z_{state}.png",
            f"Simulated team marginals minus Dixon-Coles, z-scores ({state})", "z",
        )  # fmt: skip
        log.info("%s: %d matches in %.0fs", state, len(inputs), secs)
    inst = pd.concat(inst_all, ignore_index=True)
    jv = joint_validation(inst, seed)
    dep = dependence_ratios(inst)
    ex = curated_examples(inst)
    rel_pool = jv["pooled"]
    for key, v in rel_pool.items():
        if key.endswith("|all") or key.endswith("legs"):
            reliability_plot(
                {"simulated joint": v["reliability_simulated"], "naive product": v["reliability_naive"]},
                FIGURES_DIR / f"phase5_joint_reliability_{key.replace('|', '_')}.png",
                f"Joint reliability, {key}", f"n={v['n']}",
            )  # fmt: skip
    for state in ("lineups", "opening"):
        rws = [
            {
                "label": k.split("|")[0] + f" ({v['legs']} legs)",
                **v["log_loss_diff_sim_minus_naive"],
            }
            for k, v in jv["by_template_state"].items()
            if k.endswith(state) and "log_loss_diff_sim_minus_naive" in v
        ]
        forest_plot(
            rws,
            FIGURES_DIR / f"phase5_joint_logloss_forest_{state}.png",
            f"Simulated minus naive joint log loss ({state})",
            "log loss difference (negative = simulation better)",
        )
    prov = provenance(CMD, cfg, version)
    prov["phase4_cache_git_sha"] = ctx.cache_sha
    write_json(metrics_dir / "phase5_simulator.json", {"provenance": prov, **sim_out})
    write_json(
        metrics_dir / "phase5_comparison_b.json",
        {
            "provenance": prov,
            "states": marg_out,
            "markets": list(next(iter(marg_out.values()))["markets"]),
        },
    )
    write_json(
        metrics_dir / "phase5_sga.json",
        {
            "provenance": prov,
            "n_sims": n_sims,
            "joint_validation": jv,
            "dependence_ratio": dep,
            "examples": ex,
            "templates": {
                k: {"legs": v[0], "relationship": v[1]} for k, v in TEMPLATE_META.items()
            },
        },
    )
    inst.drop(columns=["legs_json"]).to_parquet(
        resolve_path(cfg, "processed_dir") / "phase5_sga_instances.parquet", index=False
    )
    for k, v in jv["by_template_state"].items():
        if "q_bh" not in v:
            continue
        records.append(
            make_record(
                f"phase5::sga::{k}", cfg, version, CMD, feature_set="simulated joint vs naive product",
                model="simulator_d014", hyperparameters={"n_sims": n_sims}, validation_window=WINDOW,
                metrics={"log_loss_diff": v["log_loss_diff_sim_minus_naive"], "q_bh": v["q_bh"], "n": v["n_instances"]},
                calibration={"ece_sim": v["simulated"].get("ece"), "ece_naive": v["naive"].get("ece")},
                notes=f"D-047 pre-registered template; verdict: {v['verdict']}.",
                status="promoted" if v["verdict"] == "simulated better" else "rejected",
            )
        )  # fmt: skip
    for state, v in marg_out.items():
        for mk, mv in v["markets"].items():
            records.append(
                make_record(
                    f"phase5::comparison_b::{state}::{mk}", cfg, version, CMD, feature_set="simulator marginals vs Phase 4 standalone",
                    model="simulator_d014", hyperparameters={"n_sims": n_sims}, validation_window=WINDOW,
                    metrics={"log_loss_diff": mv["log_loss_diff_new_minus_baseline"], "q_bh": mv["q_bh"], "n": mv["n"]},
                    calibration={"ece_sim": mv["new"].get("ece"), "ece_standalone": mv["baseline"].get("ece")},
                    notes="Comparison B; BH across six markets.",
                    status="promoted" if mv["survives_bh_10pct"] and mv["log_loss_diff_new_minus_baseline"]["estimate"] < 0 else "rejected",
                )
            )  # fmt: skip
    n = append_records(records)
    log.info("registry: %d new records", n)
    return metrics_dir / "phase5_sga.json"


def run_example(match_id: int, state: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Re-simulate one match from its seed and price every D-047 instance for it."""
    cfg = cfg or load_config("data")
    p5 = load_config("phase5")
    ctx = load_context(cfg)
    model = _model(ctx)
    inputs, rows, prices = build_inputs(ctx, state)
    inp = inputs[match_id]
    s_unc = prices["_p_appear"] * prices["anytime_scorer"]
    res = simulate(inp, model, int(p5["n_sims"]), int(cfg["seed"]))
    rank = rank_players(inp, s_unc[inp.row_index])
    out = []
    team_map = {int(p): int(t) for p, t in zip(inp.player_id, inp.team, strict=True)}
    for tpl, tag, sga in template_instances(rank):
        try:
            validate_sga(sga, team_map)
        except SGAError:
            continue
        ind, ok = indicator_matrix(sga, res)
        pr = price_from_indicators(ind, ok, leg_ok_matrix(sga, res))
        out.append(
            {"template": tpl, "side": tag, "legs": json.loads(sga.model_dump_json())["legs"], **pr}
        )
    return {"match_id": match_id, "state": state, "n_sims": res.n_sims, "instances": out}
