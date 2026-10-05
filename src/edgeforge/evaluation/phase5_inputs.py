"""Phase 5 inputs: Phase 4 predictions turned into per-match simulator inputs, plus realised
outcomes for the 2015/16 test window (matchweeks 20-38).

Everything here is built from the Phase 4 cache (`data/processed/phase4_cache.pkl`, written by
`edgeforge phase4 run`) and the D-033 split; nothing is tuned here.
"""

import logging
import pickle
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from edgeforge.config import resolve_path
from edgeforge.evaluation.phase4_data import P4Data, load_p4
from edgeforge.evaluation.phase4_props import (
    lineup_inputs,
    opening_inputs,
    price_opening,
    price_rows,
)
from edgeforge.models.shots_model import ShotParams, player_quantities
from edgeforge.simulation.engine import N_MIN_COLS, MatchInputs

log = logging.getLogger(__name__)


@dataclass
class Phase5Context:
    d: P4Data
    params: ShotParams
    l_bar: float
    base_cfg: tuple[int, float]
    version: str
    lineups: pd.DataFrame  # Phase 4 lineup rows, test window
    opening: pd.DataFrame  # Phase 4 opening candidate rows, test window
    shots: pd.DataFrame
    dc: pd.DataFrame  # per sb_match_id: lam_h, lam_a, rho
    scores: pd.DataFrame  # sb_match_id, home_score, away_score
    cache_sha: str


def load_context(cfg: dict[str, Any]) -> Phase5Context:
    path = resolve_path(cfg, "processed_dir") / "phase4_cache.pkl"
    if not path.exists():
        raise FileNotFoundError("run `uv run edgeforge phase4 run` first (needs phase4_cache.pkl)")
    with path.open("rb") as fh:
        cache = pickle.load(fh)
    d = load_p4(cfg)
    params: ShotParams = cache["params"]
    l_bar = float(cache["l_bar"])
    lineups = lineup_inputs(d, cache["mp"], cache["shots"], "test", l_bar)
    opening = opening_inputs(d, cache["mp"], cache["start_pred"], cache["shots"], "test", l_bar)
    link = d.con.execute("SELECT sb_match_id, fd_match_id FROM sb_match_link").df()
    pr = pd.read_parquet(resolve_path(cfg, "processed_dir") / "team_model_preds.parquet")
    pr = pr[(pr["block"] == "team_2015_16") & (pr["test"] == "all_2015_16")]
    dc = link.merge(
        pr.rename(columns={"match_id": "fd_match_id"})[["fd_match_id", "lam_h", "lam_a", "rho"]],
        on="fd_match_id",
    ).drop(columns="fd_match_id")
    scores = d.con.execute("SELECT sb_match_id, home_score, away_score FROM sb_matches").df()
    return Phase5Context(
        d, params, l_bar, cache["base_cfg"], cache["version"], lineups, opening, cache["shots"],
        dc, scores, cache["git_sha"],
    )  # fmt: skip


def canonical_order(idx: np.ndarray, pid: np.ndarray) -> np.ndarray:
    """Players in a fixed order (by StatsBomb id) so a simulation is reproducible bit for bit
    whatever order the database returns rows in."""
    return idx[np.argsort(pid[idx], kind="stable")]


def _dummy_minutes(n: int, l_bar: float) -> tuple[np.ndarray, np.ndarray]:
    w = np.zeros((n, N_MIN_COLS))
    w[:, 0] = 1.0
    return w, np.full((n, N_MIN_COLS), l_bar)


def _team_mu(shots: pd.DataFrame) -> dict[tuple[int, bool], float]:
    s = shots.dropna(subset=["mu"]).drop_duplicates(["sb_match_id", "is_home"])
    return {
        (int(m), bool(h)): float(v)
        for m, h, v in zip(s["sb_match_id"], s["is_home"], s["mu"], strict=True)
    }


def build_inputs(
    ctx: Phase5Context, state: str
) -> tuple[dict[int, MatchInputs], pd.DataFrame, dict[str, np.ndarray]]:
    """Per-match simulator inputs for one state, the aligned player rows and standalone prices."""
    rows = ctx.lineups if state == "lineups" else ctx.opening
    q = player_quantities(rows, ctx.params)
    rho_sot = (q["bb_alpha"] / (q["bb_alpha"] + q["bb_beta"])).to_numpy(float)
    g = q["g_shot"].to_numpy(float)
    sot_cond = np.clip((rho_sot - g) / np.clip(1.0 - g, 1e-6, None), 0.0, 1.0)
    omega = q["omega"].to_numpy(float)
    n = len(rows)
    if state == "lineups":
        w, gr = rows.attrs["W"], rows.attrs["G"]
        starter = rows["is_starter"].to_numpy(bool)
        dw, dg = _dummy_minutes(n, ctx.l_bar)
        ws = np.where(starter[:, None], w, dw)
        gs = np.where(starter[:, None], gr, dg)
        wb = np.where(starter[:, None], dw, w)
        gb = np.where(starter[:, None], dg, gr)
        p_start = starter.astype(float)
        q_app = np.where(starter, 0.0, 1.0 - rows["p_never"].fillna(1.0).to_numpy(float))
        prices = price_rows(rows, ctx.params, ctx.l_bar, full=True)
        p_appear = np.where(starter, 1.0, q_app)
        sampled = False
    else:
        ws, gs, wb, gb = (rows.attrs[k] for k in ("Ws", "Gs", "Wb", "Gb"))
        p_start = rows["p_start"].to_numpy(float)
        p_bench = rows["p_bench"].to_numpy(float)
        q_app = np.clip(
            p_bench
            * rows["p_appear_if_bench"].to_numpy(float)
            / np.clip(1.0 - p_start, 1e-6, None),
            0.0,
            1.0,
        )
        prices = price_opening(rows, ctx.params, ctx.l_bar, full=True)
        p_appear = p_start + p_bench * rows["p_appear_if_bench"].to_numpy(float)
        sampled = True
    prices = dict(prices)
    prices["_p_appear"] = p_appear
    mu = _team_mu(ctx.shots)
    dcd = {
        int(i): (float(a), float(b), float(c))
        for i, a, b, c in zip(
            ctx.dc["sb_match_id"], ctx.dc["lam_h"], ctx.dc["lam_a"], ctx.dc["rho"], strict=True
        )
    }
    finite = np.isfinite(omega) & np.isfinite(g) & np.isfinite(rho_sot)
    out: dict[int, MatchInputs] = {}
    mid = rows["sb_match_id"].to_numpy()
    is_home = rows["is_home"].astype(bool).to_numpy()
    pid = rows["player_id"].to_numpy(np.int64)
    for m in np.unique(mid):
        idx = canonical_order(np.flatnonzero((mid == m) & finite), pid)
        m = int(m)
        if m not in dcd or (m, True) not in mu or (m, False) not in mu:
            continue
        team = np.where(is_home[idx], 0, 1)
        if (team == 0).sum() == 0 or (team == 1).sum() == 0:
            continue
        out[m] = MatchInputs(
            match_id=m, state=state,
            lam_h=dcd[m][0], lam_a=dcd[m][1], rho=dcd[m][2],
            mu_h=mu[(m, True)], mu_a=mu[(m, False)],
            player_id=pid[idx], team=team, omega=omega[idx], g_shot=g[idx], sot_cond=sot_cond[idx],
            p_start=p_start[idx], q_app_bench=q_app[idx],
            ws=ws[idx], gs=gs[idx], wb=wb[idx], gb=gb[idx], sampled_roles=sampled,
            row_index=idx,
        )  # fmt: skip
    return out, rows, prices


def fit_shots_given_goals(d: P4Data, shots: pd.DataFrame) -> pd.DataFrame:
    """Tuning-window training rows for the conditional team-shot model (burn-in and tune matches
    only; test matches never enter)."""
    sql = """
    SELECT m.sb_match_id, pm.is_home, sum(pm.shots) AS shots, sum(pm.goals) AS player_goals,
           any_value(m.home_score) AS hs, any_value(m.away_score) AS aws,
           sum(pm.own_goals) AS own_goals_by_team
    FROM sb_matches m JOIN player_match pm USING (sb_match_id) GROUP BY m.sb_match_id, pm.is_home
    """
    t = d.con.execute(sql).df()
    t["is_home"] = t["is_home"].astype(bool)
    own_by = t.pivot(index="sb_match_id", columns="is_home", values="own_goals_by_team")
    opp = own_by.rename(columns={True: "opp_home", False: "opp_away"})
    credited = np.where(
        t["is_home"], opp.loc[t["sb_match_id"], "opp_away"], opp.loc[t["sb_match_id"], "opp_home"]
    )
    t["own_goals_credited"] = credited.astype(float)
    t["g_own"] = np.where(t["is_home"], t["hs"], t["aws"]).astype(float)
    t["g_opp"] = np.where(t["is_home"], t["aws"], t["hs"]).astype(float)
    mu = shots[["sb_match_id", "is_home", "mu", "split"]].copy()
    mu["is_home"] = mu["is_home"].astype(bool)
    t = t.merge(mu, on=["sb_match_id", "is_home"])
    train = t[t["split"].isin(["tune"]) & t["mu"].notna()].copy()
    return train


def realised_players(rows: pd.DataFrame) -> dict[int, dict[int, tuple[bool, int, int, int]]]:
    """match -> player -> (appeared, shots, sot, goals)."""
    out: dict[int, dict[int, tuple[bool, int, int, int]]] = {}
    app = rows["appeared"].fillna(False).astype(bool).to_numpy()
    sh = rows["shots"].fillna(0).to_numpy(int)
    so = rows["sot"].fillna(0).to_numpy(int)
    go = rows["goals"].fillna(0).to_numpy(int)
    for m, p, a, s, t, g in zip(
        rows["sb_match_id"], rows["player_id"], app, sh, so, go, strict=True
    ):
        out.setdefault(int(m), {})[int(p)] = (bool(a), int(s), int(t), int(g))
    return out
