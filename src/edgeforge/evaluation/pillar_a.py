"""Phase 3, Pillar A: market-efficiency map on real odds vs real outcomes (D-037), model vs market,
forecast encompassing, line movement toward the model, and the cold-start fix on the test block.

Claims are pre-registered in `build_claims` (segments from D-037). De-vig is proportional (D-036);
power and Shin are shown alongside for every market-dependent claim but are not part of the BH
family. Benjamini-Hochberg at 10% FDR is applied across all claims of a block. Anything that uses
football-data's early snapshot carries the D-024 label.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.optimize import minimize
from scipy.special import logsumexp

from edgeforge.config import load_config, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.evaluation import plots
from edgeforge.evaluation.baselines_team import outcomes
from edgeforge.evaluation.common import (
    FIGURES_DIR,
    connect,
    load_fd_clock,
    warehouse_version,
    write_json,
)
from edgeforge.evaluation.registry import append_records, make_record
from edgeforge.evaluation.segments import (
    EARLY_SNAPSHOT_LABEL,
    FLB_BANDS,
    add_segments,
    flb_band,
    load_promoted,
)
from edgeforge.evaluation.splits import load_split_ids
from edgeforge.evaluation.stats import (
    Boot,
    batched_logistic,
    benjamini_hochberg,
    summarize,
)
from edgeforge.evaluation.team_model import dc_probs, ev_frame
from edgeforge.pricing.devig import devig

log = logging.getLogger(__name__)
FloatArray = NDArray[np.float64]
METHODS = ("proportional", "power", "shin")
FDR = 0.10
N_BOOT = 2000
EPS = 1e-12
DISAGREE = 0.03


@dataclass
class Frame:
    name: str
    family: str
    bookmaker: str
    ev: pd.DataFrame
    y: NDArray[np.int64]
    close: dict[str, FloatArray]
    early: dict[str, FloatArray]
    model: FloatArray
    model_fix: FloatArray

    @property
    def n(self) -> int:
        return len(self.y)


def odds_1x2(
    con: duckdb.DuckDBPyConnection, ids: list[str], bookmaker: str, snapshot: str
) -> pd.DataFrame:
    con.register("pa_ids", pd.DataFrame({"id": ids}))
    return con.execute(
        """
        SELECT match_id,
               max(price) FILTER (WHERE selection = 'home') AS h,
               max(price) FILTER (WHERE selection = 'draw') AS d,
               max(price) FILTER (WHERE selection = 'away') AS a
        FROM odds
        WHERE market = '1X2' AND snapshot = ? AND bookmaker = ? AND match_id IN (SELECT id FROM pa_ids)
        GROUP BY match_id HAVING count(*) = 3
        """,
        [snapshot, bookmaker],
    ).df()


def build_frame(
    con: duckdb.DuckDBPyConnection,
    fd_all: pd.DataFrame,
    promoted: pd.DataFrame,
    ids: list[str],
    bookmaker: str,
    preds: pd.DataFrame,
    name: str,
    family: str,
) -> Frame:
    ev = add_segments(ev_frame(fd_all, ids), fd_all, promoted)
    y = outcomes(ev)["1x2"]

    def probs(snapshot: str) -> dict[str, FloatArray]:
        o = ev[["match_id"]].merge(odds_1x2(con, ids, bookmaker, snapshot), how="left")
        x = o[["h", "d", "a"]].to_numpy(float)
        ok = ~np.isnan(x).any(axis=1)
        out = {}
        for m in METHODS:
            p = np.full((len(ev), 3), np.nan)
            if ok.any():
                p[ok] = devig(x[ok], m)[0]
            out[m] = p
        return out

    p = preds[preds["match_id"].isin(set(ids))]
    model = dc_probs(p, ev)["1x2"]
    if "lam_h_fix" in p.columns and p["lam_h_fix"].notna().any():
        pf = p.rename(columns={"lam_h_fix": "lh", "lam_a_fix": "la", "rho_fix": "r"})
        pf = pf[["match_id", "lh", "la", "r"]].rename(
            columns={"lh": "lam_h", "la": "lam_a", "r": "rho"}
        )
        model_fix = dc_probs(pf, ev)["1x2"]
    else:
        model_fix = model
    return Frame(name, family, bookmaker, ev, y, probs("close"), probs("early"), model, model_fix)


# ------------------------------------------------------------------------ statistic helpers


def _onehot(y: NDArray[np.int64]) -> FloatArray:
    oh = np.zeros((len(y), 3))
    oh[np.arange(len(y)), y] = 1.0
    return oh


def _ratio(boot: Boot, num_m: FloatArray, den_m: FloatArray) -> tuple[float, FloatArray]:
    point = float(num_m.sum() / den_m.sum()) if den_m.sum() > 0 else float("nan")
    with np.errstate(invalid="ignore", divide="ignore"):
        draws = (boot.counts @ num_m) / (boot.counts @ den_m)
    return point, draws


def _mean_stat(boot: Boot, v: FloatArray, mask: NDArray[np.bool_]) -> tuple[float, FloatArray]:
    return _ratio(boot, np.where(mask, v, 0.0), mask.astype(float))


def _logit(p: FloatArray) -> FloatArray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    out: FloatArray = np.log(p / (1 - p))
    return out


def _slope_stat(
    boot: Boot, p: FloatArray, y_sel: FloatArray, rowmatch: NDArray[np.int64], ok: NDArray[np.bool_]
) -> tuple[float, FloatArray]:
    """Logistic slope of outcome on logit(p) over selection rows (clustered by match)."""
    x, yy, rm = _logit(p[ok]), y_sel[ok], rowmatch[ok]
    b0 = batched_logistic(x, yy, np.ones((1, len(x))))[0, 1]
    draws = batched_logistic(x, yy, boot.counts[:, rm])[:, 1]
    return float(b0), draws


def _ll_match(p: FloatArray, y: NDArray[np.int64]) -> FloatArray:
    return -np.log(np.clip(p[np.arange(len(y)), y], EPS, 1.0))


def _bll(p: FloatArray, y_sel: FloatArray) -> FloatArray:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return -(y_sel * np.log(p) + (1 - y_sel) * np.log(1 - p))


# ------------------------------------------------------------------------ encompassing


def _pool_probs(theta: FloatArray, lm: FloatArray, ld: FloatArray) -> FloatArray:
    a = np.array([0.0, theta[0], theta[1]])
    z = a[None, :] + theta[2] * lm + theta[3] * ld
    out: FloatArray = np.exp(z - logsumexp(z, axis=1, keepdims=True))
    return out


def fit_pool(pm: FloatArray, pd_: FloatArray, y: NDArray[np.int64]) -> FloatArray:
    lm, ld = np.log(np.clip(pm, EPS, 1)), np.log(np.clip(pd_, EPS, 1))

    def nll(th: FloatArray) -> float:
        p = _pool_probs(th, lm, ld)
        return float(
            -np.log(np.clip(p[np.arange(len(y)), y], EPS, 1)).mean() + 1e-4 * (th[:2] ** 2).sum()
        )

    res = minimize(nll, np.array([0.0, 0.0, 1.0, 0.0]), method="BFGS")
    return np.asarray(res.x)


def encompassing(
    tune: Frame | None, test: Frame, method: str, seed: int, n_refit: int = 200
) -> dict[str, Any] | None:
    if tune is None:
        return None
    ok_t = ~np.isnan(tune.close[method]).any(axis=1) & ~np.isnan(tune.model).any(axis=1)
    if ok_t.sum() < 200:
        return None
    th = fit_pool(tune.close[method][ok_t], tune.model[ok_t], tune.y[ok_t])
    rng = np.random.default_rng(seed)
    ws, bds = [], []
    idx_all = np.flatnonzero(ok_t)
    for _ in range(n_refit):
        s = rng.choice(idx_all, size=len(idx_all), replace=True)
        t = fit_pool(tune.close[method][s], tune.model[s], tune.y[s])
        bds.append(t[3])
        ws.append(t[3] / (t[2] + t[3]) if abs(t[2] + t[3]) > 1e-6 else np.nan)
    return {
        "method": method,
        "fitted_on_tuning_matches": int(ok_t.sum()),
        "theta": {
            "alpha_draw": th[0],
            "alpha_away": th[1],
            "beta_market": th[2],
            "beta_model": th[3],
        },
        "model_weight": float(th[3] / (th[2] + th[3])),
        "model_weight_ci95": [float(np.nanpercentile(ws, 2.5)), float(np.nanpercentile(ws, 97.5))],
        "beta_model_ci95": [float(np.percentile(bds, 2.5)), float(np.percentile(bds, 97.5))],
        "_theta": th,
    }


# ------------------------------------------------------------------------ claims


def _entry(
    cid: str, seg: str, desc: str, early: bool, null: float, point: float, draws: FloatArray, n: int
) -> dict[str, Any]:
    return {
        "id": cid,
        "segment": seg,
        "description": desc + (f" [{EARLY_SNAPSHOT_LABEL}]" if early else ""),
        "uses_early_snapshot": early,
        "null": null,
        "n": int(n),
        **summarize(point, draws, null),
    }


def build_claims(
    f: Frame, boot: Boot, method: str, enc: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """All pre-registered claims of one block for one de-vig method (D-037)."""
    n = f.n
    y = f.y
    oh = _onehot(y)
    pc, pe = f.close[method], f.early[method]
    ok_c = ~np.isnan(pc).any(axis=1)
    ok_e = ~np.isnan(pe).any(axis=1)
    both = ok_c & ok_e
    ok_m = ~np.isnan(f.model).any(axis=1)
    rc = np.where(ok_c[:, None], oh - np.nan_to_num(pc), 0.0)  # residual y - p, closing
    re = np.where(ok_e[:, None], oh - np.nan_to_num(pe), 0.0)
    out: list[dict[str, Any]] = []
    rowmatch = np.repeat(np.arange(n), 3)
    cls = np.tile(np.arange(3), n)

    def add(
        cid: str,
        seg: str,
        desc: str,
        early: bool,
        null: float,
        res: tuple[float, FloatArray],
        cnt: int,
    ) -> None:
        out.append(_entry(cid, seg, desc, early, null, res[0], res[1], cnt))

    # 1. league: home-win and draw bias of the closing market
    for lg in sorted(f.ev["league"].unique()):
        m = (f.ev["league"] == lg).to_numpy() & ok_c
        for k, nm in ((0, "home"), (1, "draw")):
            add(
                f"eff.league.{lg}.{nm}_bias",
                "league",
                f"League {lg}: mean(outcome - closing prob) for {nm}",
                False,
                0.0,
                _mean_stat(boot, rc[:, k], m),
                int(m.sum()),
            )
    # 2. season phase: early (either team in its first 6) minus rest
    early_phase = f.ev["early_phase"].to_numpy()
    for k, nm in ((0, "home"), (1, "draw")):
        ph_a = _mean_stat(boot, rc[:, k], early_phase & ok_c)
        ph_b = _mean_stat(boot, rc[:, k], ~early_phase & ok_c)
        add(
            f"eff.phase.{nm}_bias_diff",
            "season_phase",
            f"Season phase: {nm} bias, early-phase minus rest (closing)",
            False,
            0.0,
            (ph_a[0] - ph_b[0], ph_a[1] - ph_b[1]),
            int((early_phase & ok_c).sum()),
        )
    # 3. favourite-longshot: bands (closing and early) and logit slope
    for snap, P, R, ok, early in (("close", pc, rc, ok_c, False), ("early", pe, re, ok_e, True)):
        band = np.stack([flb_band(np.nan_to_num(P[:, k])) for k in range(3)], axis=1)
        for b, (lo, hi) in enumerate(FLB_BANDS):
            inb = (band == b) & ok[:, None]
            num = (R * inb).sum(axis=1)
            den = inb.sum(axis=1).astype(float)
            add(
                f"eff.flb.{snap}.band{b}_bias",
                "favourite_longshot",
                f"FLB {snap}: mean(outcome - implied) for selections with implied prob in [{lo:.2f}, {min(hi, 1):.2f})",
                early,
                0.0,
                _ratio(boot, num, den),
                int(inb.sum()),
            )
        okr = np.repeat(ok, 3)
        add(
            f"eff.flb.{snap}.slope",
            "favourite_longshot",
            f"FLB {snap}: logit slope of outcome on implied probability (1 = calibrated)",
            early,
            1.0,
            _slope_stat(boot, P.ravel(), oh.ravel(), rowmatch, okr),
            int(ok.sum()),
        )
    # 4. draw
    add(
        "eff.draw.close.bias",
        "draw",
        "Draw: mean(draw outcome - closing draw prob)",
        False,
        0.0,
        _mean_stat(boot, rc[:, 1], ok_c),
        int(ok_c.sum()),
    )
    add(
        "eff.draw.early.bias",
        "draw",
        "Draw: mean(draw outcome - early draw prob)",
        True,
        0.0,
        _mean_stat(boot, re[:, 1], ok_e),
        int(ok_e.sum()),
    )
    add(
        "eff.draw.close.slope",
        "draw",
        "Draw: logit slope of draw outcome on closing draw prob (1 = calibrated)",
        False,
        1.0,
        _slope_stat(boot, pc[:, 1], oh[:, 1], np.arange(n), ok_c),
        int(ok_c.sum()),
    )
    # 5. promoted teams: promoted team's win, first 10 matches vs later
    hp, ap = f.ev["home_promoted"].to_numpy(), f.ev["away_promoted"].to_numpy()
    hn, an = f.ev["home_n"].to_numpy(), f.ev["away_n"].to_numpy()
    rows_m = np.concatenate([np.flatnonzero(hp), np.flatnonzero(ap)])
    p_win = np.concatenate([np.nan_to_num(pc[hp, 0]), np.nan_to_num(pc[ap, 2])])
    y_win = np.concatenate([(y[hp] == 0).astype(float), (y[ap] == 2).astype(float)])
    first = np.concatenate([hn[hp] <= 10, an[ap] <= 10])
    okp = np.concatenate([ok_c[hp], ok_c[ap]])
    res_w = y_win - p_win

    def promoted_stat(sel: NDArray[np.bool_]) -> tuple[float, FloatArray]:
        s = sel & okp
        num = np.bincount(rows_m[s], res_w[s], minlength=n)
        den = np.bincount(rows_m[s], minlength=n).astype(float)
        return _ratio(boot, num, den)

    a1, a2 = promoted_stat(first), promoted_stat(~first)
    add(
        "eff.promoted.first10.win_bias",
        "promoted",
        "Promoted team's win: mean(outcome - closing prob), its first 10 matches",
        False,
        0.0,
        a1,
        int((first & okp).sum()),
    )
    add(
        "eff.promoted.later.win_bias",
        "promoted",
        "Promoted team's win: mean(outcome - closing prob), later matches",
        False,
        0.0,
        a2,
        int((~first & okp).sum()),
    )
    add(
        "eff.promoted.diff.win_bias",
        "promoted",
        "Promoted team's win bias: first 10 minus later",
        False,
        0.0,
        (a1[0] - a2[0], a1[1] - a2[1]),
        int(okp.sum()),
    )
    # 6. early snapshot versus close
    mv = np.where(both[:, None], np.nan_to_num(pc) - np.nan_to_num(pe), 0.0)
    r_e = np.where(both[:, None], oh - np.nan_to_num(pe), 0.0)
    add(
        "eff.snapshot.movement_slope",
        "snapshot",
        "Does the line move toward the outcome: slope of (outcome - early prob) on (close - early) prob (0 = no information in the move, 1 = closing fully efficient)",
        True,
        0.0,
        _ratio(boot, (mv * r_e).sum(axis=1), (mv * mv).sum(axis=1)),
        int(both.sum()),
    )
    dll = np.where(
        both,
        _ll_match(np.nan_to_num(pc, nan=1 / 3), y) - _ll_match(np.nan_to_num(pe, nan=1 / 3), y),
        0.0,
    )
    add(
        "eff.snapshot.logloss_close_minus_early",
        "snapshot",
        "Closing minus early log loss (negative = closing better calibrated)",
        True,
        0.0,
        _mean_stat(boot, dll, both),
        int(both.sum()),
    )

    # --- model vs market head-to-head (model log loss minus market log loss; negative = model better)
    both_m = ok_c & ok_m
    llm = np.where(
        both_m,
        _ll_match(np.nan_to_num(f.model, nan=1 / 3), y)
        - _ll_match(np.nan_to_num(pc, nan=1 / 3), y),
        0.0,
    )
    for lg in sorted(f.ev["league"].unique()):
        m = (f.ev["league"] == lg).to_numpy() & both_m
        add(
            f"mvm.league.{lg}",
            "mvm_league",
            f"Model minus closing-market log loss, league {lg}",
            False,
            0.0,
            _mean_stat(boot, llm, m),
            int(m.sum()),
        )
    add(
        "mvm.phase.early",
        "mvm_season_phase",
        "Model minus market log loss, early phase",
        False,
        0.0,
        _mean_stat(boot, llm, early_phase & both_m),
        int((early_phase & both_m).sum()),
    )
    add(
        "mvm.phase.rest",
        "mvm_season_phase",
        "Model minus market log loss, rest of season",
        False,
        0.0,
        _mean_stat(boot, llm, ~early_phase & both_m),
        int((~early_phase & both_m).sum()),
    )
    p1 = f.ev["promoted_first10"].to_numpy()
    pl = f.ev["promoted_later"].to_numpy()
    add(
        "mvm.promoted.first10",
        "mvm_promoted",
        "Model minus market log loss, promoted team's first 10 matches",
        False,
        0.0,
        _mean_stat(boot, llm, p1 & both_m),
        int((p1 & both_m).sum()),
    )
    add(
        "mvm.promoted.later",
        "mvm_promoted",
        "Model minus market log loss, promoted team later matches",
        False,
        0.0,
        _mean_stat(boot, llm, pl & both_m),
        int((pl & both_m).sum()),
    )
    sel_ll = _bll(np.nan_to_num(f.model, nan=1 / 3), oh) - _bll(np.nan_to_num(pc, nan=1 / 3), oh)
    band = np.stack([flb_band(np.nan_to_num(pc[:, k])) for k in range(3)], axis=1)
    for b, (lo, hi) in enumerate(FLB_BANDS):
        inb = (band == b) & both_m[:, None]
        add(
            f"mvm.flb.band{b}",
            "mvm_favourite_longshot",
            f"Model minus market selection log loss, closing implied prob in [{lo:.2f}, {min(hi, 1):.2f})",
            False,
            0.0,
            _ratio(boot, (sel_ll * inb).sum(axis=1), inb.sum(axis=1).astype(float)),
            int(inb.sum()),
        )
    d_only = np.where(both_m, sel_ll[:, 1], 0.0)
    add(
        "mvm.draw",
        "mvm_draw",
        "Model minus market log loss on the draw selection",
        False,
        0.0,
        _mean_stat(boot, d_only, both_m),
        int(both_m.sum()),
    )
    # --- forecast encompassing (weights fitted on the tuning window, evaluated here)
    if enc is not None:
        th = enc["_theta"]
        pp = np.full((n, 3), np.nan)
        pp[both_m] = _pool_probs(
            th, np.log(np.clip(pc[both_m], EPS, 1)), np.log(np.clip(f.model[both_m], EPS, 1))
        )
        dpool = np.where(
            both_m,
            _ll_match(np.nan_to_num(pp, nan=1 / 3), y) - _ll_match(np.nan_to_num(pc, nan=1 / 3), y),
            0.0,
        )
        add(
            "mvm.encompassing.test_delta",
            "mvm_encompassing",
            "Log loss of the model+market combination (weights fitted on the tuning window) minus market alone",
            False,
            0.0,
            _mean_stat(boot, dpool, both_m),
            int(both_m.sum()),
        )
    # --- line movement toward the model
    bm = both & ok_m
    d = np.where(bm[:, None], np.nan_to_num(f.model) - np.nan_to_num(pe), 0.0)
    mv2 = np.where(bm[:, None], np.nan_to_num(pc) - np.nan_to_num(pe), 0.0)
    add(
        "mvm.linemove.slope",
        "mvm_line_movement",
        "Does the close move toward the model: slope of (close - early) on (model - early) probability",
        True,
        0.0,
        _ratio(boot, (mv2 * d).sum(axis=1), (d * d).sum(axis=1)),
        int(bm.sum()),
    )
    # --- cold-start fix (promoted team, first 10), fix minus no fix
    okf = ~np.isnan(f.model_fix).any(axis=1) & ok_m
    dfix = np.where(
        okf,
        _ll_match(np.nan_to_num(f.model_fix, nan=1 / 3), y)
        - _ll_match(np.nan_to_num(f.model, nan=1 / 3), y),
        0.0,
    )
    add(
        "coldstart.fix_vs_none.first10",
        "cold_start",
        "Promoted-team prior fix minus no fix, log loss on promoted teams' first 10 matches",
        False,
        0.0,
        _mean_stat(boot, dfix, p1 & okf),
        int((p1 & okf).sum()),
    )
    del rowmatch, cls
    return out


def describe_extras(f: Frame, method: str = "proportional") -> dict[str, Any]:
    """Descriptive (non-claim) breakdowns: line movement by disagreement size and CLV-style rows."""
    pc, pe = f.close[method], f.early[method]
    ok = ~np.isnan(pc).any(axis=1) & ~np.isnan(pe).any(axis=1) & ~np.isnan(f.model).any(axis=1)
    if ok.sum() == 0:
        return {}
    d = (f.model - pe)[ok].ravel()
    m = (pc - pe)[ok].ravel()
    out: dict[str, Any] = {"label": EARLY_SNAPSHOT_LABEL, "n_matches": int(ok.sum())}
    bins = [(0, 0.02), (0.02, 0.05), (0.05, 1.0)]
    out["movement_on_disagreement_by_size"] = []
    for lo, hi in bins:
        s = (np.abs(d) >= lo) & (np.abs(d) < hi)
        if s.sum() > 0:
            out["movement_on_disagreement_by_size"].append(
                {
                    "abs_disagreement": [lo, hi],
                    "n_selections": int(s.sum()),
                    "slope": float((m[s] * d[s]).sum() / (d[s] ** 2).sum()),
                    "share_close_moves_toward_model": float(
                        (np.sign(m[s]) == np.sign(d[s])).mean()
                    ),
                }
            )
    pe_r, pc_r = pe[ok].ravel(), pc[ok].ravel()
    for nm, s in (
        ("model_favoured_d_gt_0.03", d > DISAGREE),
        ("model_disfavoured_d_lt_-0.03", d < -DISAGREE),
    ):
        if s.sum() > 0:
            out[nm] = {
                "n_selections": int(s.sum()),
                "mean_log_close_over_early": float(np.log(pc_r[s] / pe_r[s]).mean()),
                "share_close_prob_above_early": float((pc_r[s] > pe_r[s]).mean()),
            }
    out["note"] = "CLV-style description of probability drift only; no betting P&L is claimed."
    return out


# ------------------------------------------------------------------------ figures


def _rel(p: FloatArray, y_sel: FloatArray) -> list[dict[str, float]]:
    return M.reliability_bins(p, y_sel)


def make_figures(f: Frame, claims: list[dict[str, Any]], stem: str) -> None:
    pc, pe = f.close["proportional"], f.early["proportional"]
    oh = _onehot(f.y)
    ok = ~np.isnan(pc).any(axis=1)
    league_curves = {}
    for lg in sorted(f.ev["league"].unique()):
        m = np.repeat((f.ev["league"] == lg).to_numpy() & ok, 3)
        league_curves[str(lg)] = _rel(pc.ravel()[m], oh.ravel()[m])
    plots.reliability_plot(
        league_curves,
        FIGURES_DIR / f"pillar_a_reliability_by_league_{stem}.png",
        f"Closing 1X2 reliability by league ({f.bookmaker}, proportional)",
        f"{stem}, n={int(ok.sum())} matches",
    )
    ph = f.ev["early_phase"].to_numpy()
    plots.reliability_plot(
        {
            "early phase": _rel(
                pc.ravel()[np.repeat(ph & ok, 3)], oh.ravel()[np.repeat(ph & ok, 3)]
            ),
            "rest of season": _rel(
                pc.ravel()[np.repeat(~ph & ok, 3)], oh.ravel()[np.repeat(~ph & ok, 3)]
            ),
        },
        FIGURES_DIR / f"pillar_a_reliability_by_phase_{stem}.png",
        "Closing 1X2 reliability by season phase",
        stem,
    )
    curves = {}
    for nm, m in (
        ("promoted team first 10", f.ev["promoted_first10"].to_numpy() & ok),
        ("all other matches", ~f.ev["promoted_involved"].to_numpy() & ok),
    ):
        mm = np.repeat(m, 3)
        if mm.sum():
            curves[nm] = _rel(pc.ravel()[mm], oh.ravel()[mm])
    plots.reliability_plot(
        curves,
        FIGURES_DIR / f"pillar_a_reliability_by_promoted_{stem}.png",
        "Closing 1X2 reliability, promoted teams' first 10 matches",
        stem,
    )
    # early vs close calibration (matches with both)
    both = ok & ~np.isnan(pe).any(axis=1)
    bb = np.repeat(both, 3)
    plots.reliability_plot(
        {
            "closing": _rel(pc.ravel()[bb], oh.ravel()[bb]),
            "early snapshot (not timestamped)": _rel(pe.ravel()[bb], oh.ravel()[bb]),
        },
        FIGURES_DIR / f"pillar_a_reliability_early_vs_close_{stem}.png",
        "Early snapshot vs closing calibration",
        stem,
    )
    # FLB plot: observed vs implied by band
    series = {}
    for snap, P, okk in (("closing", pc, ok), ("early snapshot", pe, ~np.isnan(pe).any(axis=1))):
        pts = []
        band = np.stack([flb_band(np.nan_to_num(P[:, k])) for k in range(3)], axis=1)
        for b in range(len(FLB_BANDS)):
            inb = (band == b) & okk[:, None]
            if inb.sum():
                pts.append((float(P[inb].mean()), float(oh[inb].mean())))
        series[snap] = pts
    series["perfect"] = [(0.0, 0.0), (1.0, 1.0)]
    plots.line_plot(
        series,
        FIGURES_DIR / f"pillar_a_flb_{stem}.png",
        "Favourite-longshot: observed vs implied by band",
        "mean implied probability",
        "observed frequency",
    )
    rows = [
        {
            "label": c["id"].replace("mvm.", ""),
            "estimate": c["estimate"],
            "ci_low": c["ci_low"],
            "ci_high": c["ci_high"],
        }
        for c in claims
        if c["id"].startswith("mvm.")
        and np.isfinite(c["estimate"])
        and c["id"] not in ("mvm.linemove.slope",)
    ]
    if rows:
        plots.forest_plot(
            rows,
            FIGURES_DIR / f"pillar_a_model_vs_market_{stem}.png",
            "Model minus market log loss by segment (negative = model better)",
            "log loss difference",
            0.0,
        )


# ------------------------------------------------------------------------ run


def analyse_frame(f: Frame, tune: Frame | None, boot: Boot, seed: int) -> dict[str, Any]:
    enc = encompassing(tune, f, "proportional", seed)
    base = build_claims(f, boot, "proportional", enc)
    alt = {
        m: build_claims(f, boot, m, encompassing(tune, f, m, seed) if tune is not None else None)
        for m in ("power", "shin")
    }
    altmap = {m: {c["id"]: c for c in cl} for m, cl in alt.items()}
    ps = [c["p"] for c in base]
    q, keep = benjamini_hochberg(ps, FDR)
    for c, qq, kk in zip(base, q, keep, strict=True):
        c["q_bh"], c["survives_bh_10pct"] = qq, bool(kk)
        c["power"] = (
            {k: altmap["power"][c["id"]][k] for k in ("estimate", "ci_low", "ci_high", "p")}
            if c["id"] in altmap["power"]
            else None
        )
        c["shin"] = (
            {k: altmap["shin"][c["id"]][k] for k in ("estimate", "ci_low", "ci_high", "p")}
            if c["id"] in altmap["shin"]
            else None
        )
    survivors = [
        {
            "id": c["id"],
            "description": c["description"],
            "estimate": c["estimate"],
            "ci": [c["ci_low"], c["ci_high"]],
            "q_bh": c["q_bh"],
        }
        for c in base
        if c["survives_bh_10pct"]
    ]
    enc_out = {k: v for k, v in (enc or {}).items() if not k.startswith("_")} or None
    return {
        "family": f.family,
        "name": f.name,
        "bookmaker": f.bookmaker,
        "n_matches": f.n,
        "n_claims_in_bh_family": len(base),
        "fdr": FDR,
        "n_boot": boot.n_boot,
        "claims": base,
        "bh_survivors": survivors,
        "encompassing_fit": enc_out,
        "extras_line_movement": describe_extras(f),
    }


def run_pillar_a(cfg: dict[str, Any] | None = None) -> Path:
    from edgeforge.provenance import provenance

    cfg = cfg or load_config("data")
    con = connect(cfg)
    fd_all = load_fd_clock(con)
    promoted = load_promoted(con)
    proc = resolve_path(cfg, "processed_dir")
    preds = pd.read_parquet(proc / "team_model_preds.parquet")
    a_ids = load_split_ids("pillar_a")
    t_ids = load_split_ids("team_2015_16")
    seed = int(cfg["seed"])

    def pr(block: str, test: str) -> pd.DataFrame:
        return preds[(preds["block"] == block) & (preds["test"] == test)]

    specs = [
        (
            "F1_primary_2024_25",
            "confirmatory",
            "PS",
            a_ids["test_primary_2024_25"],
            pr("pillar_a", "primary_2024_25"),
            "pillar_a",
            N_BOOT,
        ),
        (
            "F2_secondary_2025_26",
            "replication",
            "Avg",
            a_ids["test_secondary_2025_26"],
            pr("pillar_a", "secondary_2025_26"),
            "pillar_a",
            N_BOOT,
        ),
        (
            "F3_earlier_2019_20_to_2023_24",
            "exploratory",
            "PS",
            a_ids["burn_in_tune_2019_23"],
            pr("pillar_a", "tune"),
            None,
            600,
        ),
        (
            "F4_team_2015_16_mw20_38",
            "team_block",
            "PS",
            t_ids["test_2015_16_mw20_38"],
            pr("team_2015_16", "all_2015_16"),
            "team_2015_16",
            N_BOOT,
        ),
    ]
    tune_ids = {
        "pillar_a": (a_ids["burn_in_tune_2019_23"], pr("pillar_a", "tune")),
        "team_2015_16": (t_ids["decay_tuning_2013_14"], pr("team_2015_16", "tune")),
    }
    results: dict[str, Any] = {}
    all_claims: dict[str, list[dict[str, Any]]] = {}
    for name, family, bk, ids, pdf, tune_key, nb in specs:
        log.info("frame %s (%s, %s)", name, family, bk)
        f = build_frame(con, fd_all, promoted, ids, bk, pdf, name, family)
        tune = None
        if tune_key:
            ti, tp = tune_ids[tune_key]
            tune = build_frame(con, fd_all, promoted, ti, bk, tp, f"tune_{tune_key}", "tuning")
        boot = Boot.make(f.n, nb, seed)
        res = analyse_frame(f, tune, boot, seed)
        results[name] = res
        all_claims[name] = res["claims"]
        if family in ("confirmatory", "replication", "team_block"):
            make_figures(f, res["claims"], name)
    con.close()
    # replication: BH survivors of F1 that are significant and same-signed in F2 (nominal p < 0.05)
    f1 = {c["id"]: c for c in results["F1_primary_2024_25"]["claims"]}
    f2 = {c["id"]: c for c in results["F2_secondary_2025_26"]["claims"]}
    replication = []
    for cid, c in f1.items():
        if c["survives_bh_10pct"]:
            c2 = f2.get(cid)
            replication.append(
                {
                    "id": cid,
                    "primary_estimate": c["estimate"],
                    "secondary_estimate": c2["estimate"] if c2 else None,
                    "secondary_p": c2["p"] if c2 else None,
                    "same_sign_and_secondary_p_lt_0.05": bool(
                        c2
                        and np.sign(c2["estimate"] - c["null"])
                        == np.sign(c["estimate"] - c["null"])
                        and c2["p"] < 0.05
                    ),
                }
            )
    version = warehouse_version(cfg)
    payload = {
        "provenance": provenance("edgeforge pillar-a run", cfg, version),
        "design": {
            "segments": "D-037 pre-registered; claims enumerated in build_claims()",
            "de_vig": "proportional (D-036); power and Shin shown alongside, outside the BH family",
            "bh": f"Benjamini-Hochberg at {int(FDR * 100)}% FDR within each block's claim family",
            "early_snapshot_label": EARLY_SNAPSHOT_LABEL,
        },
        "blocks": results,
        "replication_of_primary_survivors": replication,
    }
    metrics_dir = resolve_path(cfg, "metrics_dir")
    write_json(metrics_dir / "pillar_a.json", payload)
    recs = []
    for name, res in results.items():
        enc_claim = next(
            (c for c in res["claims"] if c["id"] == "mvm.encompassing.test_delta"), None
        )
        if enc_claim:
            kept = enc_claim["ci_high"] < 0
            recs.append(
                make_record(
                    f"pillar_a::{name}::encompassing_pool",
                    cfg,
                    version,
                    "edgeforge pillar-a run",
                    feature_set="closing de-vigged odds + Dixon-Coles probabilities (log-linear pool)",
                    model="log_linear_pool",
                    hyperparameters={"fitted_on": "tuning window", "bookmaker": res["bookmaker"]},
                    validation_window=name,
                    metrics={k: enc_claim[k] for k in ("estimate", "ci_low", "ci_high", "p", "n")},
                    calibration={},
                    notes="Test: does the model add information beyond the closing market?",
                    status="promoted" if kept else "rejected",
                )
            )
        fix = next((c for c in res["claims"] if c["id"] == "coldstart.fix_vs_none.first10"), None)
        if fix:
            recs.append(
                make_record(
                    f"pillar_a::{name}::coldstart_fix_test",
                    cfg,
                    version,
                    "edgeforge pillar-a run",
                    feature_set="Dixon-Coles + promoted-team prior",
                    model="dixon_coles_coldstart_fix",
                    hyperparameters={"chosen_on": "tuning window"},
                    validation_window=name,
                    metrics={k: fix[k] for k in ("estimate", "ci_low", "ci_high", "p", "n")},
                    calibration={},
                    notes="Held-out cold-start result; promoted only if the CI excludes zero on the negative side.",
                    status="promoted" if fix["ci_high"] < 0 else "rejected",
                )
            )
        recs.append(
            make_record(
                f"pillar_a::{name}::efficiency_map",
                cfg,
                version,
                "edgeforge pillar-a run",
                feature_set="football-data closing and early-snapshot odds, outcomes",
                model="market_efficiency_map",
                hyperparameters={"de_vig": "proportional", "bh_fdr": FDR, "n_boot": res["n_boot"]},
                validation_window=name,
                metrics={
                    "n_claims": res["n_claims_in_bh_family"],
                    "n_bh_survivors": len(res["bh_survivors"]),
                },
                calibration={},
                notes="Pre-registered D-037 segments; survivors listed in pillar_a.json.",
                status="baseline",
            )
        )
    append_records(recs)
    return metrics_dir / "pillar_a.json"
