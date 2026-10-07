"""Phase 6b levers (D-053, D-055): margin allocation and configuration logic."""

import numpy as np
import pandas as pd
import pytest

from edgeforge.evaluation.phase6b import Config, quoted, run_config


@pytest.mark.parametrize("alloc", ["proportional", "power", "odds_ratio"])
@pytest.mark.parametrize("m", [0.05, 0.10, 0.20])
def test_every_allocation_hits_the_target_overround(alloc: str, m: float) -> None:
    p = np.array([0.25, 0.30, 0.50, 0.70, 0.78])  # both sides stay below the 0.9999 quote cap
    qy, qn = quoted(p, m, alloc)
    assert np.allclose(qy + qn, 1 + m, atol=1e-6)
    assert (qy > p).all() and (qn > 1 - p).all()


def test_power_and_odds_ratio_load_more_margin_on_long_shots_than_proportional() -> None:
    p = np.array([0.03, 0.05])
    prop, _ = quoted(p, 0.10, "proportional")
    for alloc in ("power", "odds_ratio"):
        qy, _ = quoted(p, 0.10, alloc)
        assert (qy / p > prop / p).all()  # relative margin on the long shot is larger
    fav = np.array([0.85])
    qy_fav, _ = quoted(fav, 0.10, "power")
    assert qy_fav[0] / 0.85 < 1.10  # and the favourite is charged less than proportional


def _frame() -> pd.DataFrame:
    n = 6
    return pd.DataFrame({
        "sb_match_id": [1, 1, 2, 2, 3, 3],
        "band": ["rotation", "nailed"] * 3,
        "appeared": [True, True, True, False, True, True],
        "is_starter": [False, True, False, False, True, True],
        "p_o": [0.2, 0.5, 0.2, 0.5, 0.2, 0.5],
        "p_o_s": [0.3, 0.5, 0.3, 0.5, 0.3, 0.5],
        "p_o2": [0.2] * n, "p_o2_s": [0.3] * n,
        "p_l": [0.6, 0.5, 0.6, 0.5, 0.35, 0.5],
        "y": [1.0, 0.0, 1.0, 0.0, 0.0, 1.0],
    })  # fmt: skip


def test_start_settlement_voids_non_starters_and_appearance_voids_non_appearers() -> None:
    d = _frame()
    app = run_config(d, Config("a"), 0.05, 1000.0, False, False, 0, {})
    sta = run_config(d, Config("s", rule="start"), 0.05, 1000.0, False, False, 0, {})
    # appearance rule: rows 0, 2, 4 bettable (0.2 -> 0.6, 0.6, 0.35); row 3 is void
    assert app["bets"] >= 3
    # start rule: only the starter row with p_l 0.35 > 0.3 * 1.05 can be bet
    assert sta["bets"] == 1
    assert app["informed_expected_per_offered"] > sta["informed_expected_per_offered"]


def test_offer_rule_removes_the_rotation_band_and_cap_shortens_long_shots() -> None:
    d = _frame()
    off = run_config(d, Config("o", offer=True), 0.05, 1000.0, False, False, 0, {})
    assert off["offer_retained"] == 0.5 and off["offered"] == 3
    capped = run_config(d, Config("c", cap=True), 0.05, 3.0, False, False, 0, {})
    assert capped["share_selections_shortened"] > 0
    free = run_config(d, Config("b"), 0.05, 1000.0, False, False, 0, {})
    assert capped["informed_expected_per_offered"] < free["informed_expected_per_offered"]
    assert free["share_selections_shortened"] == 0
