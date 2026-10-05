"""Leakage tests (PLAN Phase 2, D-033): synthetic fixtures plus one real-data smoke test.

(a) altering any row at or after asof_ts leaves features unchanged
(b) minutes / subs / cards / post-kickoff stats never appear as features in opening or lineups
(c) the in-play state at elapsed second t uses only events before t
(d) no fitting or tuning code reads a test-window match_id
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from edgeforge.evaluation.splits import LeakageError, SplitGuard, build_splits
from edgeforge.features.pit import (
    FORBIDDEN_FEATURE_COLUMNS,
    assert_no_forbidden,
    eligible_history,
    fd_clock,
    feature_columns,
    inplay_state,
    league_frequency_features,
    player_features,
    position_group_rates,
    register_clocks,
    sb_clock,
    team_form_features,
)

BASE = pd.Timestamp("2015-09-01 15:00")


def _sb_matches(n: int = 6) -> pd.DataFrame:
    """n matches of one league, one week apart, 15:00 kickoff, team 1 vs team 2 always."""
    rows = []
    for i in range(n):
        d = (BASE + pd.Timedelta(days=7 * i)).normalize()
        rows.append(
            {
                "sb_match_id": 100 + i,
                "league": "E0",
                "match_week": i + 1,
                "match_date": d,
                "kick_off": "15:00:00.000",
            }
        )
    return pd.DataFrame(rows)


def _player_rows(n: int = 6, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        for pid, team in ((1, 10), (2, 10), (3, 20), (4, 20)):
            started = pid in (1, 3)
            appeared = started or bool(rng.integers(0, 2))
            rows.append(
                {
                    "sb_match_id": 100 + i,
                    "player_id": pid,
                    "team_id": team,
                    "started": started,
                    "appeared": appeared,
                    "minutes": float(rng.integers(20, 95)) if appeared else 0.0,
                    "shots": int(rng.integers(0, 4)) if appeared else 0,
                    "shots_on_target": int(rng.integers(0, 2)) if appeared else 0,
                    "goals": int(rng.integers(0, 2)) if appeared else 0,
                    "position_group": "FWD" if pid in (1, 3) else "MID",
                    "yellow": int(rng.integers(0, 2)),
                    "red": 0,
                    "second_yellow": 0,
                    "sub_on_s": None,
                    "sub_off_s": None,
                    "penalties_taken": 0,
                    "exit_kind": "substitution"
                    if (appeared and started and rng.random() < 0.4)
                    else None,
                    "xg": float(rng.random()),
                }
            )
    return pd.DataFrame(rows)


def _con(players: pd.DataFrame) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.register("player_match", players)
    register_clocks(con, None, sb_clock(_sb_matches()))
    return con


def _features(players: pd.DataFrame, state: str, target: int, exclude: list[int] | None = None):  # type: ignore[no-untyped-def]
    con = _con(players)
    f = player_features(con, state, [target], exclude)
    return f.sort_values(["player_id"]).reset_index(drop=True)


def _mutate(players: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    out = players.copy()
    rng = np.random.default_rng(99)
    for col in ("minutes", "shots", "shots_on_target", "goals", "yellow", "xg"):
        out.loc[mask, col] = out.loc[mask, col] + rng.integers(1, 50, size=int(mask.sum()))
    out.loc[mask, "appeared"] = True
    return out


# ------------------------------------------------------------------------------------- (a)


@pytest.mark.parametrize("state", ["lineups", "opening"])
def test_a_rows_at_or_after_asof_do_not_change_player_features(state: str) -> None:
    players = _player_rows()
    target = 103  # match index 3
    base = _features(players, state, target)
    assert len(base) > 0
    # mutate the target match and every later match: all complete after asof_ts
    later = players["sb_match_id"] >= target
    pd.testing.assert_frame_equal(base, _features(_mutate(players, later), state, target))


def test_a_result_delay_window_is_excluded() -> None:
    """A prior match that kicked off 1h before asof_ts is still in play and must not count."""
    players = _player_rows()
    sb = _sb_matches()
    # make match 102 kick off only 30 minutes before match 103's lineups as-of time
    sb.loc[sb["sb_match_id"] == 102, "match_date"] = pd.Timestamp("2015-09-22")
    sb.loc[sb["sb_match_id"] == 102, "kick_off"] = "13:30:00.000"
    sb.loc[sb["sb_match_id"] == 103, "match_date"] = pd.Timestamp("2015-09-22")
    sb.loc[sb["sb_match_id"] == 103, "kick_off"] = "15:00:00.000"  # asof_lineups = 14:00 > 13:30

    def feats(pm: pd.DataFrame) -> pd.DataFrame:
        con = duckdb.connect(":memory:")
        con.register("player_match", pm)
        register_clocks(con, None, sb_clock(sb))
        return (
            player_features(con, "lineups", [103], None)
            .sort_values("player_id")
            .reset_index(drop=True)
        )

    mutated = _mutate(players, players["sb_match_id"] == 102)
    pd.testing.assert_frame_equal(feats(players), feats(mutated))


def test_a_power_check_earlier_rows_do_change_features() -> None:
    players = _player_rows()
    earlier = players["sb_match_id"] == 101
    a = _features(players, "lineups", 104)
    b = _features(_mutate(players, earlier), "lineups", 104)
    assert not a.equals(b)


def test_a_group_rates_ignore_rows_at_or_after_asof() -> None:
    players = _player_rows()
    con = _con(players)
    base = position_group_rates(con, "lineups", [103]).sort_values("position_group")
    con2 = _con(_mutate(players, players["sb_match_id"] >= 103))
    after = position_group_rates(con2, "lineups", [103]).sort_values("position_group")
    pd.testing.assert_frame_equal(base.reset_index(drop=True), after.reset_index(drop=True))


def _fd(n: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    rows = []
    for i in range(n):
        rows.append(
            {
                "match_id": f"E0_{i:03d}",
                "league": "E0",
                "season": "1516",
                "season_start_year": 2015,
                "date": pd.Timestamp("2015-08-01") + pd.Timedelta(days=3 * i),
                "kickoff_local": pd.NaT,
                "home": f"T{i % 4}",
                "away": f"T{(i + 1) % 4}",
                "fthg": int(rng.integers(0, 4)),
                "ftag": int(rng.integers(0, 4)),
            }
        )
    return fd_clock(pd.DataFrame(rows))


def test_a_league_and_team_features_ignore_matches_after_cutoff() -> None:
    fd = _fd()
    tgt = fd[fd["match_id"] == "E0_030"]
    league_t = pd.DataFrame(
        {"match_id": tgt["match_id"], "league": "E0", "cutoff_ts": tgt["model_cutoff"]}
    )
    team_t = pd.DataFrame(
        {
            "match_id": tgt["match_id"],
            "league": "E0",
            "home": tgt["home"],
            "away": tgt["away"],
            "asof_ts": tgt["asof_opening"],
            "state": "opening",
        }
    )

    def run(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        con = duckdb.connect(":memory:")
        register_clocks(con, frame, None)
        return league_frequency_features(con, league_t), team_form_features(con, team_t)

    base = run(fd)
    cutoff = tgt["asof_opening"].iloc[0]
    altered = fd.copy()
    late = altered["done_ts"] > cutoff
    altered.loc[late, "fthg"] = altered.loc[late, "fthg"] + 5
    altered.loc[late, "ftag"] = altered.loc[late, "ftag"] + 3
    after = run(altered)
    for x, y in zip(base, after, strict=True):
        pd.testing.assert_frame_equal(x, y)
    # asof_ts never later than the match's own opening as-of time
    assert (base[0]["asof_ts"] <= tgt["asof_opening"].iloc[0]).all()
    assert base[0]["n_prior"].iloc[0] > 0


def test_eligible_history_requires_completion_and_window() -> None:
    fd = _fd()
    cutoff = fd["asof_opening"].iloc[30]
    h = eligible_history(fd, cutoff, 30)
    assert (h["done_ts"] <= cutoff).all()
    assert (h["ko_late"] >= cutoff - pd.Timedelta(days=30)).all()
    assert len(h) < len(eligible_history(fd, cutoff, 365))


# ------------------------------------------------------------------------------------- (b)


@pytest.mark.parametrize("state", ["lineups", "opening"])
def test_b_no_post_kickoff_columns_in_feature_frames(state: str) -> None:
    f = _features(_player_rows(), state, 104)
    assert_no_forbidden(f)
    assert not (set(feature_columns(f)) & FORBIDDEN_FEATURE_COLUMNS)
    if state == "opening":
        assert "announced_starter" not in f.columns
    else:
        assert "announced_starter" in f.columns


def test_b_forbidden_columns_are_detected() -> None:
    bad = pd.DataFrame({"sb_match_id": [1], "player_id": [1], "minutes": [90.0]})
    with pytest.raises(AssertionError):
        assert_no_forbidden(bad)


def test_b_opening_candidates_exclude_players_not_seen_before() -> None:
    """Opening candidates come from prior squads, never from the target match's own lineup."""
    players = _player_rows()
    players.loc[players["sb_match_id"] == 104, "player_id"] += 100  # new ids only in match 104
    f = _features(players, "opening", 104)
    assert (f["player_id"] < 100).all()


# ------------------------------------------------------------------------------------- (c)


def _timeline_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.register("sb_matches", pd.DataFrame({"sb_match_id": [1], "home": ["H"], "away": ["A"]}))
    tl = pd.DataFrame(
        {
            "sb_match_id": [1, 1, 1, 1],
            "kind": ["goal", "red_card", "own_goal", "goal"],
            "team": ["H", "A", "H", "A"],
            "elapsed_s": [600, 1200, 2400, 4000],
        }
    )
    con.register("events_timeline", tl)
    return con


def test_c_inplay_uses_only_events_strictly_before_t() -> None:
    con = _timeline_con()
    s = inplay_state(con, 1, 2400)
    assert (s["home_goals"], s["away_goals"]) == (1, 0)  # own goal AT t=2400 is excluded
    assert (s["home_dismissals"], s["away_dismissals"]) == (0, 1)
    assert inplay_state(con, 1, 2401)["home_goals"] == 2
    assert inplay_state(con, 1, 600)["home_goals"] == 0  # event exactly at t excluded
    assert inplay_state(con, 1, 601)["home_goals"] == 1
    assert s["state"] == "inplay"


def test_c_changing_events_at_or_after_t_leaves_state_unchanged() -> None:
    t = 2400
    base = inplay_state(_timeline_con(), 1, t)
    con = _timeline_con()
    tl = con.execute("SELECT * FROM events_timeline").df()
    tl.loc[tl["elapsed_s"] >= t, "kind"] = "goal"
    tl.loc[tl["elapsed_s"] >= t, "team"] = "A"
    con.register("events_timeline", tl)
    assert inplay_state(con, 1, t) == base


# ------------------------------------------------------------------------------------- (d)


def test_d_guard_blocks_test_window_ids_in_tuning() -> None:
    guard = SplitGuard(frozenset({"fd_test"}), frozenset({7}))
    guard.check_tuning(["fd_ok", 1, 2], "ok")
    with pytest.raises(LeakageError):
        guard.check_tuning(["fd_test"], "tuning")
    with pytest.raises(LeakageError):
        guard.check_tuning([7], "tuning sb")
    assert guard.reads[0] == ("ok", 3)


def test_d_guard_blocks_fits_that_read_future_matches() -> None:
    guard = SplitGuard(frozenset(), frozenset())
    cutoff = pd.Timestamp("2016-01-04")
    guard.check_fit_before([pd.Timestamp("2016-01-03")], cutoff, "ok")
    with pytest.raises(LeakageError):
        guard.check_fit_before([pd.Timestamp("2016-01-05")], cutoff, "late")


def test_d_excluding_test_window_matches_makes_tuning_features_independent_of_them() -> None:
    players = _player_rows()
    test_ids = [104, 105]
    base = _features(players, "lineups", 103, exclude=test_ids)
    mutated = _mutate(players, players["sb_match_id"].isin(test_ids))
    pd.testing.assert_frame_equal(base, _features(mutated, "lineups", 103, exclude=test_ids))
    # group rates too
    g0 = position_group_rates(_con(players), "lineups", [103], test_ids)
    g1 = position_group_rates(_con(mutated), "lineups", [103], test_ids)
    pd.testing.assert_frame_equal(
        g0.sort_values("position_group").reset_index(drop=True),
        g1.sort_values("position_group").reset_index(drop=True),
    )


def test_d_split_blocks_are_disjoint_and_follow_the_matchweek_rule() -> None:
    con = duckdb.connect(":memory:")
    sbm = pd.DataFrame({"sb_match_id": range(1, 39), "match_week": range(1, 39)})
    con.register("sb_matches", sbm)
    fd_rows = []
    for yr in range(2005, 2026):
        for k in range(3):
            fd_rows.append(
                {
                    "match_id": f"E0_{yr}_{k}",
                    "league": "E0",
                    "season_start_year": yr,
                    "fthg": 1,
                    "ftag": 0,
                }
            )
    con.register("matches", pd.DataFrame(fd_rows))
    link = pd.DataFrame({"sb_match_id": [1, 20], "fd_match_id": ["E0_2015_0", "E0_2015_1"]})
    con.register("sb_match_link", link)
    s = build_splits(con)
    p = s["player_2015_16"]["ids"]
    assert set(p["burn_in_mw1_9"]) == set(range(1, 10))
    assert set(p["tune_mw10_19"]) == set(range(10, 20))
    assert set(p["test_mw20_38"]) == set(range(20, 39))
    assert not (set(p["tune_mw10_19"]) & set(p["test_mw20_38"]))
    a = s["pillar_a"]["ids"]
    assert not (set(a["burn_in_tune_2019_23"]) & set(a["test_primary_2024_25"]))
    assert not (set(a["test_primary_2024_25"]) & set(a["test_secondary_2025_26"]))
    assert all("2024" in i for i in a["test_primary_2024_25"])
    t = s["team_2015_16"]["ids"]
    assert t["test_2015_16_mw20_38"] == ["E0_2015_1"]
    assert not (set(t["decay_tuning_2013_14"]) & set(t["eval_2015_16_all"]))


# ------------------------------------------------------------------------------------- real data

WAREHOUSE = Path(__file__).resolve().parents[1] / "data" / "warehouse.duckdb"
SPLITS = Path(__file__).resolve().parents[1] / "artifacts" / "splits" / "player_2015_16.json"


@pytest.mark.skipif(not WAREHOUSE.exists(), reason="warehouse not built (CI uses fixtures only)")
def test_real_data_smoke_tune_features_do_not_read_test_window() -> None:
    import json

    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    ids = json.loads(SPLITS.read_text())["ids"]
    tune, test = ids["tune_mw10_19"], ids["test_mw20_38"]
    assert not (set(tune) & set(test))
    sbm = con.execute(
        "SELECT sb_match_id, league, match_week, match_date, kick_off FROM sb_matches"
    ).df()
    register_clocks(con, None, sb_clock(sbm))
    sample = [int(x) for x in np.random.default_rng(3).choice(tune, 25, replace=False)]
    base = player_features(con, "lineups", sample, test)
    assert_no_forbidden(base)
    assert (base["asof_ts"].notna()).all() and (base["state"] == "lineups").all()
    # every feature row's as-of time precedes its own kickoff by 60 minutes
    clock = sb_clock(sbm).set_index("sb_match_id")
    ko = clock.loc[base["sb_match_id"], "kickoff_early"].to_numpy()
    assert ((pd.to_datetime(ko) - base["asof_ts"]) == pd.Timedelta(minutes=60)).all()
    # perturbing the test window cannot move tuning features when it is excluded
    pm = con.execute("SELECT * FROM player_match").df()
    pm.loc[pm["sb_match_id"].isin(test), ["shots", "goals", "minutes"]] += 7
    con2 = duckdb.connect(":memory:")
    con2.register("player_match", pm)
    register_clocks(con2, None, sb_clock(sbm))
    alt = player_features(con2, "lineups", sample, test)
    key = ["sb_match_id", "player_id"]
    pd.testing.assert_frame_equal(
        base.sort_values(key).reset_index(drop=True), alt.sort_values(key).reset_index(drop=True)
    )
