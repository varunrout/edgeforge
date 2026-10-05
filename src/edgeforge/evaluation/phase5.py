"""Phase 5: simulator validation, convergence, Comparison B and SGA joint validation (D-047)."""

import logging
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.common import binary_metrics
from edgeforge.evaluation.phase4 import cluster_diff_summary
from edgeforge.evaluation.phase4_props import MARKETS, THRESH, compare_markets, outcome_arrays
from edgeforge.evaluation.phase5_inputs import Phase5Context
from edgeforge.evaluation.stats import benjamini_hochberg
from edgeforge.models.dixon_coles import dc_grid
from edgeforge.sga.engine import (
    BTTS,
    SGA,
    PlayerAGS,
    PlayerShots,
    PlayerSoT,
    TeamResult,
    Total,
    indicator_matrix,
    leg_ok_matrix,
    price_from_indicators,
)
from edgeforge.simulation.engine import MatchInputs, ShotsGivenGoals, SimResult, simulate

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]

TEMPLATE_META: dict[str, tuple[int, str]] = {
    "T01": (2, "player vs team result, same side"),
    "T02": (2, "player vs team result, opposing"),
    "T03": (2, "player vs total"),
    "T04": (3, "opponents + BTTS"),
    "T05": (2, "teammates"),
    "T06": (2, "same player"),
    "T07": (2, "player vs total"),
    "T08": (3, "mixed"),
    "T09": (4, "mixed"),
    "T10": (5, "mixed"),
    "T11": (4, "mixed, teammates"),
    "T12": (2, "player vs team result, same side"),
    "T13": (2, "opponents"),
}


# ----------------------------------------------------------------------------- invariants


def check_invariants(r: SimResult) -> dict[str, int]:
    bad = {
        "goal_accounting": 0,
        "goals_le_sot_le_shots": 0,
        "off_pitch_records": 0,
        "team_shot_sum": 0,
    }
    for t in (0, 1):
        idx = np.flatnonzero(r.inputs.team == t)
        if not (r.goals[:, idx].sum(axis=1) + r.own_goals[:, t] == r.team_goals[:, t]).all():
            bad["goal_accounting"] += 1
        if not (r.shots[:, idx].sum(axis=1) == r.team_shots[:, t]).all():
            bad["team_shot_sum"] += 1
    if not ((r.goals <= r.sot).all() and (r.sot <= r.shots).all()):
        bad["goals_le_sot_le_shots"] += 1
    off = ~r.on_pitch
    if (r.shots[off] != 0).any() or (r.sot[off] != 0).any() or (r.goals[off] != 0).any():
        bad["off_pitch_records"] += 1
    return bad


def team_marginal_z(r: SimResult) -> dict[str, float]:
    """z-scores of simulated team marginals against the exact Dixon-Coles grid."""
    i = r.inputs
    grid = dc_grid(np.array([i.lam_h]), np.array([i.lam_a]), i.rho, 10)[0]
    ii, jj = np.indices(grid.shape)
    g = r.team_goals
    n = r.n_sims
    exact = {
        "home_win": grid[ii > jj].sum(),
        "draw": grid[ii == jj].sum(),
        "away_win": grid[ii < jj].sum(),
        "btts": grid[(ii > 0) & (jj > 0)].sum(),
        "over_2.5": grid[ii + jj > 2].sum(),
        "over_1.5": grid[ii + jj > 1].sum(),
    }
    est = {
        "home_win": (g[:, 0] > g[:, 1]).mean(),
        "draw": (g[:, 0] == g[:, 1]).mean(),
        "away_win": (g[:, 0] < g[:, 1]).mean(),
        "btts": ((g > 0).all(axis=1)).mean(),
        "over_2.5": (g.sum(axis=1) > 2.5).mean(),
        "over_1.5": (g.sum(axis=1) > 1.5).mean(),
    }
    return {
        k: float((est[k] - exact[k]) / np.sqrt(max(exact[k] * (1 - exact[k]), 1e-12) / n))
        for k in exact
    }


def expected_player_goals_ratio(r: SimResult) -> dict[str, float]:
    """Mean simulated player goals per side over the Dixon-Coles team mean (coherence gap)."""
    i = r.inputs
    out = {}
    for t, lam, nm in ((0, i.lam_h, "home"), (1, i.lam_a, "away")):
        idx = np.flatnonzero(i.team == t)
        out[nm] = float(r.goals[:, idx].sum(axis=1).mean() / lam)
    return out


# ----------------------------------------------------------------------------- player marginals


def simulated_marginals(r: SimResult) -> dict[str, FloatArray]:
    """P(market | player appears) per player, from the simulation."""
    on = r.on_pitch
    cnt = on.sum(axis=0).astype(float)
    cnt_safe = np.where(cnt > 0, cnt, np.nan)
    out = {}
    for m, (col, thr) in THRESH.items():
        arr = {"shots": r.shots, "sot": r.sot, "goals": r.goals}[col]
        out[m] = ((arr >= thr) & on).sum(axis=0) / cnt_safe
    out["_p_appear"] = cnt / r.n_sims
    return out


# ----------------------------------------------------------------------------- templates


def rank_players(inp: MatchInputs, s_unconditional: FloatArray) -> dict[int, list[int]]:
    """D-047: per team, players ranked by unconditional AGS probability (ties: lower player id)."""
    out: dict[int, list[int]] = {}
    for t in (0, 1):
        idx = np.flatnonzero(inp.team == t)
        s = s_unconditional[idx]
        ok = np.isfinite(s)
        order = sorted(
            zip(idx[ok], s[ok], inp.player_id[idx][ok], strict=True), key=lambda x: (-x[1], x[2])
        )
        out[t] = [int(p) for _, _, p in order]
    return out


def template_instances(rank: dict[int, list[int]]) -> list[tuple[str, str, SGA]]:
    """All D-047 instances for one match: (template, side tag, SGA)."""
    inst: list[tuple[str, str, SGA]] = []
    res = {0: TeamResult(result="home"), 1: TeamResult(result="away")}
    for t, tag in ((0, "H"), (1, "A")):
        if not rank[t]:
            continue
        own, opp = rank[t], rank[1 - t]
        t1 = own[0]
        inst.append(("T01", tag, SGA(legs=[res[t], PlayerAGS(player_id=t1)])))
        if opp:
            inst.append(("T02", tag, SGA(legs=[res[t], PlayerAGS(player_id=opp[0])])))
        inst.append(("T03", tag, SGA(legs=[PlayerAGS(player_id=t1), Total(line=2.5, side="over")])))
        if len(own) > 1:
            t2 = own[1]
            inst.append(("T05", tag, SGA(legs=[PlayerAGS(player_id=t1), PlayerAGS(player_id=t2)])))
            inst.append(("T10", tag, SGA(legs=[
                res[t], Total(line=1.5, side="over"), PlayerAGS(player_id=t1),
                PlayerSoT(player_id=t1, k=2), PlayerShots(player_id=t2, k=1)])))  # fmt: skip
            inst.append(("T11", tag, SGA(legs=[
                res[t], PlayerAGS(player_id=t1), PlayerAGS(player_id=t2),
                Total(line=2.5, side="over")])))  # fmt: skip
        inst.append(("T06", tag, SGA(legs=[PlayerSoT(player_id=t1, k=2), PlayerAGS(player_id=t1)])))
        inst.append(
            ("T07", tag, SGA(legs=[Total(line=2.5, side="under"), PlayerAGS(player_id=t1)]))
        )
        inst.append(
            ("T08", tag, SGA(legs=[res[t], Total(line=2.5, side="over"), PlayerAGS(player_id=t1)]))
        )
        inst.append(("T12", tag, SGA(legs=[res[t], PlayerShots(player_id=t1, k=3)])))
    if rank[0] and rank[1]:
        h1, a1 = rank[0][0], rank[1][0]
        inst.append(
            (
                "T04",
                "HA",
                SGA(legs=[BTTS(side="yes"), PlayerAGS(player_id=h1), PlayerAGS(player_id=a1)]),
            )
        )
        inst.append(("T09", "HA", SGA(legs=[
            BTTS(side="yes"), Total(line=2.5, side="over"),
            PlayerAGS(player_id=h1), PlayerAGS(player_id=a1)])))  # fmt: skip
        inst.append(("T13", "HA", SGA(legs=[PlayerAGS(player_id=h1), PlayerAGS(player_id=a1)])))
    return inst


def realised_leg(
    leg: object, score: tuple[int, int], pl: dict[int, tuple[bool, int, int, int]]
) -> bool:
    h, a = score
    if isinstance(leg, TeamResult):
        return {"home": h > a, "draw": h == a, "away": h < a}[leg.result]
    if isinstance(leg, Total):
        return (h + a > leg.line) if leg.side == "over" else (h + a < leg.line)
    if isinstance(leg, BTTS):
        return (h > 0 and a > 0) if leg.side == "yes" else not (h > 0 and a > 0)
    _, shots, sot, goals = pl[leg.player_id]  # type: ignore[attr-defined]
    if isinstance(leg, PlayerAGS):
        return goals >= 1
    if isinstance(leg, PlayerShots):
        return shots >= leg.k
    if isinstance(leg, PlayerSoT):
        return sot >= leg.k
    raise TypeError(leg)


def evaluate_instance_outcome(
    sga: SGA, score: tuple[int, int], pl: dict[int, tuple[bool, int, int, int]]
) -> float:
    """1/0 joint outcome, NaN if any named player did not appear (void rule)."""
    for leg in sga.legs:
        pid = getattr(leg, "player_id", None)
        if pid is not None and (pid not in pl or not pl[pid][0]):
            return float("nan")
    return float(all(realised_leg(leg, score, pl) for leg in sga.legs))


# ----------------------------------------------------------------------------- joint evaluation


def _ll(p: FloatArray, y: FloatArray) -> FloatArray:
    return M.log_loss_binary(p, y)


def joint_validation(inst: pd.DataFrame, seed: int) -> dict[str, Any]:
    """D-047 tests: simulated joint vs naive product on realised joint outcomes."""
    out: dict[str, Any] = {"by_template_state": {}, "bh_family": {}}
    ps, keys = [], []
    for state in ("lineups", "opening"):
        for tpl in TEMPLATE_META:
            sub = inst[(inst["state"] == state) & (inst["template"] == tpl) & inst["y"].notna()]
            key = f"{tpl}|{state}"
            if len(sub) < 30:
                out["by_template_state"][key] = {"n": int(len(sub)), "note": "too few instances"}
                continue
            y = sub["y"].to_numpy(float)
            sim, nai = sub["p_sim"].to_numpy(float), sub["p_naive"].to_numpy(float)
            cl = sub["sb_match_id"].to_numpy()
            d_ll = _ll(sim, y) - _ll(nai, y)
            d_br = M.brier_binary(sim, y) - M.brier_binary(nai, y)
            s_ll = cluster_diff_summary(d_ll, cl, seed)
            s_br = cluster_diff_summary(d_br, cl, seed)
            out["by_template_state"][key] = {
                "template": tpl,
                "state": state,
                "n_instances": int(len(sub)),
                "n_matches": int(len(np.unique(cl))),
                "legs": TEMPLATE_META[tpl][0],
                "relationship": TEMPLATE_META[tpl][1],
                "event_rate": float(y.mean()),
                "mean_p_sim": float(sim.mean()),
                "mean_p_naive": float(nai.mean()),
                "simulated": binary_metrics(sim, y),
                "naive": binary_metrics(nai, y),
                "log_loss_diff_sim_minus_naive": s_ll,
                "brier_diff_sim_minus_naive": s_br,
            }
            ps.append(s_ll["p"])
            keys.append(key)
    q, keep = benjamini_hochberg(ps, 0.10)
    for k, qq, kk in zip(keys, q, keep, strict=True):
        out["by_template_state"][k]["q_bh"] = qq
        out["by_template_state"][k]["survives_bh_10pct"] = bool(kk)
        out["by_template_state"][k]["verdict"] = (
            "simulated better" if kk and out["by_template_state"][k]["log_loss_diff_sim_minus_naive"]["estimate"] < 0
            else "simulated worse" if kk
            else "not distinguishable"
        )  # fmt: skip
    out["bh_family"] = {
        "n_tests": len(ps),
        "fdr": 0.10,
        "survivors_better": [
            k for k, kk in zip(keys, keep, strict=True)
            if kk and out["by_template_state"][k]["log_loss_diff_sim_minus_naive"]["estimate"] < 0
        ],
        "survivors_worse": [
            k for k, kk in zip(keys, keep, strict=True)
            if kk and out["by_template_state"][k]["log_loss_diff_sim_minus_naive"]["estimate"] > 0
        ],
    }  # fmt: skip
    # pooled, by state and by leg count
    pooled: dict[str, Any] = {}
    for state in ("lineups", "opening"):
        for grp, col in (("all", None), ("2_legs", 2), ("3plus_legs", 3)):
            sub = inst[(inst["state"] == state) & inst["y"].notna()]
            if col == 2:
                sub = sub[sub["n_legs"] == 2]
            elif col == 3:
                sub = sub[sub["n_legs"] >= 3]
            if len(sub) < 30:
                continue
            y = sub["y"].to_numpy(float)
            sim, nai = sub["p_sim"].to_numpy(float), sub["p_naive"].to_numpy(float)
            pooled[f"{state}|{grp}"] = {
                "n": int(len(sub)),
                "simulated": binary_metrics(sim, y),
                "naive": binary_metrics(nai, y),
                "log_loss_diff_sim_minus_naive": cluster_diff_summary(
                    _ll(sim, y) - _ll(nai, y), sub["sb_match_id"].to_numpy(), seed
                ),
                "brier_diff_sim_minus_naive": cluster_diff_summary(
                    M.brier_binary(sim, y) - M.brier_binary(nai, y),
                    sub["sb_match_id"].to_numpy(),
                    seed,
                ),
                "reliability_simulated": M.reliability_bins(sim, y),
                "reliability_naive": M.reliability_bins(nai, y),
            }
    out["pooled"] = pooled
    return out


def dependence_ratios(inst: pd.DataFrame) -> dict[str, Any]:
    """Distribution of joint / naive by template, leg count and relationship (all instances,
    including those whose players did not appear: the ratio is a pre-match price property)."""
    v = inst[(inst["p_naive"] > 0) & (inst["p_sim"] > 0) & (inst["n_eff"] >= 200)].copy()
    v["ratio"] = v["p_sim"] / v["p_naive"]

    def q(s: pd.Series) -> dict[str, float]:
        qs = s.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
        return {
            "n": float(len(s)),
            "mean": float(s.mean()),
            **{
                f"q{pc:02d}": float(x)
                for pc, x in zip((5, 25, 50, 75, 95), qs.to_numpy(), strict=True)
            },
        }

    out: dict[str, Any] = {"by_template": {}, "by_leg_count": {}, "by_relationship": {}}
    for (tpl, state), g in v.groupby(["template", "state"]):
        out["by_template"][f"{tpl}|{state}"] = q(g["ratio"])
    for (n, state), g in v.groupby(["n_legs", "state"]):
        out["by_leg_count"][f"{n}|{state}"] = q(g["ratio"])
    for (rel, state), g in v.groupby(["relationship", "state"]):
        out["by_relationship"][f"{rel}|{state}"] = q(g["ratio"])
    return out


def curated_examples(inst: pd.DataFrame, k: int = 3) -> dict[str, Any]:
    v = inst[(inst["state"] == "lineups") & (inst["p_sim"] > 0.01) & (inst["n_eff"] >= 500)].copy()
    v["ratio"] = v["p_sim"] / v["p_naive"]
    cols = ["sb_match_id", "template", "side", "legs_json", "p_sim", "p_naive", "ratio", "n_eff"]

    def rec(df: pd.DataFrame) -> list[dict[str, Any]]:
        return [{c: (float(r[c]) if c in ("p_sim", "p_naive", "ratio") else r[c]) for c in cols}
                for _, r in df.iterrows()]  # fmt: skip

    near = v.iloc[(v["ratio"] - 1.0).abs().argsort()[:k]]
    return {
        "strongly_positive": rec(v.nlargest(k, "ratio")),
        "strongly_negative": rec(v.nsmallest(k, "ratio")),
        "near_independent": rec(near),
        "regenerate": "uv run edgeforge phase5 example --match-id <sb_match_id> --state lineups",
    }


# ----------------------------------------------------------------------------- comparison B


def comparison_b(
    ctx: Phase5Context,
    state: str,
    rows: pd.DataFrame,
    standalone: dict[str, FloatArray],
    sim_prices: dict[str, FloatArray],
    seed: int,
) -> dict[str, Any]:
    """Simulator player marginals vs the Phase 4 standalone prices on appearing players."""
    y = outcome_arrays(rows)
    ok = (
        rows["appeared"].fillna(False).astype(bool).to_numpy()
        & np.isfinite(sim_prices["shots_1plus"])
        & np.isfinite(standalone["shots_1plus"])
    )
    new = {m: np.clip(sim_prices[m], 1e-6, 1 - 1e-6) for m in MARKETS}
    base = {m: np.clip(standalone[m], 1e-6, 1 - 1e-6) for m in MARKETS}
    res = compare_markets(new, base, y, rows["sb_match_id"].to_numpy(), seed, ok)
    res["n_priced_rows"] = int(ok.sum())
    res["new"] = "simulator marginals (conditional on appearance)"
    res["baseline"] = "Phase 4 standalone models"
    return res


# ----------------------------------------------------------------------------- convergence


def convergence_study(
    inputs: dict[int, MatchInputs],
    rank_by_match: dict[int, dict[int, list[int]]],
    model: ShotsGivenGoals,
    grid: list[int],
    match_ids: list[int],
    seed: int,
    ref_n: int,
) -> dict[str, Any]:
    """Estimates and Monte Carlo SE against the number of simulations for a team market, a prop
    and 3- and 5-leg SGAs (T08 and T10, home side)."""
    quantities = ("team_home_win", "prop_top1_ags", "sga_3leg_T08", "sga_5leg_T10")
    rows: list[dict[str, Any]] = []
    t_run: dict[int, float] = {}
    for m in match_ids:
        inp, rk = inputs[m], rank_by_match[m]
        if len(rk[0]) < 2:
            continue
        t1, t2 = rk[0][0], rk[0][1]
        r_ = TeamResult(result="home")
        sgas = {
            "sga_3leg_T08": SGA(legs=[r_, Total(line=2.5, side="over"), PlayerAGS(player_id=t1)]),
            "sga_5leg_T10": SGA(legs=[
                r_, Total(line=1.5, side="over"), PlayerAGS(player_id=t1),
                PlayerSoT(player_id=t1, k=2), PlayerShots(player_id=t2, k=1)]),
        }  # fmt: skip
        ref: dict[str, float] = {}
        for n in [*grid, ref_n]:
            t0 = time.perf_counter()
            res = simulate(inp, model, n, seed)
            t_run[n] = t_run.get(n, 0.0) + (time.perf_counter() - t0)
            est: dict[str, tuple[float, float]] = {}
            hw = (res.team_goals[:, 0] > res.team_goals[:, 1]).mean()
            est["team_home_win"] = (float(hw), float(np.sqrt(hw * (1 - hw) / n)))
            j = res.player_index(t1)
            on = res.on_pitch[:, j]
            pa = float((res.goals[on, j] >= 1).mean()) if on.any() else float("nan")
            est["prop_top1_ags"] = (pa, float(np.sqrt(pa * (1 - pa) / max(int(on.sum()), 1))))
            for name, sga in sgas.items():
                ind, ok = indicator_matrix(sga, res)
                pr = price_from_indicators(ind, ok, leg_ok_matrix(sga, res))
                est[name] = (float(pr["joint"]), float(pr["joint_mc_se"]))  # type: ignore[arg-type]
            if n == ref_n:
                ref = {k: v[0] for k, v in est.items()}
                continue
            for k, (p, se) in est.items():
                rows.append({"match_id": m, "n": n, "quantity": k, "p": p, "se": se})
        for r in rows:
            if r["match_id"] == m and "ref" not in r:
                r["ref"] = ref.get(r["quantity"], float("nan"))
                r["abs_err_vs_ref"] = abs(r["p"] - r["ref"])
    df = pd.DataFrame(rows)
    summary: dict[str, Any] = {}
    for qn in quantities:
        sub = df[df["quantity"] == qn]
        summary[qn] = [
            {
                "n_sims": int(str(n)),
                "mean_p": float(g["p"].mean()),
                "mean_mc_se": float(g["se"].mean()),
                "mean_relative_se": float((g["se"] / g["p"].clip(lower=1e-9)).mean()),
                "mean_abs_error_vs_reference": float(g["abs_err_vs_ref"].mean()),
            }
            for n, g in sub.groupby("n")
        ]
    return {
        "reference_n_sims": ref_n,
        "n_matches": int(df["match_id"].nunique()),
        "summary": summary,
        "seconds_per_match_by_n": {
            int(n): t / max(df["match_id"].nunique(), 1) for n, t in t_run.items()
        },
    }


def timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    t0 = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t0
