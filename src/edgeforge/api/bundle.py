"""Serving bundle: the derived artefacts the API and the app need (no raw data, no player names).

`edgeforge serve build` writes `data/serving/` (git-ignored, like all of data/) from the local
warehouse: per demo match the simulator inputs of both pre-match states (player ids only), the
Dixon-Coles intensities, the event times by side, the fitted in-play model and the shot model.
The API regenerates every simulation from the seed (D-045); nothing here is a result table.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from edgeforge.config import PROJECT_ROOT, load_config, resolve_path
from edgeforge.evaluation.inplay_model import Params
from edgeforge.provenance import provenance
from edgeforge.simulation.engine import MatchInputs, ShotsGivenGoals

log = logging.getLogger(__name__)
DEFAULT_DIR = PROJECT_ROOT / "data" / "serving"
STATES = ("opening", "lineups")
N_DEMO = 16


@dataclass
class Bundle:
    root: Path
    meta: dict[str, Any]
    model: ShotsGivenGoals
    inplay: Params
    events: dict[int, dict[str, Any]]
    cache: dict[tuple[int, str], Any] = field(default_factory=dict)

    def inputs(self, event_id: int, state: str) -> MatchInputs:
        z = np.load(self.root / f"match_{event_id}_{state}.npz")
        s = z["scalars"]
        return MatchInputs(
            match_id=int(event_id), state=state, lam_h=float(s[0]), lam_a=float(s[1]), rho=float(s[2]),
            mu_h=float(s[3]), mu_a=float(s[4]), player_id=z["player_id"], team=z["team"],
            omega=z["omega"], g_shot=z["g_shot"], sot_cond=z["sot_cond"], p_start=z["p_start"],
            q_app_bench=z["q_app_bench"], ws=z["ws"], gs=z["gs"], wb=z["wb"], gb=z["gb"],
            sampled_roles=bool(s[5]),
        )  # fmt: skip

    def has_state(self, event_id: int, state: str) -> bool:
        return (self.root / f"match_{event_id}_{state}.npz").exists()


def save_inputs(root: Path, inp: MatchInputs) -> None:
    np.savez_compressed(
        root / f"match_{inp.match_id}_{inp.state}.npz",
        scalars=np.array([inp.lam_h, inp.lam_a, inp.rho, inp.mu_h, inp.mu_a, float(inp.sampled_roles)]),
        player_id=inp.player_id, team=inp.team, omega=inp.omega, g_shot=inp.g_shot,
        sot_cond=inp.sot_cond, p_start=inp.p_start, q_app_bench=inp.q_app_bench,
        ws=inp.ws, gs=inp.gs, wb=inp.wb, gb=inp.gb,
    )  # fmt: skip


def params_to_json(p: Params) -> dict[str, Any]:
    return {"beta": p.beta.tolist(), "gamma": p.gamma.tolist(), "delta_own": p.delta_own,
            "delta_opp": p.delta_opp, "kappa": p.kappa, "l_bar": p.l_bar, "lengths": p.lengths.tolist()}  # fmt: skip


def params_from_json(d: dict[str, Any]) -> Params:
    return Params(
        np.array(d["beta"]),
        np.array(d["gamma"]),
        d["delta_own"],
        d["delta_opp"],
        d["kappa"],
        d["l_bar"],
        np.array(d["lengths"]),
    )


def write_bundle_files(
    root: Path, meta: dict[str, Any], model: ShotsGivenGoals, inplay: Params
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "bundle.json").write_text(
        json.dumps({"meta": meta, "model": model.__dict__, "inplay": params_to_json(inplay)}),
        encoding="utf-8",
    )


def load_bundle(root: Path | None = None) -> Bundle:
    root = root or DEFAULT_DIR
    path = root / "bundle.json"
    if not path.exists():
        raise FileNotFoundError(f"no serving bundle at {root}: run `uv run edgeforge serve build`")
    j = json.loads(path.read_text(encoding="utf-8"))
    events = {int(e["id"]): e for e in j["meta"]["events"]}
    return Bundle(
        root, j["meta"], ShotsGivenGoals(**j["model"]), params_from_json(j["inplay"]), events
    )


def build_bundle(cfg: dict[str, Any] | None = None, out_dir: Path | None = None) -> Path:
    """Build the bundle from the local warehouse and the Phase 4 cache (run phase4 first)."""
    from edgeforge.evaluation.inplay_model import build_segments, fit_params
    from edgeforge.evaluation.phase5_inputs import build_inputs, load_context
    from edgeforge.evaluation.phase5_run import _model
    from edgeforge.evaluation.phase7_run import load_data

    cfg = cfg or load_config("data")
    out = out_dir or DEFAULT_DIR
    out.mkdir(parents=True, exist_ok=True)
    ctx = load_context(cfg)
    model = _model(ctx)
    inputs = {s: build_inputs(ctx, s, "test")[0] for s in STATES}
    d7 = load_data(cfg)
    mm = d7["matches"]
    ev = d7["events"]
    both = sorted(set(inputs["opening"]) & set(inputs["lineups"]) & set(mm["sb_match_id"]))
    red_ids = sorted(ev[ev["kind"].isin(["red_card", "second_yellow"])]["sb_match_id"].unique())
    red_demo = [m for m in both if m in set(int(x) for x in red_ids)][:4]
    spaced = both[:: max(len(both) // (N_DEMO - len(red_demo)), 1)]
    demo = sorted(set(red_demo) | set(spaced[: N_DEMO - len(red_demo)]))
    info = (
        d7["con"]
        .execute("SELECT sb_match_id, match_date, match_week FROM sb_matches")
        .df()
        .set_index("sb_match_id")
    )
    rows = mm.set_index("sb_match_id")
    events_meta = []
    for m in demo:
        r = rows.loc[m]
        e = ev[ev["sb_match_id"] == m].sort_values("elapsed_s")
        evl = [
            {
                "elapsed_s": int(x.elapsed_s),
                "kind": str(x.kind),
                "side": "home" if x.team == r["home"] else "away",
            }
            for x in e.itertuples()
        ]
        events_meta.append({
            "id": int(m), "home": str(r["home"]), "away": str(r["away"]), "date": str(info.loc[m, "match_date"]),
            "match_week": int(info.loc[m, "match_week"]), "lam_h": float(r["lam_h"]), "lam_a": float(r["lam_a"]),
            "final_score": [int(r["home_score"]), int(r["away_score"])], "match_length_s": float(r["match_length_s"]),
            "timeline": evl,
        })  # fmt: skip
        for s in STATES:
            save_inputs(out, inputs[s][m])
    fit_ids = d7["ids"]["burn"] + d7["ids"]["tune"]
    lengths = mm[mm["sb_match_id"].isin(fit_ids)]["match_length_s"].to_numpy(float)
    l_bar = float(lengths.mean())
    kappa = float(
        json.loads((resolve_path(cfg, "metrics_dir") / "phase7_inplay.json").read_text())[
            "kappa_tuning"
        ]["value"]
    )
    seg = build_segments(mm[mm["sb_match_id"].isin(fit_ids)], ev, l_bar)
    inplay = fit_params(seg, kappa, lengths)
    meta = {
        "provenance": provenance("edgeforge serve build", cfg, ctx.version),
        "label": "Historical replay of StatsBomb 2015/16 test matches (matchweeks 20-38); not live",
        "n_sims": int(load_config("phase5")["n_sims"]),
        "seed": int(cfg["seed"]),
        "events": events_meta,
    }
    write_bundle_files(out, meta, model, inplay)
    log.info("bundle with %d events written to %s", len(demo), out)
    return out
