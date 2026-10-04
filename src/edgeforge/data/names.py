"""Team-name mapping football-data <-> StatsBomb, and the StatsBomb <-> football-data match link.

The mapping is bootstrapped from matches that join uniquely on (league, date, home goals, away
goals), then reviewed by a human and committed (configs/team_name_map.csv, names only).
"""

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

LEAGUE_CODE = {"Premier League": "E0", "La Liga": "SP1", "Serie A": "I1", "Ligue 1": "F1"}
MAP_COLUMNS = ["league", "fd_name", "sb_name", "support", "method", "reviewed"]


def bootstrap_candidates(fd: pd.DataFrame, sb: pd.DataFrame) -> pd.DataFrame:
    """Candidate name pairs from unique (league, date, fthg, ftag) joins.

    `fd` needs league, date, home, away, fthg, ftag; `sb` needs league, match_date (datetime),
    home, away, home_score, away_score.
    """
    f = fd.assign(key_h=fd["fthg"].astype("Int64"), key_a=fd["ftag"].astype("Int64"))
    s = sb.assign(key_h=sb["home_score"].astype("Int64"), key_a=sb["away_score"].astype("Int64"))
    f = f.rename(columns={"home": "fd_home", "away": "fd_away", "date": "d"})
    s = s.rename(columns={"home": "sb_home", "away": "sb_away", "match_date": "d"})
    keys = ["league", "d", "key_h", "key_a"]
    f_n = f.groupby(keys).size().rename("n_fd")
    s_n = s.groupby(keys).size().rename("n_sb")
    j = f.merge(s, on=keys).merge(f_n, on=keys).merge(s_n, on=keys)
    j = j[(j["n_fd"] == 1) & (j["n_sb"] == 1)]
    pairs = pd.concat(
        [
            j[["league", "fd_home", "sb_home"]].set_axis(["league", "fd_name", "sb_name"], axis=1),
            j[["league", "fd_away", "sb_away"]].set_axis(["league", "fd_name", "sb_name"], axis=1),
        ]
    )
    cand = pairs.groupby(["league", "fd_name", "sb_name"]).size().rename("support").reset_index()
    cand = cand.sort_values(["league", "fd_name", "support"], ascending=[True, True, False])
    best = cand.groupby(["league", "fd_name"], as_index=False).first()
    best["method"] = "date_score_unique_join"
    best["reviewed"] = "pending"
    return best[MAP_COLUMNS].reset_index(drop=True)


def write_map(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")


def load_map(path: Path, require_reviewed: bool = True) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8", dtype=str, keep_default_na=False)
    if list(df.columns) != MAP_COLUMNS:
        raise ValueError(f"{path}: expected columns {MAP_COLUMNS}")
    if require_reviewed and not (df["reviewed"] == "yes").all():
        raise ValueError(
            f"{path}: rows not reviewed: {df[df['reviewed'] != 'yes']['fd_name'].tolist()}"
        )
    for col in ("fd_name", "sb_name"):
        dup = df[df.duplicated(["league", col], keep=False)]
        if len(dup):
            raise ValueError(f"{path}: duplicate {col} within league: {dup[col].tolist()}")
    return df


def link_matches(
    fd: pd.DataFrame, sb: pd.DataFrame, name_map: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Join StatsBomb matches to football-data matches on (league, date, mapped home, mapped away).

    Returns (links, fd_unlinked, sb_unlinked).
    """
    m = name_map.set_index(["league", "fd_name"])["sb_name"].to_dict()
    f = fd.copy()
    f["sb_home"] = [m.get((lg, h)) for lg, h in zip(f["league"], f["home"], strict=True)]
    f["sb_away"] = [m.get((lg, a)) for lg, a in zip(f["league"], f["away"], strict=True)]
    s = sb.rename(columns={"home": "sb_home", "away": "sb_away"})
    j = f.merge(
        s, on=["league", "sb_home", "sb_away"], how="outer", suffixes=("", "_sb"), indicator=True
    )
    j = j[(j["_merge"] == "both") & (j["date"] == j["match_date"])]
    links = pd.DataFrame(
        {
            "sb_match_id": j["sb_match_id"].astype("int64"),
            "fd_match_id": j["match_id"],
            "league": j["league"],
            "date": j["date"],
            "link_method": "date+mapped_names",
        }
    ).reset_index(drop=True)
    fd_unlinked = fd[~fd["match_id"].isin(links["fd_match_id"])]
    sb_unlinked = sb[~sb["sb_match_id"].isin(links["sb_match_id"])]
    return links, fd_unlinked, sb_unlinked
