"""Pillar C (D-016, D-029, D-051): lineup information shock and the informed-bettor simulation.

Everything here is an UPPER BOUND (lineups assumed known with certainty at T-60 min) and the
informed-bettor results are a SIMULATION, not observed betting. The specification (propositions,
bands, margin rule, families) is D-051, committed before any result existed.
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.common import FIGURES_DIR, write_json
from edgeforge.evaluation.phase4_props import MARKETS
from edgeforge.evaluation.phase5 import (
    TEMPLATE_META,
    evaluate_instance_outcome,
    rank_players,
    simulated_marginals,
    template_instances,
)
from edgeforge.evaluation.phase5_inputs import (
    Phase5Context,
    build_inputs,
    load_context,
    realised_players,
)
from edgeforge.evaluation.phase5_run import _model
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.stats import Boot, benjamini_hochberg, summarize
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
CMD = "edgeforge phase6 run"
N_BOOT = 1000
BANDS = ("rotation", "likely", "nailed")
MIN_EFF = 200
CLIP = 1e-4
COLLECT_SHA: dict[str, str] = {}


def _num(d: dict[str, object], key: str) -> float:
    v = d[key]
    assert isinstance(v, int | float)
    return float(v)


def band_of(p_start: float, edges: list[float]) -> str:
    return "rotation" if p_start < edges[0] else "likely" if p_start <= edges[1] else "nailed"


# ----------------------------------------------------------------------------- collection


def collect(
    ctx: Phase5Context,
    split: str,
    theta: float,
    n_sims: int,
    seed: int,
    model: ShotsGivenGoals,
    edges: list[float],
    control_seed: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Props and SGA propositions with opening and lineups probabilities and realised outcomes.

    `control_seed`: if given, also simulate the opening state a second time with that seed and
    store its probabilities as `p_o2`, the null control for Monte Carlo noise (same information,
    independent draws)."""
    in_o, rows_o, prices_o = build_inputs(ctx, "opening", split)
    in_l, rows_l, _ = build_inputs(ctx, "lineups", split)
    s_unc = prices_o["_p_appear"] * prices_o["anytime_scorer"]
    real_l = realised_players(rows_l)
    scores = {
        int(m): (int(h), int(a))
        for m, h, a in zip(
            ctx.scores["sb_match_id"],
            ctx.scores["home_score"],
            ctx.scores["away_score"],
            strict=True,
        )
    }
    props: list[dict[str, Any]] = []
    sgas: list[dict[str, Any]] = []
    for m in sorted(set(in_o) & set(in_l)):
        if m not in scores:
            continue
        io, il = in_o[m], in_l[m]
        ro = simulate(io, model, n_sims, seed, theta)
        rl = simulate(il, model, n_sims, seed, theta)
        r2 = simulate(io, model, n_sims, control_seed, theta) if control_seed is not None else None
        mo, ml = simulated_marginals(ro), simulated_marginals(rl)
        m2 = simulated_marginals(r2) if r2 is not None else None
        pos_l = {int(p): i for i, p in enumerate(il.player_id)}
        rl_m = real_l.get(m, {})
        for i, pid in enumerate(io.player_id):
            j = pos_l.get(int(pid))
            if j is None:
                continue
            app, shots, sot, goals = rl_m.get(int(pid), (False, 0, 0, 0))
            outc = {"shots_1plus": shots >= 1, "shots_2plus": shots >= 2, "shots_3plus": shots >= 3,
                    "sot_1plus": sot >= 1, "sot_2plus": sot >= 2, "anytime_scorer": goals >= 1}  # fmt: skip
            band = band_of(float(io.p_start[i]), edges)
            for mk in MARKETS:
                props.append({
                    "sb_match_id": m, "player_id": int(pid), "market": mk, "band": band,
                    "p_start_open": float(io.p_start[i]), "appeared": bool(app),
                    "p_o": float(mo[mk][i]), "p_l": float(ml[mk][j]),
                    "p_o2": float(m2[mk][i]) if m2 is not None else np.nan, "y": float(outc[mk]),
                })  # fmt: skip
        rank = rank_players(io, s_unc[io.row_index])
        tmap_o = {int(p): int(t) for p, t in zip(io.player_id, io.team, strict=True)}
        tmap_l = {int(p): int(t) for p, t in zip(il.player_id, il.team, strict=True)}
        pstart = {int(p): float(s) for p, s in zip(io.player_id, io.p_start, strict=True)}
        for tpl, tag, sga in template_instances(rank):
            try:
                validate_sga(sga, tmap_o)
            except SGAError:
                continue
            ind, ok = indicator_matrix(sga, ro)
            po = price_from_indicators(ind, ok, leg_ok_matrix(sga, ro))
            named = [int(leg.player_id) for leg in sga.legs if hasattr(leg, "player_id")]
            in_squad = all(p in pos_l for p in named)
            p_l, n_l = float("nan"), 0
            if in_squad:
                try:
                    validate_sga(sga, tmap_l)
                    ind2, ok2 = indicator_matrix(sga, rl)
                    pl = price_from_indicators(ind2, ok2, leg_ok_matrix(sga, rl))
                    p_l, n_l = _num(pl, "joint"), int(_num(pl, "n_effective_sims"))
                except SGAError:
                    pass
            p_o2 = float("nan")
            if r2 is not None:
                ind3, ok3 = indicator_matrix(sga, r2)
                p_o2 = _num(price_from_indicators(ind3, ok3, leg_ok_matrix(sga, r2)), "joint")
            band = band_of(min((pstart[p] for p in named), default=1.0), edges)
            sgas.append({
                "sb_match_id": m, "template": tpl, "side": tag, "n_legs": TEMPLATE_META[tpl][0],
                "band": band, "p_o": _num(po, "joint"), "n_eff_o": int(_num(po, "n_effective_sims")),
                "p_l": p_l, "n_eff_l": n_l, "p_o2": p_o2,
                "y": evaluate_instance_outcome(sga, scores[m], real_l.get(m, {})) if in_squad else float("nan"),
            })  # fmt: skip
    return pd.DataFrame(props), pd.DataFrame(sgas)


def usable(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Propositions that can be priced and settled: both probabilities exist, the player(s)
    appeared (D-030) and, for SGAs, enough effective simulations in both states."""
    d = df.copy()
    for c in ("p_o", "p_l", "p_o2"):
        if c in d:
            d[c] = d[c].clip(CLIP, 1 - CLIP)
    ok = d["p_o"].notna() & d["p_l"].notna() & d["y"].notna()
    if kind == "props":
        ok &= d["appeared"]
    else:
        ok &= (d["n_eff_o"] >= MIN_EFF) & (d["n_eff_l"] >= MIN_EFF)
    return d[ok].reset_index(drop=True)


# ----------------------------------------------------------------------------- bootstrap


def _ratio_ci(
    num: FloatArray, den: FloatArray, cluster: NDArray[Any], boot_cache: dict[int, Boot], seed: int
) -> dict[str, float]:
    _, inv = np.unique(cluster, return_inverse=True)
    n = int(inv.max()) + 1 if len(inv) else 0
    if n < 5 or den.sum() <= 0:
        return {"estimate": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "p": 1.0}
    if n not in boot_cache:
        boot_cache[n] = Boot.make(n, N_BOOT, seed)
    b = boot_cache[n]
    nn = np.bincount(inv, num, minlength=n)
    dd = np.bincount(inv, den, minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        draws = (b.counts @ nn) / (b.counts @ dd)
    return summarize(float(nn.sum() / dd.sum()), draws, 0.0)


# ----------------------------------------------------------------------------- shock


def _q(a: FloatArray) -> dict[str, float]:
    qs = np.percentile(a, [5, 25, 50, 75, 95])
    return {
        "n": float(len(a)),
        "mean": float(a.mean()),
        "q05": qs[0],
        "q25": qs[1],
        "q50": qs[2],
        "q75": qs[3],
        "q95": qs[4],
    }


def shock_stats(d: pd.DataFrame) -> dict[str, Any]:
    p_o, p_l = d["p_o"].to_numpy(float), d["p_l"].to_numpy(float)
    dlogit = np.log(p_l / (1 - p_l)) - np.log(p_o / (1 - p_o))
    rel = p_o / p_l - 1.0  # relative change in fair decimal odds, opening -> lineups
    return {
        "n": int(len(d)),
        "delta_log_odds": _q(dlogit),
        "relative_odds_change": _q(rel),
        "mean_abs_delta_log_odds": float(np.abs(dlogit).mean()),
        "share_abs_odds_change_gt_10pct": float((np.abs(rel) > 0.10).mean()),
        "share_abs_odds_change_gt_25pct": float((np.abs(rel) > 0.25).mean()),
        "share_abs_odds_change_gt_50pct": float((np.abs(rel) > 0.50).mean()),
    }


def voi_diff(d: pd.DataFrame) -> tuple[FloatArray, NDArray[Any]]:
    y = d["y"].to_numpy(float)
    return M.log_loss_binary(d["p_o"].to_numpy(float), y) - M.log_loss_binary(
        d["p_l"].to_numpy(float), y
    ), d["sb_match_id"].to_numpy()


def value_of_information(
    d: pd.DataFrame, group: str, keys: list[str], seed: int, cache: dict[int, Boot]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in keys:
        sub = d[d[group] == k]
        if len(sub) < 30:
            continue
        diff, cl = voi_diff(sub)
        out[k] = {
            "n": int(len(sub)),
            "log_loss_opening": float(
                M.log_loss_binary(sub["p_o"].to_numpy(float), sub["y"].to_numpy(float)).mean()
            ),
            "log_loss_lineups": float(
                M.log_loss_binary(sub["p_l"].to_numpy(float), sub["y"].to_numpy(float)).mean()
            ),
            "opening_minus_lineups": _ratio_ci(diff, np.ones(len(diff)), cl, cache, seed),
        }
    return out


# ----------------------------------------------------------------------------- informed bettor


def bets(
    p_o: FloatArray, p_l: FloatArray, y: FloatArray, m: float | FloatArray, two_way: bool
) -> dict[str, FloatArray]:
    """Per-proposition informed-bettor quantities at margin m (unit stakes)."""
    q_yes = np.minimum(p_o * (1 + m), 0.9999)
    bet_y = p_l > q_yes
    exp_y = p_l / q_yes - 1.0
    real_y = y / q_yes - 1.0
    if two_way:
        q_no = np.minimum((1 - p_o) * (1 + m), 0.9999)
        bet_n = (1 - p_l) > q_no
        exp_n = (1 - p_l) / q_no - 1.0
        real_n = (1 - y) / q_no - 1.0
    else:
        bet_n = np.zeros(len(p_o), dtype=bool)
        exp_n = real_n = np.zeros(len(p_o))
    n_bets = bet_y.astype(float) + bet_n.astype(float)
    return {
        "n_bets": n_bets,
        "exp": np.where(bet_y, exp_y, 0.0) + np.where(bet_n, exp_n, 0.0),
        "real": np.where(bet_y, real_y, 0.0) + np.where(bet_n, real_n, 0.0),
    }


def informed_curve(
    d: pd.DataFrame,
    margins: list[float],
    two_way: bool,
    seed: int,
    cache: dict[int, Boot],
    p_l_col: str = "p_l",
    full_ci: bool = True,
) -> list[dict[str, Any]]:
    out = []
    cl = d["sb_match_id"].to_numpy()
    p_o, p_l, y = d["p_o"].to_numpy(float), d[p_l_col].to_numpy(float), d["y"].to_numpy(float)
    one = np.ones(len(d))
    for m in margins:
        b = bets(p_o, p_l, y, m, two_way)
        nb = float(b["n_bets"].sum())
        rec: dict[str, Any] = {
            "margin": m,
            "offered": int(len(d)),
            "bets": nb,
            "bet_rate": nb / max(len(d), 1),
            "informed_expected_per_bet": float(b["exp"].sum() / nb) if nb else float("nan"),
            "book_expected_pnl_per_unit_staked": float(-b["exp"].sum() / nb)
            if nb
            else float("nan"),
            "informed_expected_per_offered": float(b["exp"].sum() / max(len(d), 1)),
            "informed_realised_per_bet": float(b["real"].sum() / nb) if nb else float("nan"),
            "informed_realised_per_offered": float(b["real"].sum() / max(len(d), 1)),
        }
        if full_ci:
            rec["informed_expected_per_offered_ci"] = _ratio_ci(b["exp"], one, cl, cache, seed)
            rec["informed_realised_per_offered_ci"] = _ratio_ci(b["real"], one, cl, cache, seed)
            rec["informed_realised_per_bet_ci"] = _ratio_ci(b["real"], b["n_bets"], cl, cache, seed)
            rec["book_expected_pnl_per_unit_staked_ci"] = _ratio_ci(
                -b["exp"], b["n_bets"], cl, cache, seed
            )
        out.append(rec)
    return out


def neutralising_margin(curve: list[dict[str, Any]], eps: float) -> float:
    for r in curve:
        if r["informed_expected_per_offered"] <= eps:
            return float(r["margin"])
    return float("nan")


def margin_grid(p6: dict[str, Any]) -> list[float]:
    step, top = float(p6["margin_step"]), float(p6["margin_max"])
    return [round(x * step, 6) for x in range(int(round(top / step)) + 1)]


# ----------------------------------------------------------------------------- analysis


def _cells_props(d: pd.DataFrame) -> dict[str, pd.DataFrame]:
    cells: dict[str, pd.DataFrame] = {"props|all": d}
    for mk in MARKETS:
        cells[f"props|{mk}"] = d[d["market"] == mk]
    for b in BANDS:
        cells[f"props|band:{b}"] = d[d["band"] == b]
    for mk in MARKETS:
        for b in BANDS:
            cells[f"props|{mk}|{b}"] = d[(d["market"] == mk) & (d["band"] == b)]
    return cells


def _cells_sga(d: pd.DataFrame) -> dict[str, pd.DataFrame]:
    cells: dict[str, pd.DataFrame] = {"sga|all": d}
    for t in TEMPLATE_META:
        cells[f"sga|{t}"] = d[d["template"] == t]
    for n in (2, 3, 4, 5):
        cells[f"sga|legs:{n}"] = d[d["n_legs"] == n]
    for b in BANDS:
        cells[f"sga|band:{b}"] = d[d["band"] == b]
    return cells


def policy_eval(
    d: pd.DataFrame, margin: FloatArray, two_way: bool, seed: int, cache: dict[int, Boot]
) -> dict[str, Any]:
    p_o, p_l, y = d["p_o"].to_numpy(float), d["p_l"].to_numpy(float), d["y"].to_numpy(float)
    b = bets(p_o, p_l, y, margin, two_way)
    cl = d["sb_match_id"].to_numpy()
    one = np.ones(len(d))
    return {
        "offered": int(len(d)),
        "bets": float(b["n_bets"].sum()),
        "informed_expected_per_offered": float(b["exp"].sum() / len(d)),
        "informed_expected_per_offered_ci": _ratio_ci(b["exp"], one, cl, cache, seed),
        "informed_realised_per_offered": float(b["real"].sum() / len(d)),
        "informed_realised_per_offered_ci": _ratio_ci(b["real"], one, cl, cache, seed),
        "mean_margin_charged": float(np.mean(margin)),
    }


def per_match_expected(d: pd.DataFrame, m: float, two_way: bool) -> tuple[float, float]:
    """Mean over matches of the informed bettor's expected and realised profit at unit stakes."""
    b = bets(d["p_o"].to_numpy(float), d["p_l"].to_numpy(float), d["y"].to_numpy(float), m, two_way)
    g = (
        pd.DataFrame({"m": d["sb_match_id"].to_numpy(), "e": b["exp"], "r": b["real"]})
        .groupby("m")[["e", "r"]]
        .sum()
    )
    return float(g["e"].mean()), float(g["r"].mean())


def _collect_cached(
    cfg: dict[str, Any],
    ctx: Phase5Context,
    split: str,
    theta: float,
    n_sims: int,
    seed: int,
    model: ShotsGivenGoals,
    edges: list[float],
    reuse: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """collect() with an on-disk cache (git-ignored) so analysis changes need no re-simulation.
    The cache records the git sha of the collection code and is only read when `reuse` is set."""
    import pickle

    path = resolve_path(cfg, "processed_dir") / f"phase6_collected_{split}.pkl"
    key = {"theta": theta, "n_sims": n_sims, "seed": seed, "phase4_cache": ctx.cache_sha}
    if reuse and path.exists():
        with path.open("rb") as fh:
            saved = pickle.load(fh)
        if saved["key"] == key:
            log.info("reusing collected %s frames made at git sha %s", split, saved["git_sha"])
            COLLECT_SHA[split] = saved["git_sha"]
            return saved["props"], saved["sga"]
    props, sga = collect(ctx, split, theta, n_sims, seed, model, edges, control_seed=seed + 1)
    from edgeforge.provenance import git_sha

    COLLECT_SHA[split] = git_sha()
    with path.open("wb") as fh:
        pickle.dump({"key": key, "git_sha": COLLECT_SHA[split], "props": props, "sga": sga}, fh)
    return props, sga


def shock_set(raw: pd.DataFrame, kind: str) -> pd.DataFrame:
    d = raw.copy()
    for c in ("p_o", "p_l"):
        d[c] = d[c].clip(CLIP, 1 - CLIP)
    d = d[d["p_o"].notna() & d["p_l"].notna()]
    if kind == "sga":
        d = d[(d["n_eff_o"] >= MIN_EFF) & (d["n_eff_l"] >= MIN_EFF)]
    return d


def run_pillar_c(cfg: dict[str, Any] | None = None, reuse: bool = False) -> Path:
    import json

    cfg = cfg or load_config("data")
    p5, p6 = load_config("phase5"), load_config("phase6")
    pc = p6["pillar_c"]
    n_sims, seed = int(p5["n_sims"]), int(cfg["seed"])
    fr = json.loads(
        (resolve_path(cfg, "metrics_dir") / "phase6_frailty.json").read_text(encoding="utf-8")
    )
    theta = float(fr["decision"]["theta_evaluated"]) if fr["decision"]["adopted"] else 0.0
    ctx = load_context(cfg)
    model = _model(ctx)
    version = ctx.version
    edges = [float(x) for x in pc["bands"]]
    margins = margin_grid(pc)
    ref = [float(x) for x in pc["reference_margins"]]
    eps = float(pc["epsilon"])
    limit = float(pc["stake_limit_loss_per_match"])
    cache: dict[int, Boot] = {}

    log.info("collecting tuning window (theta=%s)", theta)
    pt_raw, gt_raw = _collect_cached(cfg, ctx, "tune", theta, n_sims, seed, model, edges, reuse)
    log.info("collecting test window")
    pe_raw, ge_raw = _collect_cached(cfg, ctx, "test", theta, n_sims, seed, model, edges, reuse)
    P_t, G_t = usable(pt_raw, "props"), usable(gt_raw, "sga")
    P, G = usable(pe_raw, "props"), usable(ge_raw, "sga")
    P["mb"] = P["market"] + "|" + P["band"]

    # ---- shock (all in-squad propositions; appearance is not required for a pre-match quantity)
    S_p, S_g = shock_set(pe_raw, "props"), shock_set(ge_raw, "sga")
    shock: dict[str, Any] = {"label": "UPPER BOUND (D-016, D-029)", "props": {}, "sga": {}}
    for mk in MARKETS:
        shock["props"][mk] = {"all": shock_stats(S_p[S_p["market"] == mk])}
        for b in BANDS:
            sub = S_p[(S_p["market"] == mk) & (S_p["band"] == b)]
            if len(sub) >= 30:
                shock["props"][mk][b] = shock_stats(sub)
    shock["props"]["by_band"] = {b: shock_stats(S_p[S_p["band"] == b]) for b in BANDS}
    shock["sga"]["by_template"] = {
        t: shock_stats(S_g[S_g["template"] == t])
        for t in TEMPLATE_META
        if (S_g["template"] == t).sum() >= 30
    }
    shock["sga"]["by_leg_count"] = {
        str(n): shock_stats(S_g[S_g["n_legs"] == n])
        for n in (2, 3, 4, 5)
        if (S_g["n_legs"] == n).sum() >= 30
    }
    shock["sga"]["by_band"] = {
        b: shock_stats(S_g[S_g["band"] == b]) for b in BANDS if (S_g["band"] == b).sum() >= 30
    }
    shock["counts"] = {
        "props_in_squad": int(len(S_p)),
        "props_settled": int(len(P)),
        "sga_priced_both_states": int(len(S_g)),
        "sga_settled": int(len(G)),
        "sga_void_named_player_not_in_lineups_squad": int(ge_raw["p_l"].isna().sum()),
    }

    # ---- value of information
    voi_m = value_of_information(P, "market", list(MARKETS), seed, cache)
    voi_t = value_of_information(G, "template", list(TEMPLATE_META), seed, cache)
    keys_v = [(f"props|{k}", v) for k, v in voi_m.items()] + [
        (f"sga|{k}", v) for k, v in voi_t.items()
    ]
    qv, kv = benjamini_hochberg([v["opening_minus_lineups"]["p"] for _, v in keys_v], 0.10)
    for (_, v), q, kk in zip(keys_v, qv, kv, strict=True):
        v["q_bh"], v["survives_bh_10pct"] = q, bool(kk)
    voi_b = value_of_information(P, "mb", sorted(P["mb"].unique()), seed, cache)
    qb, kb = benjamini_hochberg([v["opening_minus_lineups"]["p"] for v in voi_b.values()], 0.10)
    for v, q, kk in zip(voi_b.values(), qb, kb, strict=True):
        v["q_bh"], v["survives_bh_10pct"] = q, bool(kk)
    voi = {
        "family_V": {
            "markets": voi_m,
            "templates": voi_t,
            "n_tests": len(keys_v),
            "survivors": [k for (k, _), kk in zip(keys_v, kv, strict=True) if kk],
        },
        "family_B_market_x_band": voi_b,
        "note": "difference = log loss(opening price) - log loss(lineups price); positive means the lineups price is better",
    }

    # ---- informed bettor on the test window, all cells
    informed: dict[str, Any] = {"label": "SIMULATION", "margin_grid": margins, "cells": {}}
    test_cells = {**_cells_props(P), **_cells_sga(G)}
    for name, sub in test_cells.items():
        if len(sub) < 30:
            continue
        curve = informed_curve(sub, margins, name.startswith("props"), seed, cache)
        informed["cells"][name] = {
            "offered": int(len(sub)),
            "curve": curve,
            "neutralising_margin_expected": neutralising_margin(curve, eps),
            "reference": {
                f"{m:g}": next(r for r in curve if abs(r["margin"] - m) < 1e-9) for m in ref
            },
        }
    pk = [
        k
        for k in informed["cells"]
        if k.startswith("props|") and k.count("|") == 1 and k.split("|")[1] in MARKETS
    ]
    pk += [
        k for k in informed["cells"] if k.startswith("sga|") and k.split("|")[1] in TEMPLATE_META
    ]
    pp = [
        informed["cells"][k]["reference"]["0.05"]["informed_realised_per_offered_ci"]["p"]
        for k in pk
    ]
    qp, kp = benjamini_hochberg(pp, 0.10)
    for k, q, kk in zip(pk, qp, kp, strict=True):
        informed["cells"][k]["family_P_q_bh_at_5pct"], informed["cells"][k]["family_P_survives"] = (
            q,
            bool(kk),
        )
    informed["family_P"] = {
        "n_tests": len(pk),
        "survivors": [k for k, kk in zip(pk, kp, strict=True) if kk],
    }
    # null control: same information, independent simulation draws
    control: dict[str, Any] = {
        "label": "null control: lineups probabilities replaced by an independent re-simulation of the opening state",
        "cells": {},
    }
    Pc, Gc = P[P["p_o2"].notna()], G[G["p_o2"].notna()]
    ctl = [("props|all", Pc, True), ("sga|all", Gc, False)] + [
        (f"props|band:{b}", Pc[Pc["band"] == b], True) for b in BANDS
    ]
    for name, sub, two in ctl:
        c = informed_curve(sub, margins, two, seed, cache, p_l_col="p_o2", full_ci=False)
        control["cells"][name] = {
            "curve": c,
            "neutralising_margin_noise_floor": neutralising_margin(c, eps),
        }

    # ---- policy fitted on the tuning window, evaluated once on test
    # D-051 grid first (0 to 60%); when no margin on it reaches epsilon the cell is reported as
    # "not reached" and a labelled POST-HOC extended grid (configs/phase6.yaml) searches further.
    ext = [
        round(i * float(pc["extended_step"]), 6)
        for i in range(
            int(round(float(pc["extended_margin_max"]) / float(pc["extended_step"]))) + 1
        )
    ]
    cap = ext[-1]
    mstar: dict[str, float] = {}
    mstar_ext: dict[str, float] = {}
    tune_cells = {**_cells_props(P_t), **_cells_sga(G_t)}
    for name, sub in tune_cells.items():
        if len(sub) < 30:
            continue
        two = name.startswith("props")
        mstar[name] = neutralising_margin(
            informed_curve(sub, margins, two, seed, cache, full_ci=False), eps
        )
        mstar_ext[name] = neutralising_margin(
            informed_curve(sub, ext, two, seed, cache, full_ci=False), eps
        )
    prop_cells = [
        f"props|{mk}|{b}" for mk in MARKETS for b in BANDS if f"props|{mk}|{b}" in mstar_ext
    ]

    def _m(d: dict[str, float], key: str, default: float) -> float:
        v = d.get(key, np.nan)
        return default if not np.isfinite(v) else v  # unreached even on the extended grid: cap

    flat_pooled = _m(mstar_ext, "props|all", cap)
    flat_worst = float(max(_m(mstar_ext, c, cap) for c in prop_cells))
    sga_flat = _m(mstar_ext, "sga|all", cap)
    # realised-profit checks of the margin-capped variants use the pre-registered 60% ceiling
    margin_ceiling = margins[-1]
    ext_test = {
        name: neutralising_margin(
            informed_curve(sub, ext, name.startswith("props"), seed, cache, full_ci=False), eps
        )
        for name, sub in test_cells.items()
        if len(sub) >= 30
    }
    policies: dict[str, Any] = {
        "label": "ILLUSTRATIVE", "fitted_on": "tuning window mw10-19", "epsilon": eps,
        "neutralising_margins_tune_preregistered_grid": mstar,
        "neutralising_margins_tune": mstar_ext,
        "neutralising_margins_test_extended_grid": ext_test,
        "extended_grid_note": "POST-HOC: the D-051 grid (0-60%) is too short for most cells; margins above 60% come from the extended grid; a cell still unreached at the cap is set to the cap",
        "extended_grid_cap": cap, "preregistered_grid_ceiling": margin_ceiling,
        "flat_pooled_props": flat_pooled, "flat_worst_cell_props": flat_worst, "flat_pooled_sga": sga_flat,
    }  # fmt: skip
    diff_margin = np.array(
        [
            _m(mstar_ext, f"props|{mk}|{b}", flat_pooled)
            for mk, b in zip(P["market"], P["band"], strict=True)
        ],
        dtype=float,
    )
    ev: dict[str, Any] = {
        "props_no_margin": policy_eval(P, np.zeros(len(P)), True, seed, cache),
        "props_flat_5pct": policy_eval(P, np.full(len(P), 0.05), True, seed, cache),
        "props_flat_pooled": policy_eval(P, np.full(len(P), flat_pooled), True, seed, cache),
        "props_flat_worst_cell": policy_eval(P, np.full(len(P), flat_worst), True, seed, cache),
        "props_differentiated": policy_eval(P, diff_margin, True, seed, cache),
        "props_differentiated_capped_at_60pct": policy_eval(
            P, np.minimum(diff_margin, margin_ceiling), True, seed, cache
        ),
        "props_flat_60pct": policy_eval(P, np.full(len(P), margin_ceiling), True, seed, cache),
    }
    band_arr = P["band"].to_numpy()
    ev["props_margin_charged_by_band"] = {
        pol: {b: float(m[band_arr == b].mean()) for b in BANDS}
        for pol, m in (
            ("differentiated", diff_margin),
            ("flat_pooled", np.full(len(P), flat_pooled)),
            ("flat_worst_cell", np.full(len(P), flat_worst)),
        )
    }
    sdiff = np.array([_m(mstar_ext, f"sga|{t}", sga_flat) for t in G["template"]], dtype=float)
    ev["sga_flat_5pct"] = policy_eval(G, np.full(len(G), 0.05), False, seed, cache)
    ev["sga_flat_pooled"] = policy_eval(G, np.full(len(G), sga_flat), False, seed, cache)
    ev["sga_differentiated_by_template"] = policy_eval(G, sdiff, False, seed, cache)
    policies["evaluation_on_test"] = ev
    stake: dict[str, Any] = {"loss_limit_per_match": limit, "margin": 0.05, "cells": {}}
    for name, sub_t in tune_cells.items():
        sub_e = test_cells.get(name)
        if len(sub_t) < 30 or sub_e is None or len(sub_e) < 30:
            continue
        two = name.startswith("props")
        e_t, _ = per_match_expected(sub_t, 0.05, two)
        if e_t <= 0:
            continue
        s_max = limit / e_t
        e_e, r_e = per_match_expected(sub_e, 0.05, two)
        stake["cells"][name] = {
            "expected_informed_profit_per_match_tune_at_unit_stake": e_t, "max_stake": s_max,
            "test_expected_per_match_at_max_stake": s_max * e_e, "test_realised_per_match_at_max_stake": s_max * r_e,
        }  # fmt: skip
    policies["stake_limit"] = stake

    prov = provenance(CMD, cfg, version)
    prov["frailty_theta"] = theta
    prov["collected_at_git_sha"] = dict(COLLECT_SHA)
    out = {
        "provenance": prov,
        "shock": shock,
        "value_of_information": voi,
        "informed_bettor": informed,
        "null_control": control,
        "policy": policies,
    }
    write_json(resolve_path(cfg, "metrics_dir") / "phase6_pillar_c.json", out)
    _figures(S_p, informed, control, policies)
    _register(cfg, version, voi, informed, policies, theta, eps)
    return resolve_path(cfg, "metrics_dir") / "phase6_pillar_c.json"


# ----------------------------------------------------------------------------- figures, registry


def _figures(
    S_p: pd.DataFrame, informed: dict[str, Any], control: dict[str, Any], policies: dict[str, Any]
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 3, figsize=(11, 6), sharey=True)
    for ax, mk in zip(axes.ravel(), MARKETS, strict=True):
        data, labels = [], []
        for b in BANDS:
            sub = S_p[(S_p["market"] == mk) & (S_p["band"] == b)]
            if len(sub) >= 30:
                p_o, p_l = sub["p_o"].to_numpy(float), sub["p_l"].to_numpy(float)
                data.append(np.log(p_l / (1 - p_l)) - np.log(p_o / (1 - p_o)))
                labels.append(f"{b}\n(n={len(sub)})")
        ax.boxplot(data, tick_labels=labels, showfliers=False)
        ax.axhline(0, color="grey", linestyle="--", linewidth=1)
        ax.set_title(mk, fontsize=9)
    axes[0, 0].set_ylabel("change in log-odds, opening to lineups")
    fig.suptitle("Lineup shock by player type (UPPER BOUND)", fontsize=10)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "phase6_shock_by_band_market.png", dpi=110)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    mg = informed["margin_grid"]
    for ax, names, title in (
        (axes[0], [f"props|band:{b}" for b in BANDS] + ["props|all"], "props, by player type"),
        (axes[1], [f"sga|legs:{n}" for n in (2, 3, 4, 5)] + ["sga|all"], "SGAs, by leg count"),
    ):
        for n in names:
            if n in informed["cells"]:
                ax.plot(
                    mg,
                    [r["informed_expected_per_offered"] for r in informed["cells"][n]["curve"]],
                    label=n.split("|")[1],
                )
        ctl = control["cells"].get("props|all" if "props" in names[0] else "sga|all")
        if ctl:
            ax.plot(
                mg,
                [r["informed_expected_per_offered"] for r in ctl["curve"]],
                "k:",
                label="noise control",
            )
        ax.set_xlabel("pre-lineup margin")
        ax.set_ylabel("informed expected profit per offered proposition")
        ax.set_title(title + " (SIMULATION)", fontsize=9)
        ax.legend(fontsize=7)
        ax.set_yscale("symlog", linthresh=1e-3)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "phase6_informed_profit_vs_margin.png", dpi=110)
    plt.close(fig)

    ms = policies["neutralising_margins_tune"]
    mat = np.array([[ms.get(f"props|{mk}|{b}", np.nan) for b in BANDS] for mk in MARKETS])
    fig, ax = plt.subplots(figsize=(5.2, 3.8))
    im = ax.imshow(mat, cmap="viridis", aspect="auto")
    ax.set_xticks(range(len(BANDS)), BANDS)
    ax.set_yticks(range(len(MARKETS)), MARKETS, fontsize=7)
    for i in range(len(MARKETS)):
        for j in range(len(BANDS)):
            if np.isfinite(mat[i, j]):
                ax.text(
                    j, i, f"{mat[i, j]:.0%}", ha="center", va="center", color="white", fontsize=8
                )
    fig.colorbar(im, ax=ax, label="neutralising margin")
    ax.set_title("Illustrative policy: margin that neutralises the informed bettor", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "phase6_policy_heatmap.png", dpi=110)
    plt.close(fig)


def _register(
    cfg: dict[str, Any],
    version: str,
    voi: dict[str, Any],
    informed: dict[str, Any],
    policies: dict[str, Any],
    theta: float,
    eps: float,
) -> None:
    recs = []
    wnd = "player_2015_16: test mw20-38"
    for grp in ("markets", "templates"):
        for k, v in voi["family_V"][grp].items():
            recs.append(make_record(
                f"phase6::voi::{k}", cfg, version, CMD, feature_set="opening vs lineups prices (simulator)",
                model="simulator_d014", hyperparameters={"theta": theta}, validation_window=wnd,
                metrics={"opening_minus_lineups_log_loss": v["opening_minus_lineups"], "q_bh": v["q_bh"], "n": v["n"]},
                calibration={}, notes="Pillar C value of information (D-051 family V); UPPER BOUND.",
                status="promoted" if v["survives_bh_10pct"] and v["opening_minus_lineups"]["estimate"] > 0 else "rejected",
            ))  # fmt: skip
    for k in informed["family_P"]["survivors"] + [
        k
        for k in informed["cells"]
        if k.startswith(("props|", "sga|"))
        and "family_P_survives" in informed["cells"][k]
        and not informed["cells"][k]["family_P_survives"]
    ]:
        c = informed["cells"][k]
        recs.append(make_record(
            f"phase6::informed::{k}", cfg, version, CMD, feature_set="informed bettor at 5% pre-lineup margin",
            model="informed_bettor_simulation", hyperparameters={"margin": 0.05, "theta": theta}, validation_window=wnd,
            metrics={"realised_per_offered_at_5pct": c["reference"]["0.05"]["informed_realised_per_offered_ci"], "q_bh": c["family_P_q_bh_at_5pct"]},
            calibration={}, notes="SIMULATION (D-051 family P).",
            status="promoted" if c["family_P_survives"] else "rejected",
        ))  # fmt: skip
    for name, m in (
        ("differentiated", None),
        ("flat_pooled", policies["flat_pooled_props"]),
        ("flat_worst_cell", policies["flat_worst_cell_props"]),
    ):
        e = policies["evaluation_on_test"]["props_differentiated" if m is None else f"props_{name}"]
        recs.append(make_record(
            f"phase6::policy::{name}", cfg, version, CMD, feature_set="pre-lineup margin policy fitted on mw10-19",
            model="margin_policy", hyperparameters={"flat_margin": m, "epsilon": eps}, validation_window=wnd,
            metrics={k: e[k] for k in ("informed_expected_per_offered", "informed_realised_per_offered", "mean_margin_charged")},
            calibration={}, notes="ILLUSTRATIVE policy, evaluated once on test.", status="promoted" if name == "differentiated" else "rejected",
        ))  # fmt: skip
    append_records(recs)
