"""Shared test fixtures: a small synthetic serving bundle (no real data needed)."""

from pathlib import Path

import numpy as np
import pytest

from edgeforge.api.bundle import save_inputs, write_bundle_files
from edgeforge.evaluation.inplay_model import Params
from edgeforge.simulation.engine import N_MIN_COLS, MatchInputs, ShotsGivenGoals


def _inputs(eid: int, state: str, sampled: bool) -> MatchInputs:
    rng = np.random.default_rng(eid)
    per = 14
    p = 2 * per
    team = np.repeat([0, 1], per)
    pos = np.tile(np.arange(per), 2)
    ws = np.zeros((p, N_MIN_COLS))
    ws[:, -1] = 0.8
    ws[:, 12] = 0.2
    gs = np.tile(np.linspace(5, 95, N_MIN_COLS), (p, 1))
    wb = np.zeros((p, N_MIN_COLS))
    wb[:, 5] = 1.0
    gb = np.tile(np.linspace(80, 10, N_MIN_COLS), (p, 1))
    p_start = np.where(pos < 11, 0.9 if sampled else 1.0, 0.1 if sampled else 0.0)
    return MatchInputs(
        match_id=eid, state=state, lam_h=1.6, lam_a=1.1, rho=-0.1, mu_h=13.0, mu_a=10.0,
        player_id=np.arange(100, 100 + p), team=team, omega=rng.uniform(0.4, 1.8, p),
        g_shot=rng.uniform(0.04, 0.2, p), sot_cond=rng.uniform(0.2, 0.5, p), p_start=p_start,
        q_app_bench=np.where(pos < 11, 0.0, 0.4), ws=ws, gs=gs, wb=wb, gb=gb, sampled_roles=sampled,
    )  # fmt: skip


@pytest.fixture(scope="session")
def bundle_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bundle")
    timeline = [
        {"elapsed_s": 1200, "kind": "goal", "side": "home"},
        {"elapsed_s": 3000, "kind": "red_card", "side": "away"},
        {"elapsed_s": 4000, "kind": "goal", "side": "away"},
    ]
    meta = {
        "label": "synthetic", "n_sims": 4000, "seed": 7, "provenance": {},
        "events": [{
            "id": 1, "home": "Alpha", "away": "Beta", "date": "2016-01-01", "match_week": 21,
            "lam_h": 1.6, "lam_a": 1.1, "final_score": [1, 1], "match_length_s": 5700.0, "timeline": timeline,
        }],
    }  # fmt: skip
    model = ShotsGivenGoals(a=-0.9, b=1.0, c=0.1, d=0.0, alpha=0.1, pi_og=0.03)
    inplay = Params(np.zeros(7), np.zeros(5), -0.3, 0.4, 0.0, 5700.0, np.full(30, 5700.0))
    write_bundle_files(root, meta, model, inplay)
    save_inputs(root, _inputs(1, "opening", True))
    save_inputs(root, _inputs(1, "lineups", False))
    return root
