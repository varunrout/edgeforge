"""Coherent match simulator (D-014, D-045).

Per simulation: (1) scoreline from Dixon-Coles with rho; (2) own goals as a small share of goals,
team shots = non-own goals + a negative-binomial excess conditional on both teams' goals;
(3) player roles and minutes from the participation model (lineups: confirmed XI and bench,
opening: starters sampled so that exactly eleven start); (4) each team shot is allocated to an
on-pitch player in proportion to shot share x minutes; (5) the team's non-own goals go to shots
with probability proportional to the player's per-shot scoring rate (xG share); goals are on
target; (6) non-goal shots are on target at the player's conditional SoT rate.

Dismissals are not simulated as events: a sent-off player's early exit is already part of the
fitted exit-time distribution (D-031 counts dismissals as exits), but a dismissal does not change
the opponent's or the team's scoring. This assumption is documented, not hidden.

Everything is vectorised over simulations and seeded from (seed, match_id, state, MODEL_VERSION),
so any simulation can be regenerated exactly.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.optimize import minimize_scalar
from scipy.special import gammaln
from sklearn.linear_model import PoissonRegressor

from edgeforge.models.dixon_coles import dc_grid

FloatArray = NDArray[np.float64]
MODEL_VERSION = 1
STATE_CODE = {"opening": 1, "lineups": 2}
MAX_GOALS = 10
N_MIN_COLS = 20
STARTERS = 11


def make_rng(seed: int, match_id: int, state: str) -> np.random.Generator:
    return np.random.default_rng([seed, int(match_id), STATE_CODE[state], MODEL_VERSION])


def _nb_ll(y: FloatArray, mu: FloatArray, alpha: float) -> float:
    r = 1.0 / alpha
    ll = (
        gammaln(y + r)
        - gammaln(y + 1)
        - gammaln(r)
        + r * np.log(r / (r + mu))
        + y * np.log(mu / (r + mu))
    )
    return float(ll.sum())


@dataclass(frozen=True)
class ShotsGivenGoals:
    """Team shots excess X = shots - non-own goals ~ NB(exp(a + b log mu + c G + d G_opp), alpha),
    plus the own-goal share pi_og. Fitted on the tuning window only."""

    a: float
    b: float
    c: float
    d: float
    alpha: float
    pi_og: float

    @classmethod
    def fit(cls, df: pd.DataFrame) -> "ShotsGivenGoals":
        """df rows: mu (shots prior mean), g_own, g_opp (scoreline goals incl. own goals credited),
        shots, player_goals (non-own goals), own_goals_credited."""
        x = np.column_stack([np.log(df["mu"]), df["g_own"], df["g_opp"]]).astype(float)
        y = (df["shots"] - df["player_goals"]).to_numpy(float)
        reg = PoissonRegressor(alpha=0.0, max_iter=500).fit(x, y)
        mu = reg.predict(x)
        res = minimize_scalar(
            lambda la: -_nb_ll(y, mu, float(np.exp(la))), bounds=(-8, 2), method="bounded"
        )
        pi = float(df["own_goals_credited"].sum() / max(df["g_own"].sum(), 1.0))
        return cls(
            float(reg.intercept_),
            float(reg.coef_[0]),
            float(reg.coef_[1]),
            float(reg.coef_[2]),
            float(np.exp(res.x)),
            pi,
        )

    def mean_excess(self, mu: float, g_own: FloatArray, g_opp: FloatArray) -> FloatArray:
        out: FloatArray = np.exp(self.a + self.b * np.log(mu) + self.c * g_own + self.d * g_opp)
        return out


@dataclass
class MatchInputs:
    match_id: int
    state: str
    lam_h: float
    lam_a: float
    rho: float
    mu_h: float
    mu_a: float
    player_id: NDArray[np.int64]
    team: NDArray[np.int64]  # 0 home, 1 away
    omega: FloatArray
    g_shot: FloatArray
    sot_cond: FloatArray  # P(on target | shot, not a goal)
    p_start: FloatArray  # P(starts); 0/1 in the lineups state
    q_app_bench: FloatArray  # P(appears | does not start)
    ws: FloatArray
    gs: FloatArray
    wb: FloatArray
    gb: FloatArray
    sampled_roles: bool
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def n_players(self) -> int:
        return len(self.player_id)


@dataclass
class SimResult:
    inputs: MatchInputs
    team_goals: NDArray[np.int16]  # [N, 2] scoreline goals (incl. own goals credited)
    own_goals: NDArray[np.int16]  # [N, 2] own goals credited to each team
    shots: NDArray[np.int16]  # [N, P]
    sot: NDArray[np.int16]
    goals: NDArray[np.int16]  # non-own goals
    on_pitch: NDArray[np.bool_]
    started: NDArray[np.bool_]
    minutes: NDArray[np.float32]
    team_shots: NDArray[np.int16] = field(default_factory=lambda: np.zeros((0, 2), np.int16))

    @property
    def n_sims(self) -> int:
        return int(self.team_goals.shape[0])

    def player_index(self, player_id: int) -> int:
        hit = np.flatnonzero(self.inputs.player_id == player_id)
        if len(hit) == 0:
            raise KeyError(player_id)
        return int(hit[0])


def sample_scoreline(
    rng: np.random.Generator, lam_h: float, lam_a: float, rho: float, n: int
) -> NDArray[np.int16]:
    grid = dc_grid(np.array([lam_h]), np.array([lam_a]), rho, MAX_GOALS)[0]
    cdf = np.cumsum(grid.ravel())
    cdf /= cdf[-1]
    idx = np.minimum(np.searchsorted(cdf, rng.random(n)), cdf.size - 1)
    out = np.stack([idx // (MAX_GOALS + 1), idx % (MAX_GOALS + 1)], axis=1)
    return out.astype(np.int16)


def _pick_columns(rng: np.random.Generator, w: FloatArray) -> NDArray[np.int64]:
    """Sample a column index per row of a [N, P, K] weight tensor (rows need not be normalised)."""
    cw = np.cumsum(w, axis=-1)
    cw /= np.clip(cw[..., -1:], 1e-12, None)
    u = rng.random(w.shape[:-1])[..., None]
    col: NDArray[np.int64] = np.minimum((u > cw).sum(axis=-1), w.shape[-1] - 1).astype(np.int64)
    return col


def sample_roles_and_minutes(
    rng: np.random.Generator, inp: MatchInputs, n: int
) -> tuple[NDArray[np.bool_], NDArray[np.bool_], NDArray[np.float32]]:
    p = inp.n_players
    if inp.sampled_roles:
        started = np.zeros((n, p), dtype=bool)
        for t in (0, 1):
            idx = np.flatnonzero(inp.team == t)
            if len(idx) <= STARTERS:
                started[:, idx] = True
                continue
            ps = np.clip(inp.p_start[idx], 1e-6, 1 - 1e-6)
            key = np.log(ps / (1 - ps))[None, :] + rng.gumbel(size=(n, len(idx)))
            kth = np.partition(-key, STARTERS - 1, axis=1)[:, STARTERS - 1][:, None]
            started[:, idx] = -key <= kth
    else:
        started = np.broadcast_to(inp.p_start >= 0.5, (n, p)).copy()
    appears_b = rng.random((n, p)) < inp.q_app_bench[None, :]
    on_pitch = started | appears_b
    w = np.where(started[..., None], inp.ws[None], inp.wb[None])
    g = np.where(started[..., None], inp.gs[None], inp.gb[None])
    col = _pick_columns(rng, w.astype(np.float32))
    minutes = np.take_along_axis(g, col[..., None], axis=-1)[..., 0].astype(np.float32)
    minutes = np.where(on_pitch, minutes, 0.0).astype(np.float32)
    return started, on_pitch, minutes


def _team_counts(
    rng: np.random.Generator,
    idx: NDArray[np.int64],
    inp: MatchInputs,
    on_pitch: NDArray[np.bool_],
    minutes: NDArray[np.float32],
    s_total: NDArray[np.int64],
    ng: NDArray[np.int64],
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Allocate shots, goals and SoT to the players `idx` of one team."""
    n = len(s_total)
    pt = len(idx)
    w = inp.omega[idx][None, :] * minutes[:, idx] * on_pitch[:, idx]
    w = np.where(w.sum(axis=1, keepdims=True) > 0, w, on_pitch[:, idx].astype(float))
    cw = np.cumsum(w, axis=1)
    cw = cw / np.clip(cw[:, -1:], 1e-12, None)
    smax = int(s_total.max()) if n else 0
    shots = np.zeros((n, pt))
    sot = np.zeros((n, pt))
    goals = np.zeros((n, pt))
    if smax == 0:
        return shots, sot, goals
    active = np.arange(smax)[None, :] < s_total[:, None]
    u = rng.random((n, smax))
    pl = np.minimum((u[..., None] > cw[:, None, :]).sum(axis=-1), pt - 1)
    gkey = np.log(np.clip(inp.g_shot[idx], 1e-9, None))[pl] + rng.gumbel(size=(n, smax))
    gkey = np.where(active, gkey, -np.inf)
    srt = -np.sort(-gkey, axis=1)
    kth = np.take_along_axis(srt, np.clip(ng - 1, 0, smax - 1)[:, None], axis=1)
    is_goal = (gkey >= kth) & (ng[:, None] > 0) & active
    on_t = rng.random((n, smax)) < inp.sot_cond[idx][pl]
    is_sot = is_goal | (on_t & active)
    flat = (np.arange(n)[:, None] * pt + pl).ravel()
    for arr, mask in ((shots, active), (sot, is_sot), (goals, is_goal)):
        arr[:] = np.bincount(flat, weights=mask.ravel().astype(float), minlength=n * pt).reshape(
            n, pt
        )
    return shots, sot, goals


def simulate(inp: MatchInputs, model: ShotsGivenGoals, n_sims: int, seed: int) -> SimResult:
    rng = make_rng(seed, inp.match_id, inp.state)
    tg = sample_scoreline(rng, inp.lam_h, inp.lam_a, inp.rho, n_sims)
    og = rng.binomial(tg.astype(np.int64), model.pi_og).astype(np.int16)  # own goals credited
    ng = (tg - og).astype(np.int64)
    started, on_pitch, minutes = sample_roles_and_minutes(rng, inp, n_sims)
    p = inp.n_players
    shots = np.zeros((n_sims, p), np.int16)
    sot = np.zeros((n_sims, p), np.int16)
    goals = np.zeros((n_sims, p), np.int16)
    team_shots = np.zeros((n_sims, 2), np.int16)
    for t, mu in ((0, inp.mu_h), (1, inp.mu_a)):
        idx = np.flatnonzero(inp.team == t)
        if len(idx) == 0:
            continue
        m = model.mean_excess(mu, tg[:, t].astype(float), tg[:, 1 - t].astype(float))
        if model.alpha < 1e-6:
            x = rng.poisson(m)
        else:
            x = rng.poisson(rng.gamma(1.0 / model.alpha, model.alpha * m))
        s_total = ng[:, t] + x
        team_shots[:, t] = s_total
        a, b, c = _team_counts(rng, idx, inp, on_pitch, minutes, s_total, ng[:, t])
        shots[:, idx], sot[:, idx], goals[:, idx] = a, b, c
    return SimResult(inp, tg, og, shots, sot, goals, on_pitch, started, minutes, team_shots)
