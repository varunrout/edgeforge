"""Pillar B in-play team model (D-017, D-056): time-inhomogeneous goal intensities.

rate_i(s) = lambda_pre,i / Lbar * exp(beta_bin(s) + gamma_state(i, s) + delta_own * 1[own reds] +
delta_opp * 1[opponent reds]) on the elapsed-seconds clock, fitted by penalised Poisson regression
on exposure segments, priced by a discrete-time Markov evolution of the remaining goals.
Future dismissals are not modelled (documented assumption).
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.optimize import minimize

FloatArray = NDArray[np.float64]
BIN_EDGES_S = np.array([0, 900, 1800, 2700, 3600, 4500, 5400], dtype=float)
N_BINS = len(BIN_EDGES_S)  # 7: the last bin is [5400, end)
STATE_LEVELS = (
    "trail2",
    "trail1",
    "level",
    "lead1",
    "lead2",
)  # team goal margin <= -2, -1, 0, 1, >= 2
N_STATES = 5
LEVEL = 2
MAX_REMAINING = 8
DT = 30.0
CHECKPOINTS_MIN = (15, 30, 45, 60, 75, 85)


def time_bin(s: FloatArray | float) -> NDArray[np.int64]:
    return (
        np.searchsorted(BIN_EDGES_S, np.asarray(s, dtype=float), side="right").astype(np.int64) - 1
    )


def state_index(margin: NDArray[np.int64]) -> NDArray[np.int64]:
    """Index into STATE_LEVELS for a team's goal margin (own minus opponent)."""
    return np.clip(np.asarray(margin), -2, 2).astype(np.int64) + LEVEL


# ----------------------------------------------------------------------------- segments


def build_segments(matches: pd.DataFrame, events: pd.DataFrame, l_bar: float) -> pd.DataFrame:
    """Exposure segments, one row per (match, team, interval).

    matches: sb_match_id, home, away, match_length_s, lam_h, lam_a.
    events: sb_match_id, kind, team, elapsed_s (goal, own_goal credited to `team`; red_card and
    second_yellow against `team`). Interval (a, b]: goals with a < elapsed_s <= b are counted; the
    state is from events with elapsed_s <= a.
    """
    ev = events[events["kind"].isin(["goal", "own_goal", "red_card", "second_yellow"])]
    by_match = {int(str(k)): g for k, g in ev.groupby("sb_match_id")}
    rows = []
    mcols = zip(
        matches["sb_match_id"].to_numpy(np.int64),
        matches["home"].to_numpy(),
        matches["away"].to_numpy(),
        matches["match_length_s"].to_numpy(float),
        matches["lam_h"].to_numpy(float),
        matches["lam_a"].to_numpy(float),
        strict=True,
    )
    for mid, home, away, length, lam_h, lam_a in mcols:
        g = by_match.get(int(mid))
        ev_t = np.array([]) if g is None else g["elapsed_s"].to_numpy(float)
        kinds = np.array([]) if g is None else g["kind"].to_numpy()
        teams = np.array([]) if g is None else g["team"].to_numpy()
        is_goal = np.isin(kinds, ["goal", "own_goal"])
        inner = BIN_EDGES_S[(BIN_EDGES_S > 0) & (BIN_EDGES_S < length)]
        pts = np.unique(np.concatenate([[0.0, length], inner, ev_t[ev_t < length]]))
        for a, b in zip(pts[:-1], pts[1:], strict=True):
            before = ev_t <= a
            in_seg = (ev_t > a) & (ev_t <= b)
            h_goals = int(np.sum(before & is_goal & (teams == home)))
            a_goals = int(np.sum(before & is_goal & (teams == away)))
            h_red = int(np.sum(before & ~is_goal & (teams == home)))
            a_red = int(np.sum(before & ~is_goal & (teams == away)))
            y_h = int(np.sum(in_seg & is_goal & (teams == home)))
            y_a = int(np.sum(in_seg & is_goal & (teams == away)))
            tb = int(time_bin(a))
            for side, lam, y, own_g, opp_g, own_r, opp_r in (
                (0, lam_h, y_h, h_goals, a_goals, h_red, a_red),
                (1, lam_a, y_a, a_goals, h_goals, a_red, h_red),
            ):
                st = int(state_index(np.array(own_g - opp_g)))
                rows.append(
                    (
                        int(mid),
                        side,
                        y,
                        float(b - a),
                        tb,
                        st,
                        int(own_r >= 1),
                        int(opp_r >= 1),
                        float(lam) / l_bar,
                    )
                )
    return pd.DataFrame(
        rows,
        columns=[
            "sb_match_id",
            "side",
            "y",
            "exposure_s",
            "bin",
            "state",
            "own_red",
            "opp_red",
            "rate0",
        ],
    )


# ----------------------------------------------------------------------------- fitting


@dataclass(frozen=True)
class Params:
    beta: FloatArray  # [N_BINS]
    gamma: FloatArray  # [N_STATES] with the level entry fixed at 0
    delta_own: float
    delta_opp: float
    kappa: float
    l_bar: float
    lengths: FloatArray = field(default_factory=lambda: np.zeros(0))  # sorted fitted match lengths

    def as_record(self) -> dict[str, object]:
        return {
            "beta_per_second_multiplier": np.exp(self.beta).tolist(),
            "gamma_multiplier_by_state": dict(zip(("trail2", "trail1", "level", "lead1", "lead2"), np.exp(self.gamma).tolist(), strict=True)),
            "own_dismissal_multiplier": float(np.exp(self.delta_own)),
            "opponent_dismissal_multiplier": float(np.exp(self.delta_opp)),
            "kappa": self.kappa, "l_bar_s": self.l_bar, "n_lengths": int(len(self.lengths)),
        }  # fmt: skip


def _design(seg: pd.DataFrame, use_state: bool, use_dismissal: bool) -> FloatArray:
    n = len(seg)
    x = np.zeros((n, N_BINS + (N_STATES - 1) + 2))
    x[np.arange(n), seg["bin"].to_numpy()] = 1.0
    if use_state:
        st = seg["state"].to_numpy()
        for k in range(N_STATES):
            if k != LEVEL:
                x[:, N_BINS + (k if k < LEVEL else k - 1)] = (st == k).astype(float)
    if use_dismissal:
        x[:, N_BINS + N_STATES - 1] = seg["own_red"].to_numpy(float)
        x[:, N_BINS + N_STATES] = seg["opp_red"].to_numpy(float)
    return x


def fit_params(
    seg: pd.DataFrame,
    kappa: float,
    lengths: FloatArray,
    use_state: bool = True,
    use_dismissal: bool = True,
) -> Params:
    x = _design(seg, use_state, use_dismissal)
    y = seg["y"].to_numpy(float)
    off = np.log(seg["exposure_s"].to_numpy(float) * seg["rate0"].to_numpy(float))
    pen = np.zeros(x.shape[1])
    pen[N_BINS:] = kappa

    def fun(b: FloatArray) -> tuple[float, FloatArray]:
        eta = off + x @ b
        mu = np.exp(np.clip(eta, -30, 10))
        ll = float(np.sum(y * eta - mu) - 0.5 * np.sum(pen * b * b))
        grad = x.T @ (y - mu) - pen * b
        return -ll, -grad

    b0 = np.zeros(x.shape[1])
    b0[:N_BINS] = np.log(max(y.sum() / max(np.exp(off).sum(), 1e-9), 1e-3))
    res = minimize(fun, b0, jac=True, method="L-BFGS-B")
    b = res.x
    gamma = np.zeros(N_STATES)
    for k in range(N_STATES):
        if k != LEVEL:
            gamma[k] = b[N_BINS + (k if k < LEVEL else k - 1)]
    l_bar = float(np.mean(lengths))
    return Params(
        b[:N_BINS],
        gamma,
        float(b[N_BINS + N_STATES - 1]),
        float(b[N_BINS + N_STATES]),
        kappa,
        l_bar,
        np.sort(lengths),
    )


def held_out_loglik(p: Params, seg: pd.DataFrame) -> float:
    """Mean Poisson log likelihood per segment (up to the constant -log y!)."""
    eta = (
        np.log(seg["exposure_s"].to_numpy(float) * seg["rate0"].to_numpy(float))
        + p.beta[seg["bin"].to_numpy()]
        + p.gamma[seg["state"].to_numpy()]
    )
    eta = (
        eta
        + p.delta_own * seg["own_red"].to_numpy(float)
        + p.delta_opp * seg["opp_red"].to_numpy(float)
    )
    mu = np.exp(np.clip(eta, -30, 10))
    y = seg["y"].to_numpy(float)
    return float(np.mean(y * eta - mu))


# ----------------------------------------------------------------------------- pricing


def survival_hazards(lengths: FloatArray, times: FloatArray) -> FloatArray:
    """P(match ends in (s, s + dt] | length > s) for each s in `times`."""
    n = len(lengths)
    s_now = 1.0 - np.searchsorted(lengths, times, side="right") / n
    s_next = 1.0 - np.searchsorted(lengths, times + DT, side="right") / n
    with np.errstate(invalid="ignore", divide="ignore"):
        h = np.where(s_now > 0, (s_now - s_next) / s_now, 1.0)
    return np.clip(h, 0.0, 1.0)


def price(
    p: Params,
    lam_h: FloatArray,
    lam_a: FloatArray,
    h0: NDArray[np.int64],
    a0: NDArray[np.int64],
    red_h: NDArray[np.int64],
    red_a: NDArray[np.int64],
    t: FloatArray,
    use_state: bool = True,
    use_dismissal: bool = True,
) -> dict[str, FloatArray]:
    """Distribution of the remaining goals at elapsed seconds t (a vector, one per proposition)
    and the final markets. Returns joint pmf over final (home, away) goals up to the cap."""
    n = len(lam_h)
    k = MAX_REMAINING + 1
    dist = np.zeros((n, k, k))
    dist[:, 0, 0] = 1.0
    ended = np.zeros((n, k, k))
    gh = p.gamma if use_state else np.zeros(N_STATES)
    d_own_h = (
        (p.delta_own * (red_h >= 1) + p.delta_opp * (red_a >= 1)) if use_dismissal else np.zeros(n)
    )
    d_own_a = (
        (p.delta_own * (red_a >= 1) + p.delta_opp * (red_h >= 1)) if use_dismissal else np.zeros(n)
    )
    i_idx = np.arange(k)[None, :, None]
    j_idx = np.arange(k)[None, None, :]
    margin_h = (h0[:, None, None] + i_idx) - (a0[:, None, None] + j_idx)
    st_h = state_index(margin_h)
    st_a = state_index(-margin_h)
    base_h = (lam_h / p.l_bar)[:, None, None] * np.exp(d_own_h)[:, None, None] * np.exp(gh[st_h])
    base_a = (lam_a / p.l_bar)[:, None, None] * np.exp(d_own_a)[:, None, None] * np.exp(gh[st_a])
    t0 = float(np.min(t))
    steps = np.arange(t0, float(p.lengths.max()) + DT, DT)
    hz = survival_hazards(p.lengths, steps)
    for s_k, s in enumerate(steps):
        active = (t <= s)[:, None, None]  # a proposition starts evolving at its own t
        b = int(time_bin(s))
        pr_h = np.where(active, 1.0 - np.exp(-base_h * np.exp(p.beta[b]) * DT), 0.0)
        pr_a = np.where(active, 1.0 - np.exp(-base_a * np.exp(p.beta[b]) * DT), 0.0)
        pr_h[:, k - 1, :] = 0.0
        pr_a[:, :, k - 1] = 0.0
        move_h = dist * pr_h
        move_a = dist * pr_a
        both = dist * pr_h * pr_a
        new = dist - move_h - move_a + both
        new[:, 1:, :] += move_h[:, :-1, :] - both[:, :-1, :]
        new[:, :, 1:] += move_a[:, :, :-1] - both[:, :, :-1]
        new[:, 1:, 1:] += both[:, :-1, :-1]
        h = np.where(active, hz[s_k], 0.0)
        ended += new * h
        dist = new * (1.0 - h)
    ended += dist  # anything left (beyond the longest fitted length) ends now
    ended /= ended.sum(axis=(1, 2), keepdims=True)
    fin_h = h0[:, None, None] + i_idx
    fin_a = a0[:, None, None] + j_idx
    out: dict[str, FloatArray] = {
        "1x2": np.stack(
            [
                (ended * (fin_h > fin_a)).sum(axis=(1, 2)),
                (ended * (fin_h == fin_a)).sum(axis=(1, 2)),
                (ended * (fin_h < fin_a)).sum(axis=(1, 2)),
            ],
            axis=1,
        ),
        "btts": (ended * ((fin_h > 0) & (fin_a > 0))).sum(axis=(1, 2)),
        "exp_remaining": (ended * (i_idx + j_idx)).sum(axis=(1, 2)),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        out[f"over_{line}"] = (ended * ((fin_h + fin_a) > line)).sum(axis=(1, 2))
    return out
