"""Same-game-accumulator engine: leg DSL, validation and joint pricing on simulated matches.

Player legs follow the D-030 void rule: a leg is priced conditional on its player appearing, and
the joint is priced conditional on every named player appearing. The naive product multiplies the
standalone leg probabilities (each conditional on its own player appearing).
"""

import math
from typing import Annotated, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field, field_validator

from edgeforge.simulation.engine import SimResult


class SGAError(ValueError):
    """The combination is impossible, contradictory, redundant or names an unknown player."""


class TeamResult(BaseModel):
    kind: Literal["team_result"] = "team_result"
    result: Literal["home", "draw", "away"]


class Total(BaseModel):
    kind: Literal["total"] = "total"
    line: float
    side: Literal["over", "under"]

    @field_validator("line")
    @classmethod
    def _half_line(cls, v: float) -> float:
        if abs(v * 2 - round(v * 2)) > 1e-9 or abs(v - round(v)) < 1e-9 or v < 0:
            raise ValueError("totals use half-lines such as 0.5, 1.5, 2.5")
        return v


class BTTS(BaseModel):
    kind: Literal["btts"] = "btts"
    side: Literal["yes", "no"]


class PlayerAGS(BaseModel):
    kind: Literal["player_ags"] = "player_ags"
    player_id: int


class PlayerShots(BaseModel):
    kind: Literal["player_shots"] = "player_shots"
    player_id: int
    k: int = Field(ge=1, le=10)


class PlayerSoT(BaseModel):
    kind: Literal["player_sot"] = "player_sot"
    player_id: int
    k: int = Field(ge=1, le=10)


Leg = Annotated[
    TeamResult | Total | BTTS | PlayerAGS | PlayerShots | PlayerSoT, Field(discriminator="kind")
]


class SGA(BaseModel):
    legs: list[Leg] = Field(min_length=2, max_length=8)


def _player(leg: object) -> int | None:
    return getattr(leg, "player_id", None)


def validate_sga(sga: SGA, player_team: dict[int, int]) -> None:
    """Raise SGAError for impossible, contradictory or redundant combinations.
    `player_team` maps every player in the match squad to 0 (home) or 1 (away)."""
    legs = sga.legs
    keys = [leg.model_dump_json() for leg in legs]
    if len(set(keys)) != len(keys):
        raise SGAError("duplicate leg")
    for leg in legs:
        pid = _player(leg)
        if pid is not None and pid not in player_team:
            raise SGAError(f"player {pid} is not in the squad for this match")
    results = [leg for leg in legs if isinstance(leg, TeamResult)]
    if len(results) > 1:
        raise SGAError("at most one team-result leg (same market on both sides)")
    btts = [leg for leg in legs if isinstance(leg, BTTS)]
    if len(btts) > 1:
        raise SGAError("BTTS yes and no in one SGA")
    overs = {leg.line for leg in legs if isinstance(leg, Total) and leg.side == "over"}
    unders = {leg.line for leg in legs if isinstance(leg, Total) and leg.side == "under"}
    if len(overs) > 1 or len(unders) > 1:
        raise SGAError("more than one over or under line (redundant)")
    ags = [leg.player_id for leg in legs if isinstance(leg, PlayerAGS)]
    min_goals = len(set(ags))
    if results and results[0].result != "draw":
        min_goals = max(min_goals, 1)
    if btts and btts[0].side == "yes":
        min_goals = max(min_goals, 2)
    if overs:
        min_goals = max(min_goals, math.floor(min(overs)) + 1)
    if unders and min_goals > math.floor(max(unders)):
        raise SGAError(f"needs at least {min_goals} goals but Under {max(unders)} allows fewer")
    if overs and unders and min(overs) >= max(unders):
        raise SGAError("over line not below under line")
    if btts and btts[0].side == "no":
        teams = {player_team[p] for p in ags}
        if teams == {0, 1}:
            raise SGAError("scorers from both teams contradict BTTS no")
        if results and results[0].result == "draw" and min_goals >= 1:
            raise SGAError("a draw with goals needs both teams to score, contradicting BTTS no")
    # redundancy within one player
    by_player: dict[int, list[object]] = {}
    for leg in legs:
        pid = _player(leg)
        if pid is not None:
            by_player.setdefault(pid, []).append(leg)
    for pid, ls in by_player.items():
        shots = [leg.k for leg in ls if isinstance(leg, PlayerShots)]
        sots = [leg.k for leg in ls if isinstance(leg, PlayerSoT)]
        has_ags = any(isinstance(leg, PlayerAGS) for leg in ls)
        if len(shots) > 1 or len(sots) > 1:
            raise SGAError(f"two thresholds of the same market for player {pid}")
        if has_ags and ((sots and sots[0] <= 1) or (shots and shots[0] <= 1)):
            raise SGAError(f"AGS already implies this leg for player {pid}")
        if shots and sots and shots[0] <= sots[0]:
            raise SGAError(f"SoT {sots[0]}+ already implies shots {shots[0]}+ for player {pid}")


def leg_indicator(leg: object, res: SimResult) -> NDArray[np.bool_]:
    g = res.team_goals
    if isinstance(leg, TeamResult):
        ind: NDArray[np.bool_] = {
            "home": g[:, 0] > g[:, 1],
            "draw": g[:, 0] == g[:, 1],
            "away": g[:, 0] < g[:, 1],
        }[leg.result]
        return ind
    if isinstance(leg, Total):
        tot = g.sum(axis=1)
        return tot > leg.line if leg.side == "over" else tot < leg.line
    if isinstance(leg, BTTS):
        both = (g[:, 0] > 0) & (g[:, 1] > 0)
        return both if leg.side == "yes" else ~both
    if isinstance(leg, PlayerAGS):
        return res.goals[:, res.player_index(leg.player_id)] >= 1
    if isinstance(leg, PlayerShots):
        return res.shots[:, res.player_index(leg.player_id)] >= leg.k
    if isinstance(leg, PlayerSoT):
        return res.sot[:, res.player_index(leg.player_id)] >= leg.k
    raise TypeError(leg)


def indicator_matrix(sga: SGA, res: SimResult) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    """([N, L] leg indicators, [N] all named players on the pitch). This is the D-045 cache."""
    ind = np.stack([leg_indicator(leg, res) for leg in sga.legs], axis=1)
    ok = np.ones(res.n_sims, dtype=bool)
    for leg in sga.legs:
        pid = _player(leg)
        if pid is not None:
            ok &= res.on_pitch[:, res.player_index(pid)]
    return ind, ok


def price_from_indicators(
    ind: NDArray[np.bool_], ok: NDArray[np.bool_], leg_ok: NDArray[np.bool_]
) -> dict[str, object]:
    """Standalone, naive and joint probabilities from cached indicators.
    `leg_ok` [N, L]: sims where the leg's own player is on the pitch (all True for team legs)."""
    n_leg = ind.shape[1]
    standalone = []
    for j in range(n_leg):
        m = leg_ok[:, j]
        standalone.append(float(ind[m, j].mean()) if m.any() else float("nan"))
    naive = float(np.prod(standalone))
    n_eff = int(ok.sum())
    joint = float(ind[ok].all(axis=1).mean()) if n_eff else float("nan")
    se = math.sqrt(max(joint * (1 - joint), 0.0) / n_eff) if n_eff else float("nan")
    ratio = joint / naive if naive > 0 else float("nan")
    return {
        "leg_probabilities": standalone,
        "naive_product": naive,
        "joint": joint,
        "joint_mc_se": se,
        "n_effective_sims": n_eff,
        "fair_odds_naive": 1.0 / naive if naive > 0 else float("inf"),
        "fair_odds_adjusted": 1.0 / joint if joint > 0 else float("inf"),
        "adjustment_ratio": ratio,
        "direction": "positive" if ratio > 1.0 else "negative" if ratio < 1.0 else "none",
    }


def leg_ok_matrix(sga: SGA, res: SimResult) -> NDArray[np.bool_]:
    cols = []
    for leg in sga.legs:
        pid = _player(leg)
        cols.append(
            res.on_pitch[:, res.player_index(pid)]
            if pid is not None
            else np.ones(res.n_sims, dtype=bool)
        )
    return np.stack(cols, axis=1)


def price_sga(sga: SGA, res: SimResult) -> dict[str, object]:
    validate_sga(
        sga, {int(p): int(t) for p, t in zip(res.inputs.player_id, res.inputs.team, strict=True)}
    )
    ind, ok = indicator_matrix(sga, res)
    return price_from_indicators(ind, ok, leg_ok_matrix(sga, res))
