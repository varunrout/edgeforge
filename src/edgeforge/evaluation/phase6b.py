"""Phase 6b (D-053, D-055): realistic levers against the informed pre-lineup bettor.

Props only. Settlement rule (void on appearance vs void if not starting), margin allocation within
5-20% overrounds (proportional, power, odds ratio), an offer rule for the rotation band and a cap
on quoted odds, each against the same informed bettor. Settlement and cap are chosen on matchweeks
10-19; every configuration is evaluated once on matchweeks 20-38. All results are labelled
SIMULATION and UPPER BOUND (lineups assumed known with certainty at T-60 minutes).
"""

import logging
import pickle
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation.common import FIGURES_DIR, write_json
from edgeforge.evaluation.phase4_props import MARKETS, THRESH
from edgeforge.evaluation.phase5_inputs import Phase5Context, build_inputs, load_context
from edgeforge.evaluation.phase5_run import _model
from edgeforge.evaluation.phase6_pillar_c import CLIP, _ratio_ci, band_of
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.stats import Boot
from edgeforge.evaluation.tuning import Tuned, require_tuned, tune_interior
from edgeforge.provenance import git_sha, provenance
from edgeforge.simulation.engine import ShotsGivenGoals, SimResult, simulate

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]
CMD = "edgeforge phase6b run"
OVERROUNDS = (0.05, 0.10, 0.15, 0.20)
Q_CAP = 0.9999
LAMBDA = 0.05
CAP_GRID = [3.0, 5.0, 7.5, 10.0, 15.0, 25.0, 40.0, 60.0, 100.0, 200.0]
NO_CAP = 1000.0


# ----------------------------------------------------------------------------- collection


def conditional_marginals(r: SimResult, mask: NDArray[np.bool_]) -> dict[str, FloatArray]:
    """P(market | mask) per player (mask: [N, P] boolean, e.g. started or on the pitch)."""
    cnt = mask.sum(axis=0).astype(float)
    cnt = np.where(cnt > 0, cnt, np.nan)
    out: dict[str, FloatArray] = {}
    for m, (col, thr) in THRESH.items():
        arr = {"shots": r.shots, "sot": r.sot, "goals": r.goals}[col]
        out[m] = ((arr >= thr) & mask).sum(axis=0) / cnt
    return out


def collect_props(
    ctx: Phase5Context,
    split: str,
    theta: float,
    n_sims: int,
    seed: int,
    model: ShotsGivenGoals,
    edges: list[float],
    control_seed: int,
) -> pd.DataFrame:
    """One row per (match, player in the lineups squad, market) with appearance- and
    start-conditional opening probabilities, their independent re-simulation (null control),
    the lineups probabilities, settlement flags and the realised outcome."""
    in_o, _, _ = build_inputs(ctx, "opening", split)
    in_l, rows_l, _ = build_inputs(ctx, "lineups", split)
    real = {
        (int(m), int(p)): (bool(a), bool(s), int(sh), int(so), int(g))
        for m, p, a, s, sh, so, g in zip(
            rows_l["sb_match_id"],
            rows_l["player_id"],
            rows_l["appeared"].fillna(False).astype(bool),
            rows_l["is_starter"].astype(bool),
            rows_l["shots"].fillna(0).astype(int),
            rows_l["sot"].fillna(0).astype(int),
            rows_l["goals"].fillna(0).astype(int),
            strict=True,
        )
    }
    out: list[dict[str, Any]] = []
    for m in sorted(set(in_o) & set(in_l)):
        io, il = in_o[m], in_l[m]
        ro = simulate(io, model, n_sims, seed, theta)
        rl = simulate(il, model, n_sims, seed, theta)
        r2 = simulate(io, model, n_sims, control_seed, theta)
        mo, mo_s = conditional_marginals(ro, ro.on_pitch), conditional_marginals(ro, ro.started)
        m2, m2_s = conditional_marginals(r2, r2.on_pitch), conditional_marginals(r2, r2.started)
        ml = conditional_marginals(rl, rl.on_pitch)
        pos_l = {int(p): i for i, p in enumerate(il.player_id)}
        for i, pid in enumerate(io.player_id):
            j = pos_l.get(int(pid))
            if j is None:
                continue
            app, start, shots, sot, goals = real.get((m, int(pid)), (False, False, 0, 0, 0))
            outc = {"shots_1plus": shots >= 1, "shots_2plus": shots >= 2, "shots_3plus": shots >= 3,
                    "sot_1plus": sot >= 1, "sot_2plus": sot >= 2, "anytime_scorer": goals >= 1}  # fmt: skip
            band = band_of(float(io.p_start[i]), edges)
            for mk in MARKETS:
                out.append({
                    "sb_match_id": m, "player_id": int(pid), "market": mk, "band": band,
                    "p_start_open": float(io.p_start[i]), "appeared": app, "is_starter": start,
                    "p_o": float(mo[mk][i]), "p_o_s": float(mo_s[mk][i]),
                    "p_o2": float(m2[mk][i]), "p_o2_s": float(m2_s[mk][i]),
                    "p_l": float(ml[mk][j]), "y": float(outc[mk]),
                })  # fmt: skip
    return pd.DataFrame(out)


def collect_cached(
    cfg: dict[str, Any], ctx: Phase5Context, split: str, theta: float, n_sims: int, seed: int,
    model: ShotsGivenGoals, edges: list[float], reuse: bool,
) -> tuple[pd.DataFrame, str]:  # fmt: skip
    path = resolve_path(cfg, "processed_dir") / f"phase6b_collected_{split}.pkl"
    key = {"theta": theta, "n_sims": n_sims, "seed": seed, "phase4_cache": ctx.cache_sha}
    if reuse and path.exists():
        with path.open("rb") as fh:
            saved = pickle.load(fh)
        if saved["key"] == key:
            return saved["props"], saved["git_sha"]
    props = collect_props(ctx, split, theta, n_sims, seed, model, edges, seed + 1)
    sha = git_sha()
    with path.open("wb") as fh:
        pickle.dump({"key": key, "git_sha": sha, "props": props}, fh)
    return props, sha


# ----------------------------------------------------------------------------- margin allocation


def _bisect(
    f: Any, lo: FloatArray, hi: FloatArray, target: FloatArray, increasing: bool
) -> FloatArray:
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        above = f(mid) > target
        if increasing:
            hi = np.where(above, mid, hi)
            lo = np.where(above, lo, mid)
        else:
            lo = np.where(above, mid, lo)
            hi = np.where(above, hi, mid)
    return 0.5 * (lo + hi)


def quoted(p: FloatArray, m: float, alloc: str) -> tuple[FloatArray, FloatArray]:
    """Quoted (yes, no) probabilities with overround m for fair yes probability p."""
    if alloc == "proportional":
        qy, qn = p * (1 + m), (1 - p) * (1 + m)
    elif alloc == "power":
        # sum p^k + (1-p)^k is decreasing in k on (0, 1]
        k = _bisect(
            lambda k: p**k + (1 - p) ** k,
            np.full_like(p, 1e-3),
            np.ones_like(p),
            np.full_like(p, 1 + m),
            False,
        )
        qy, qn = p**k, (1 - p) ** k
    elif alloc == "odds_ratio":
        # sum increases as c falls from 1 to 0
        c = _bisect(
            lambda c: p / (p + c * (1 - p)) + (1 - p) / ((1 - p) + c * p),
            np.full_like(p, 1e-4), np.ones_like(p), np.full_like(p, 1 + m), False,
        )  # fmt: skip
        qy, qn = p / (p + c * (1 - p)), (1 - p) / ((1 - p) + c * p)
    else:
        raise ValueError(alloc)
    return np.minimum(qy, Q_CAP), np.minimum(qn, Q_CAP)


# ----------------------------------------------------------------------------- evaluation


@dataclass(frozen=True)
class Config:
    name: str
    rule: str = "appear"  # appear | start
    alloc: str = "proportional"
    offer: bool = False  # drop the rotation band
    cap: bool = False  # shorten quoted odds to the chosen cap


CONFIGS = [
    Config("base"),
    Config("power", alloc="power"),
    Config("odds_ratio", alloc="odds_ratio"),
    Config("start", rule="start"),
    Config("start+power", rule="start", alloc="power"),
    Config("start+odds_ratio", rule="start", alloc="odds_ratio"),
    Config("offer", offer=True),
    Config("cap", cap=True),
    Config("start+offer", rule="start", offer=True),
    Config("start+cap", rule="start", cap=True),
    Config("offer+cap", offer=True, cap=True),
    Config("start+offer+cap", rule="start", offer=True, cap=True),
    Config("start+power+offer+cap", rule="start", alloc="power", offer=True, cap=True),
]


def _arrays(
    d: pd.DataFrame, cfg: Config, control: bool
) -> tuple[FloatArray, FloatArray, NDArray[np.bool_]]:
    if cfg.rule == "appear":
        p_o = d["p_o"].to_numpy(float)
        p_l = d["p_o2" if control else "p_l"].to_numpy(float)
        settled = d["appeared"].to_numpy(bool)
    else:
        p_o = d["p_o_s"].to_numpy(float)
        p_l = (d["p_o2_s"] if control else d["p_l"]).to_numpy(float)
        settled = d["is_starter"].to_numpy(bool)
    return np.clip(p_o, CLIP, 1 - CLIP), np.clip(p_l, CLIP, 1 - CLIP), settled


def run_config(
    d: pd.DataFrame,
    cfg: Config,
    m: float,
    cap_odds: float,
    control: bool,
    ci: bool,
    seed: int,
    boots: dict[int, Boot],
) -> dict[str, Any]:
    """Informed expected and realised profit per offered proposition for one configuration."""
    p_o, p_l, settled = _arrays(d, cfg, control)
    y = d["y"].to_numpy(float)
    offered = np.ones(len(d), dtype=bool)
    if cfg.offer:
        offered &= (d["band"] != "rotation").to_numpy()
    ok = offered & settled & np.isfinite(p_o) & np.isfinite(p_l)
    p_o = np.where(np.isfinite(p_o), p_o, 0.5)
    p_l = np.where(np.isfinite(p_l), p_l, 0.5)
    qy, qn = quoted(p_o, m, cfg.alloc)
    short_y = np.zeros(len(d), dtype=bool)
    short_n = np.zeros(len(d), dtype=bool)
    if cfg.cap:
        short_y, short_n = 1 / qy > cap_odds, 1 / qn > cap_odds
        qy, qn = (
            np.minimum(np.maximum(qy, 1 / cap_odds), Q_CAP),
            np.minimum(np.maximum(qn, 1 / cap_odds), Q_CAP),
        )
    bet_y, bet_n = ok & (p_l > qy), ok & ((1 - p_l) > qn)
    exp = np.where(bet_y, p_l / qy - 1, 0.0) + np.where(bet_n, (1 - p_l) / qn - 1, 0.0)
    real = np.where(bet_y, y / qy - 1, 0.0) + np.where(bet_n, (1 - y) / qn - 1, 0.0)
    n_off = float(offered.sum())
    res: dict[str, Any] = {
        "offered": int(n_off),
        "offer_retained": n_off / len(d),
        "selections_left_at_original_quote": float(
            1 - 0.5 * ((short_y & offered).sum() + (short_n & offered).sum()) / max(n_off, 1)
        ),
        "share_selections_shortened": float(
            0.5 * ((short_y & offered).sum() + (short_n & offered).sum()) / max(n_off, 1)
        ),
        "bets": float(bet_y.sum() + bet_n.sum()),
        "informed_expected_per_offered": float(exp.sum() / max(n_off, 1)),
        "informed_realised_per_offered": float(real.sum() / max(n_off, 1)),
    }
    if ci:
        cl = d["sb_match_id"].to_numpy()
        den = offered.astype(float)
        res["informed_expected_per_offered_ci"] = _ratio_ci(exp, den, cl, boots, seed)
        res["informed_realised_per_offered_ci"] = _ratio_ci(real, den, cl, boots, seed)
    res["_exp_by_row"] = exp  # removed before writing
    return res


def per_match_profit(
    d: pd.DataFrame, cfg: Config, m: float, cap_odds: float, mask: NDArray[np.bool_] | None = None
) -> tuple[float, float]:
    r = run_config(d if mask is None else d[mask], cfg, m, cap_odds, False, False, 0, {})
    sub = d if mask is None else d[mask]
    exp = r["_exp_by_row"]
    g = pd.DataFrame({"m": sub["sb_match_id"].to_numpy(), "e": exp}).groupby("m")["e"].sum()
    return float(g.mean()), float(len(g))


def _strip(r: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in r.items() if not k.startswith("_")}


# ----------------------------------------------------------------------------- runner


def run_phase6b(cfg: dict[str, Any] | None = None, reuse: bool = False) -> Path:
    cfg = cfg or load_config("data")
    p5, p6 = load_config("phase5"), load_config("phase6")
    n_sims, seed = int(p5["n_sims"]), int(cfg["seed"])
    edges = [float(x) for x in p6["pillar_c"]["bands"]]
    limit = float(p6["pillar_c"]["stake_limit_loss_per_match"])
    ctx = load_context(cfg)
    model = _model(ctx)
    theta = 0.0  # D-049: frailty not adopted
    d_t, sha_t = collect_cached(cfg, ctx, "tune", theta, n_sims, seed, model, edges, reuse)
    d_e, sha_e = collect_cached(cfg, ctx, "test", theta, n_sims, seed, model, edges, reuse)
    boots: dict[int, Boot] = {}
    base, start = CONFIGS[0], CONFIGS[3]

    # ---- 1. settlement rule chosen on the tuning window (10% proportional)
    i_app = run_config(d_t, base, 0.10, NO_CAP, False, False, seed, boots)[
        "informed_expected_per_offered"
    ]
    i_sta = run_config(d_t, start, 0.10, NO_CAP, False, False, seed, boots)[
        "informed_expected_per_offered"
    ]
    chosen_rule = "start" if i_sta < i_app else "appear"

    # ---- 2. odds cap chosen on the tuning window under the chosen rule (D-040)
    rule_cfg = Config("cap_tuning", rule=chosen_rule, cap=True)

    def loss(c: float, lam: float = LAMBDA) -> float:
        r = run_config(d_t, rule_cfg, 0.10, c, False, False, seed, boots)
        return float(r["informed_expected_per_offered"] + lam * r["share_selections_shortened"])

    tuned: Tuned = tune_interior(
        "odds_cap",
        loss,
        CAP_GRID,
        lower_bound=1.5,
        upper_bound=NO_CAP,
        widen_low=lambda x: x * 0.7,
        widen_high=lambda x: x * 2.0,
    )
    cap_c = require_tuned(tuned)
    sens: dict[str, float] = {}
    for lam in (0.02, 0.10):
        t2 = tune_interior(
            f"odds_cap_lambda{lam}",
            partial(loss, lam=lam),
            CAP_GRID,
            lower_bound=1.5,
            upper_bound=NO_CAP,
            widen_low=lambda x: x * 0.7,
            widen_high=lambda x: x * 2.0,
        )
        sens[f"lambda_{lam}"] = require_tuned(t2)

    # ---- 3. recommended combination chosen on the tuning window (10% overround)
    def policy_loss(c: Config) -> float:
        r = run_config(d_t, c, 0.10, cap_c, False, False, seed, boots)
        return float(
            r["informed_expected_per_offered"]
            + LAMBDA * (1 - r["offer_retained"])
            + LAMBDA * r["share_selections_shortened"]
        )

    tune_scores = {c.name: policy_loss(c) for c in CONFIGS}
    recommended = min(tune_scores, key=lambda k: tune_scores[k])

    # ---- 4. evaluation, once, on the test window
    results: dict[str, Any] = {}
    for m in OVERROUNDS:
        for c in CONFIGS:
            r = run_config(d_e, c, m, cap_c, False, True, seed, boots)
            ctl = run_config(d_e, c, m, cap_c, True, True, seed, boots)
            results[f"{c.name}|{m:g}"] = {
                "config": c.__dict__, "overround": m, **_strip(r),
                "null_control": {k: v for k, v in _strip(ctl).items() if k in ("informed_expected_per_offered", "informed_expected_per_offered_ci", "bets")},
            }  # fmt: skip
    # ---- 5. stake limits recomputed under the chosen settlement rule and the base rule
    stake: dict[str, Any] = {
        "loss_limit_per_match": limit,
        "overround": 0.10,
        "alloc": "proportional",
        "rules": {},
    }
    cells = {
        "pooled": None,
        **{f"band:{b}": ("band", b) for b in ("rotation", "likely", "nailed")},
        **{f"market:{mk}": ("market", mk) for mk in MARKETS},
    }
    for rname, c in (("appear", base), ("start", start)):
        stake["rules"][rname] = {}
        for cell, spec in cells.items():
            mt = np.ones(len(d_t), bool) if spec is None else (d_t[spec[0]] == spec[1]).to_numpy()
            me = np.ones(len(d_e), bool) if spec is None else (d_e[spec[0]] == spec[1]).to_numpy()
            e_t, _ = per_match_profit(d_t, c, 0.10, NO_CAP, mt)
            e_e, _ = per_match_profit(d_e, c, 0.10, NO_CAP, me)
            if e_t > 0:
                s_max = limit / e_t
                stake["rules"][rname][cell] = {
                    "expected_informed_profit_per_match_tune_at_unit_stake": e_t, "max_stake": s_max,
                    "test_expected_per_match_at_unit_stake": e_e, "test_expected_per_match_at_max_stake": s_max * e_e,
                }  # fmt: skip
    prov = provenance(CMD, cfg, ctx.version)
    prov["collected_at_git_sha"] = {"tune": sha_t, "test": sha_e}
    out = {
        "provenance": prov,
        "labels": ["SIMULATION", "UPPER BOUND"],
        "tuning_window": {
            "settlement_rule_informed_expected_per_offered_at_10pct": {
                "appear": i_app,
                "start": i_sta,
            },
            "chosen_settlement_rule": chosen_rule,
            "odds_cap": tuned.as_record(),
            "chosen_cap_odds": cap_c,
            "lambda": LAMBDA,
            "cap_sensitivity_to_lambda": sens,
            "policy_loss_by_config_10pct": tune_scores,
            "recommended_combination": recommended,
        },  # fmt: skip
        "test": results,
        "stake_limits": stake,
    }
    write_json(resolve_path(cfg, "metrics_dir") / "phase6b_levers.json", out)
    _figure(results, recommended)
    _register(cfg, ctx.version, results, recommended, chosen_rule, cap_c)
    return resolve_path(cfg, "metrics_dir") / "phase6b_levers.json"


def _figure(results: dict[str, Any], recommended: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(11, 8), sharey=True)
    for ax, m in zip(axes.ravel(), OVERROUNDS, strict=True):
        for c in CONFIGS:
            r = results[f"{c.name}|{m:g}"]
            e = r["informed_expected_per_offered_ci"]
            colour = "tab:red" if c.rule == "appear" else "tab:blue"
            ax.errorbar(
                r["offer_retained"],
                r["informed_expected_per_offered"],
                yerr=[
                    [r["informed_expected_per_offered"] - e["ci_low"]],
                    [e["ci_high"] - r["informed_expected_per_offered"]],
                ],
                fmt="o",
                color=colour,
                markersize=6 if c.name != recommended else 9,
                capsize=2,
            )
            ax.plot(
                r["offer_retained"],
                r["null_control"]["informed_expected_per_offered"],
                "kx",
                markersize=5,
            )
            ax.annotate(
                c.name,
                (r["offer_retained"], r["informed_expected_per_offered"]),
                fontsize=6,
                xytext=(3, 3),
                textcoords="offset points",
            )
        ax.set_title(f"overround {m:.0%} (SIMULATION, UPPER BOUND)", fontsize=9)
        ax.set_xlabel("share of the pre-lineup offer retained")
        ax.set_yscale("symlog", linthresh=1e-3)
        ax.set_ylabel("residual informed expected profit per offered proposition")
    axes[0, 0].plot([], [], "o", color="tab:red", label="void on appearance")
    axes[0, 0].plot([], [], "o", color="tab:blue", label="void if not starting")
    axes[0, 0].plot([], [], "kx", label="null control (noise floor)")
    axes[0, 0].legend(fontsize=7)
    fig.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES_DIR / "phase6b_levers.png", dpi=110)
    plt.close(fig)


def _register(
    cfg: dict[str, Any],
    version: str,
    results: dict[str, Any],
    recommended: str,
    rule: str,
    cap_c: float,
) -> None:
    recs = []
    for k, r in results.items():
        name, m = k.split("|")
        recs.append(make_record(
            f"phase6b::{name}::{m}", cfg, version, CMD, feature_set="props, informed pre-lineup bettor",
            model="lever_configuration", hyperparameters={**r["config"], "overround": float(m), "cap_odds": cap_c if r["config"]["cap"] else None},
            validation_window="player_2015_16: test mw20-38",
            metrics={x: r[x] for x in ("informed_expected_per_offered", "informed_realised_per_offered", "offer_retained", "informed_expected_per_offered_ci", "informed_realised_per_offered_ci")},
            calibration={}, notes="SIMULATION, UPPER BOUND (D-053, D-055); recommended = " + recommended + "; chosen settlement rule = " + rule,
            status="promoted" if name == recommended else "rejected",
        ))  # fmt: skip
    append_records(recs)
