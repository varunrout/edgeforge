"""Pre-registered Pillar A segments (D-037). Definitions are fixed here, before any test-season
result is examined.

  league          E0, D1, SP1, I1, F1
  season phase    a match is 'early' if either team is in its first 6 league matches of the season
  FLB bands       selection implied probability < 0.20, 0.20-0.40, 0.40-0.60, > 0.60
  draw            calibration of the draw outcome
  promoted        matches with a promoted team: its first 10 league matches versus later
  snapshot        early-market snapshot (D-024) versus close
"""

import duckdb
import numpy as np
import pandas as pd

EARLY_PHASE_MATCHES = 6
PROMOTED_FIRST_N = 10
FLB_BANDS = ((0.0, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 1.0001))
EARLY_SNAPSHOT_LABEL = "early-market snapshot (approx. 1 to 3 days pre-kickoff, not timestamped)"


def load_promoted(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("SELECT league, season, team, promoted FROM team_season").df()


def team_match_numbers(fd_all: pd.DataFrame) -> pd.DataFrame:
    """For every match: the home and away team's league match number within the season.

    Fixture order is a calendar fact known before kickoff, so this is not outcome information.
    """
    long = pd.concat(
        [
            fd_all[["match_id", "league", "season", "home", "date"]]
            .rename(columns={"home": "team"})
            .assign(side="home"),
            fd_all[["match_id", "league", "season", "away", "date"]]
            .rename(columns={"away": "team"})
            .assign(side="away"),
        ]
    ).sort_values(["league", "season", "team", "date", "match_id"])
    long["n"] = long.groupby(["league", "season", "team"]).cumcount() + 1
    h = long[long["side"] == "home"].set_index("match_id")["n"].rename("home_n")
    a = long[long["side"] == "away"].set_index("match_id")["n"].rename("away_n")
    return pd.concat([h, a], axis=1).reset_index()


def add_segments(ev: pd.DataFrame, fd_all: pd.DataFrame, promoted: pd.DataFrame) -> pd.DataFrame:
    """Attach segment flags to a frame of matches (needs match_id, league, season, home, away)."""
    nums = team_match_numbers(fd_all)
    out = ev.merge(nums, on="match_id", how="left")
    pm = promoted[promoted["promoted"].fillna(False).astype(bool)][["league", "season", "team"]]
    pm = pm.assign(is_promoted=True)
    out = out.merge(
        pm.rename(columns={"team": "home", "is_promoted": "home_promoted"}),
        on=["league", "season", "home"],
        how="left",
    ).merge(
        pm.rename(columns={"team": "away", "is_promoted": "away_promoted"}),
        on=["league", "season", "away"],
        how="left",
    )
    out["home_promoted"] = out["home_promoted"].fillna(False).astype(bool)
    out["away_promoted"] = out["away_promoted"].fillna(False).astype(bool)
    out["early_phase"] = (out["home_n"] <= EARLY_PHASE_MATCHES) | (
        out["away_n"] <= EARLY_PHASE_MATCHES
    )
    out["promoted_involved"] = out["home_promoted"] | out["away_promoted"]
    out["promoted_first10"] = (out["home_promoted"] & (out["home_n"] <= PROMOTED_FIRST_N)) | (
        out["away_promoted"] & (out["away_n"] <= PROMOTED_FIRST_N)
    )
    out["promoted_later"] = out["promoted_involved"] & ~out["promoted_first10"]
    return out


def flb_band(p: np.ndarray) -> np.ndarray:
    """Band index 0-3 for implied probabilities."""
    idx = np.zeros(len(p), dtype=int)
    for i, (lo, hi) in enumerate(FLB_BANDS):
        idx[(p >= lo) & (p < hi)] = i
    return idx
