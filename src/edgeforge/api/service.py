"""Pricing service shared by the FastAPI app and the Streamlit trader app (Phase 8).

Every price is regenerated from the serving bundle: team markets from the Dixon-Coles grid
(opening, lineups) or the Pillar B in-play model, player props and SGAs from the seeded Phase 5
simulator. Margins come from `edgeforge.pricing.margin`. Evidence numbers come from
`edgeforge.evidence`, i.e. from metrics files.
"""

import json
from pathlib import Path
from typing import Any

import numpy as np

from edgeforge import evidence
from edgeforge.api.bundle import Bundle
from edgeforge.evaluation.inplay_model import price as inplay_price
from edgeforge.evaluation.phase4_props import MARKETS
from edgeforge.evaluation.phase6b import conditional_marginals
from edgeforge.models.dixon_coles import dc_grid
from edgeforge.pricing.margin import METHODS, apply_margin
from edgeforge.pricing.markets import OU_LINES, market_probs
from edgeforge.sga.engine import (
    SGA,
    indicator_matrix,
    leg_ok_matrix,
    price_from_indicators,
    validate_sga,
)
from edgeforge.simulation.engine import SimResult, simulate

DISCLAIMER = (
    "Historical replay of 2015/16 test matches, not a live feed. Fair prices are model "
    "probabilities; quoted prices add a margin. Nothing here claims an edge or commercial use: "
    "the pre-match model does not beat the Pinnacle closing market (see /model-health)."
)
STATES = ("opening", "lineups", "inplay")
SETTLEMENTS = ("start", "appear")


class ServiceError(ValueError):
    """A request the service cannot answer (unknown event, unsupported state, bad minute)."""


def _event(b: Bundle, event_id: int) -> dict[str, Any]:
    if event_id not in b.events:
        raise ServiceError(f"unknown event {event_id}")
    return b.events[event_id]


def list_events(b: Bundle) -> list[dict[str, Any]]:
    out = []
    for e in b.events.values():
        out.append({
            "id": e["id"], "home": e["home"], "away": e["away"], "date": e["date"],
            "match_week": e["match_week"], "final_score": e["final_score"],
            "n_goals": sum(1 for x in e["timeline"] if x["kind"] in ("goal", "own_goal")),
            "n_dismissals": sum(1 for x in e["timeline"] if x["kind"] in ("red_card", "second_yellow")),
            "states": [s for s in ("opening", "lineups") if b.has_state(e["id"], s)] + ["inplay"],
        })  # fmt: skip
    return sorted(out, key=lambda x: x["id"])


def _check_margin(overround: float, method: str) -> None:
    if not 0.0 <= overround <= 0.20:
        raise ServiceError("overround must be between 0 and 0.20")
    if method not in METHODS:
        raise ServiceError(f"method must be one of {METHODS}")


def _line(market: str, selection: str, p: float, q: float) -> dict[str, Any]:
    return {
        "market": market, "selection": selection, "fair_probability": p,
        "fair_odds": 1.0 / max(p, 1e-9), "quoted_probability": q, "quoted_odds": 1.0 / max(q, 1e-9),
    }  # fmt: skip


def _team_lines(p: dict[str, Any], overround: float, method: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    q3 = apply_margin(np.asarray(p["1x2"], dtype=float), overround, method)
    for s, pp, qq in zip(("home", "draw", "away"), p["1x2"], q3, strict=True):
        lines.append(_line("1x2", s, float(pp), float(qq)))
    for line in OU_LINES:
        po = float(p[f"over_{line}"])
        qo, qu = apply_margin(np.array([po, 1 - po]), overround, method)
        lines.append(_line(f"total_{line}", "over", po, float(qo)))
        lines.append(_line(f"total_{line}", "under", 1 - po, float(qu)))
    pb = float(p["btts"])
    qy, qn = apply_margin(np.array([pb, 1 - pb]), overround, method)
    lines.append(_line("btts", "yes", pb, float(qy)))
    lines.append(_line("btts", "no", 1 - pb, float(qn)))
    return lines


def _state_at(timeline: list[dict[str, Any]], t_s: float) -> dict[str, int]:
    prior = [e for e in timeline if e["elapsed_s"] < t_s]
    goal = [e for e in prior if e["kind"] in ("goal", "own_goal")]
    red = [e for e in prior if e["kind"] in ("red_card", "second_yellow")]
    return {
        "h0": sum(1 for e in goal if e["side"] == "home"), "a0": sum(1 for e in goal if e["side"] == "away"),
        "red_h": sum(1 for e in red if e["side"] == "home"), "red_a": sum(1 for e in red if e["side"] == "away"),
    }  # fmt: skip


def _inplay_probs(b: Bundle, e: dict[str, Any], minutes: list[float]) -> list[dict[str, Any]]:
    sts = [_state_at(e["timeline"], m * 60.0) for m in minutes]

    def arr(k: str) -> Any:
        return np.array([s[k] for s in sts], dtype=np.int64)

    n = len(minutes)
    out = inplay_price(
        b.inplay, np.full(n, e["lam_h"]), np.full(n, e["lam_a"]), arr("h0"), arr("a0"),
        arr("red_h"), arr("red_a"), np.array(minutes, dtype=float) * 60.0,
    )  # fmt: skip
    res = []
    for i in range(n):
        d: dict[str, Any] = {"1x2": out["1x2"][i].tolist(), "btts": float(out["btts"][i])}
        for line in OU_LINES:
            d[f"over_{line}"] = float(out[f"over_{line}"][i])
        d["state"] = sts[i]
        res.append(d)
    return res


def _dc_probs(e: dict[str, Any], rho: float) -> dict[str, Any]:
    g = dc_grid(np.array([e["lam_h"]]), np.array([e["lam_a"]]), rho)
    mp = market_probs(g)
    d: dict[str, Any] = {"1x2": mp["1x2"][0].tolist(), "btts": float(mp["btts"][0])}
    for line in OU_LINES:
        d[f"over_{line}"] = float(mp[f"over_{line}"][0])
    return d


def team_markets(
    b: Bundle,
    event_id: int,
    state: str,
    minute: float | None = None,
    overround: float = 0.10,
    method: str = "proportional",
) -> dict[str, Any]:
    _check_margin(overround, method)
    e = _event(b, event_id)
    if state not in STATES:
        raise ServiceError(f"state must be one of {STATES}")
    info: dict[str, Any] = {
        "event_id": event_id,
        "state": state,
        "overround": overround,
        "method": method,
    }
    if state == "inplay":
        m = 0.0 if minute is None else float(minute)
        if not 0.0 <= m < e["match_length_s"] / 60.0:
            raise ServiceError("minute must be between 0 and the end of the match")
        pr = _inplay_probs(b, e, [m])[0]
        info.update({"minute": m, "score": [pr["state"]["h0"], pr["state"]["a0"]], "dismissals": [pr["state"]["red_h"], pr["state"]["red_a"]],
                     "source": "Pillar B in-play model (D-056); future dismissals not modelled"})  # fmt: skip
    else:
        rho = b.inputs(event_id, state).rho if b.has_state(event_id, state) else 0.0
        pr = _dc_probs(e, rho)
        info["source"] = "Dixon-Coles opening-state model; lineups do not change team-market prices"
    info["lines"] = _team_lines(pr, overround, method)
    info["disclaimer"] = DISCLAIMER
    return info


def _sim(b: Bundle, event_id: int, state: str) -> SimResult:
    key = (event_id, state)
    if key not in b.cache:
        if len(b.cache) >= 24:
            b.cache.pop(next(iter(b.cache)))
        b.cache[key] = simulate(
            b.inputs(event_id, state), b.model, int(b.meta["n_sims"]), int(b.meta["seed"])
        )
    out: SimResult = b.cache[key]
    return out


def props(
    b: Bundle,
    event_id: int,
    state: str,
    overround: float = 0.10,
    method: str = "odds_ratio",
    settlement: str = "start",
) -> dict[str, Any]:
    """Player props from the simulator. `settlement`: 'start' (void if the player does not start,
    D-053/D-055 recommended) or 'appear' (void if the player does not appear, D-030)."""
    _check_margin(overround, method)
    _event(b, event_id)
    if state not in ("opening", "lineups"):
        raise ServiceError("player props exist for the opening and lineups states only (D-017)")
    if settlement not in SETTLEMENTS:
        raise ServiceError(f"settlement must be one of {SETTLEMENTS}")
    if not b.has_state(event_id, state):
        raise ServiceError(f"no {state} inputs for event {event_id}")
    inp = b.inputs(event_id, state)
    res = _sim(b, event_id, state)
    cond = conditional_marginals(res, res.started if settlement == "start" else res.on_pitch)
    p_app, p_start = res.on_pitch.mean(axis=0), res.started.mean(axis=0)
    rows = []
    for i, pid in enumerate(inp.player_id):
        band = (
            "rotation"
            if inp.p_start[i] < 0.5
            else ("likely" if inp.p_start[i] <= 0.85 else "nailed")
        )
        offered = (
            (inp.p_start[i] >= 0.5 if state == "lineups" else p_start[i] > 0.02)
            if settlement == "start" else p_app[i] > 0.02
        )  # fmt: skip
        for mk in MARKETS:
            p = float(cond[mk][i])
            if not np.isfinite(p):
                continue
            qy, qn = apply_margin(np.array([p, 1 - p]), overround, method)
            rows.append({
                "player_id": int(pid), "team": "home" if inp.team[i] == 0 else "away", "market": mk,
                "p_start_input": float(inp.p_start[i]), "p_appear": float(p_app[i]), "band": band,
                "offered": bool(offered), "fair_probability": p, "fair_odds": 1 / max(p, 1e-9),
                "quoted_odds_yes": float(1 / max(qy, 1e-9)), "quoted_odds_no": float(1 / max(qn, 1e-9)),
            })  # fmt: skip
    return {
        "event_id": event_id, "state": state, "settlement": settlement, "overround": overround, "method": method,
        "n_sims": res.n_sims, "label": "SIMULATION: seeded Phase 5 simulator, probabilities conditional on the settlement rule",
        "props": rows, "disclaimer": DISCLAIMER,
    }  # fmt: skip


def lineup_shock(b: Bundle, event_id: int, settlement: str = "start") -> list[dict[str, Any]]:
    """Opening-to-lineups change in each prop price (UPPER BOUND: lineups known with certainty)."""
    po = {
        (r["player_id"], r["market"]): r
        for r in props(b, event_id, "opening", settlement=settlement)["props"]
    }
    out = []
    for r in props(b, event_id, "lineups", settlement=settlement)["props"]:
        o = po.get((r["player_id"], r["market"]))
        if o is None or not r["offered"]:
            continue
        a, c = o["fair_probability"], r["fair_probability"]
        if not (0 < a < 1 and 0 < c < 1):
            continue
        out.append({
            "player_id": r["player_id"], "market": r["market"], "band": o["band"], "p_opening": a, "p_lineups": c,
            "delta_log_odds": float(np.log(c / (1 - c)) - np.log(a / (1 - a))), "relative_odds_change": float(a / c - 1.0),
        })  # fmt: skip
    return out


def price_sga(
    b: Bundle, event_id: int, state: str, legs: list[dict[str, Any]], overround: float = 0.10
) -> dict[str, Any]:
    _event(b, event_id)
    if state not in ("opening", "lineups"):
        raise ServiceError("SGAs exist for the opening and lineups states")
    if not b.has_state(event_id, state):
        raise ServiceError(f"no {state} inputs for event {event_id}")
    inp = b.inputs(event_id, state)
    sga = SGA.model_validate({"legs": legs})
    validate_sga(sga, {int(p): int(t) for p, t in zip(inp.player_id, inp.team, strict=True)})
    res = _sim(b, event_id, state)
    ind, ok = indicator_matrix(sga, res)
    pr = price_from_indicators(ind, ok, leg_ok_matrix(sga, res))
    joint = float(pr["joint"])  # type: ignore[arg-type]
    pr["quoted_odds_joint_with_margin"] = 1.0 / max(min(joint * (1 + overround), 0.9999), 1e-9)
    pr["overround"] = overround
    pr["label"] = (
        "SIMULATION: joint from the seeded Phase 5 simulator; naive = product of standalone legs"
    )
    pr["disclaimer"] = DISCLAIMER
    return pr


def team_path(b: Bundle, event_id: int, step_min: int = 5) -> dict[str, Any]:
    """1X2 and over 2.5 across the lifecycle: opening, lineups (same team prices), in-play minutes."""
    e = _event(b, event_id)
    mins = [float(m) for m in range(0, int(e["match_length_s"] // 60), step_min)]
    rho = b.inputs(event_id, "opening").rho if b.has_state(event_id, "opening") else 0.0
    pre = _dc_probs(e, rho)
    pts = _inplay_probs(b, e, mins)
    return {
        "event_id": event_id,
        "pre_match": {"1x2": pre["1x2"], "over_2.5": pre["over_2.5"]},
        "in_play": [{"minute": m, "1x2": p["1x2"], "over_2.5": p["over_2.5"], "score": [p["state"]["h0"], p["state"]["a0"]], "dismissals": [p["state"]["red_h"], p["state"]["red_a"]]} for m, p in zip(mins, pts, strict=True)],
        "timeline": e["timeline"],
    }  # fmt: skip


def model_health(metrics_dir: Path | None = None) -> dict[str, Any]:
    """Provenance and the headline findings, including the nulls, read from the metrics files."""
    md = metrics_dir or evidence.METRICS_DIR
    claims = evidence.resolve_all(md)

    def g(k: str) -> str:
        return str(claims[k]["text"])

    findings = [
        f"Pre-match: Dixon-Coles 1X2 log loss {g('a_model_ll')} vs Pinnacle closing {g('a_market_ll')} on {g('a_n_matches')} matches (2024/25): the market is better.",
        f"Pre-match: the model's weight in a log-linear pool with the market is {g('a_enc_weight')} [{g('a_enc_lo')}, {g('a_enc_hi')}]: no information beyond the market.",
        f"Pre-match: the closing line does not move toward the model (slope {g('a_move_slope')} [{g('a_move_lo')}, {g('a_move_hi')}]).",
        f"Props: {g('p4_props_better')} of 6 markets beat the rolling baseline (anytime scorer {g('p4_anytime_diff')} log loss); shots 1+ is a null ({g('p4_shots1_diff')}).",
        f"SGAs: the simulated joint beats the naive product in {g('j_better')} of {g('j_n_tests')} pre-registered tests and loses in {g('j_worse')} (pooled log loss {g('j_sim_ll')} vs {g('j_naive_ll')}).",
        f"Lineup shock (UPPER BOUND): {g('c_shock_rot')} of rotation-risk prop prices move more than 25% in odds vs {g('c_shock_nailed')} for nailed starters.",
        f"In-play: the model beats the naive in-play baseline in {g('i_better')} of {g('i_n_tests')} tests and loses in {g('i_worse')}; no in-play market prices exist in the data, so no comparison with a market is possible.",
    ]
    ci = evidence.load_metric("ci_local.json", md)
    health: dict[str, Any] = {
        "findings": findings, "claims": claims, "disclaimer": DISCLAIMER,
        "ci_local": {"commit_sha": ci["commit_sha"], "all_passed": ci["all_passed"], "created_at": ci["created_at"]},
        "files": sorted(p.name for p in md.glob("*.json")),
    }  # fmt: skip
    shadow = md / "shadow_report.json"
    if shadow.exists():
        health["shadow"] = json.loads(shadow.read_text(encoding="utf-8"))
    return health
