"""Phase 4 data assembly (StatsBomb 2015/16 player block, D-033 split).

Two row sets, both as of the match's own information state:
  squad  - the announced squad (XI + named bench) of every match, features as of kickoff - 60 min
  cand   - opening-state candidates (appeared in the team's previous five matches), features as of
           kickoff - 48 h, with the start / bench / out outcome (candidates not in the squad = out)

Features of burn-in and tuning rows are built with every test-window match excluded from history
(D-033), features of test rows with nothing excluded (walk-forward: their past is legitimate).
Targets come from `player_match` and are never features.
"""

import logging
from dataclasses import dataclass
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from edgeforge.evaluation.common import connect
from edgeforge.evaluation.splits import load_split_ids
from edgeforge.features.pit import (
    assert_no_forbidden,
    monday_floor,
    player_features,
    position_group_rates,
    register_clocks,
    sb_clock,
    starter_features,
)

log = logging.getLogger(__name__)
ANCHOR = pd.Timestamp("2015-08-03")  # a Monday before the first 2015/16 match


def biweekly(cutoff: pd.Series) -> pd.Series:
    """Coarser refit cutoffs (every 14 days) for the expensive hazard models."""
    k = ((cutoff - ANCHOR).dt.days // 14).clip(lower=0)
    return ANCHOR + pd.to_timedelta(k * 14, unit="D")


@dataclass
class P4Data:
    con: duckdb.DuckDBPyConnection
    clock: pd.DataFrame
    ids: dict[str, list[int]]
    squad: pd.DataFrame
    cand: pd.DataFrame
    gr_lineups: pd.DataFrame
    gr_opening: pd.DataFrame
    team_shots: pd.DataFrame
    l_bar_by_match: pd.Series

    @property
    def test_ids(self) -> set[int]:
        return set(self.ids["test"])


OUTCOME_SQL = """
SELECT sb_match_id, player_id, team_id, is_home, team, started, named_bench, appeared, minutes,
       exit_kind, exit_s, sub_on_s, shots, shots_on_target AS sot, goals, xg, penalties_taken,
       position_group AS pos_actual
FROM player_match
"""


def _add_meta(df: pd.DataFrame, clock: pd.DataFrame, split_of: dict[int, str]) -> pd.DataFrame:
    c = clock[["sb_match_id", "done_ts", "match_week", "league", "asof_opening", "asof_lineups"]]
    c = c.assign(cutoff=monday_floor(clock["asof_opening"]))
    c = c.assign(cutoff2=biweekly(c["cutoff"]))
    out = df.drop(columns=[x for x in ("league",) if x in df.columns]).merge(c, on="sb_match_id")
    out["split"] = out["sb_match_id"].map(split_of)
    return out


def load_p4(cfg: dict[str, Any]) -> P4Data:
    con = connect(cfg)
    sbm = con.execute(
        "SELECT sb_match_id, league, match_week, match_date, kick_off, match_length_s, home, away"
        " FROM sb_matches"
    ).df()
    clock = sb_clock(sbm)
    register_clocks(con, None, clock)
    s = load_split_ids("player_2015_16")
    ids = {"burn": s["burn_in_mw1_9"], "tune": s["tune_mw10_19"], "test": s["test_mw20_38"]}
    split_of = {i: k for k, v in ids.items() for i in v}
    trainval = ids["burn"] + ids["tune"]
    test = ids["test"]
    out = con.execute(OUTCOME_SQL).df()

    def lineups(match_ids: list[int], exclude: list[int]) -> pd.DataFrame:
        f = player_features(con, "lineups", match_ids, exclude)
        assert_no_forbidden(f)
        return f

    sq = pd.concat([lineups(trainval, test), lineups(test, [])], ignore_index=True)
    sq = sq.merge(out, on=["sb_match_id", "player_id", "team_id"], how="left")
    sq = _add_meta(sq, clock, split_of)

    def opening(match_ids: list[int], exclude: list[int]) -> pd.DataFrame:
        f = player_features(con, "opening", match_ids, exclude)
        sf = starter_features(con, match_ids, exclude)
        keep = [c for c in sf.columns if c not in ("asof_ts", "state")]
        return f.merge(sf[keep], on=["sb_match_id", "player_id", "team_id"])

    cand = pd.concat([opening(trainval, test), opening(test, [])], ignore_index=True)
    cand = cand.merge(
        out, on=["sb_match_id", "player_id", "team_id"], how="left", suffixes=("", "_o")
    )
    cand["is_home"] = cand["is_home"].astype(bool)
    in_squad = cand["started"].notna()
    cand["cls"] = np.where(cand["started"].fillna(False).astype(bool), 2, np.where(in_squad, 1, 0))
    cand = _add_meta(cand, clock, split_of)

    gr_l = pd.concat(
        [
            position_group_rates(con, "lineups", trainval, test),
            position_group_rates(con, "lineups", test, []),
        ]
    )
    gr_o = pd.concat(
        [
            position_group_rates(con, "opening", trainval, test),
            position_group_rates(con, "opening", test, []),
        ]
    )
    ts = con.execute(
        "SELECT sb_match_id, is_home, team_id, sum(shots) AS shots, sum(shots_on_target) AS sot,"
        " sum(goals) AS goals, sum(xg) AS xg FROM player_match GROUP BY ALL"
    ).df()
    ts = _add_meta(ts, clock, split_of)
    l_bar = sbm.set_index("sb_match_id")["match_length_s"] / 60.0
    log.info("squad rows %d, candidate rows %d", len(sq), len(cand))
    return P4Data(con, clock, ids, sq, cand, gr_l, gr_o, ts, l_bar)
