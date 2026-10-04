"""team_season: promoted flag (from football-data league membership) and manager-change flag."""

from typing import Any

import pandas as pd


def in_scope(cfg: dict[str, Any], league: str, start_year: int) -> bool:
    return any(
        league in rule["leagues"]
        and rule["first_start_year"] <= start_year <= rule["last_start_year"]
        for rule in cfg["scope"]
    )


def build_team_season(
    matches: pd.DataFrame, sb_matches: pd.DataFrame, links: pd.DataFrame, name_map: pd.DataFrame
) -> pd.DataFrame:
    """One row per (league, season, team) seen in football-data.

    promoted: True/False when the same league's previous season (start_year - 1) is cached,
    else null. Promoted means the team was absent from that league the season before. Teams are
    matched by football-data name; a club that was renamed would be flagged promoted in error,
    which `validate` surfaces through per-season promoted counts.

    manager_change_flag: only where StatsBomb managers exist (2015/16 in-scope leagues): True if
    more than one distinct manager id appears across the team's matches. Null elsewhere.
    """
    long = pd.concat(
        [
            matches[["league", "season", "season_start_year", "home"]].rename(
                columns={"home": "team"}
            ),
            matches[["league", "season", "season_start_year", "away"]].rename(
                columns={"away": "team"}
            ),
        ]
    )
    counts = long.groupby(["league", "season", "season_start_year", "team"]).size()
    ts = counts.rename("n_matches").reset_index()
    members = {
        (lg, y): set(g["team"]) for (lg, y), g in ts.groupby(["league", "season_start_year"])
    }

    def promoted(row: pd.Series) -> Any:
        prev = members.get((row["league"], row["season_start_year"] - 1))
        if prev is None:
            return pd.NA
        return row["team"] not in prev

    ts["promoted"] = ts.apply(promoted, axis=1).astype("boolean")
    ts["prev_season_cached"] = [
        (lg, y - 1) in members for lg, y in zip(ts["league"], ts["season_start_year"], strict=True)
    ]
    # managers (StatsBomb, linked matches only)
    fd_to_sb = name_map.set_index(["league", "fd_name"])["sb_name"].to_dict()
    mg = sb_matches.merge(links[["sb_match_id", "fd_match_id"]], on="sb_match_id")
    rows = []
    for _, r in mg.iterrows():
        rows.append((r["league"], r["home"], r["home_manager_ids"]))
        rows.append((r["league"], r["away"], r["away_manager_ids"]))
    mdf = pd.DataFrame(rows, columns=["league", "sb_name", "mids"])
    agg = []
    for (lg, sbn), g in mdf.groupby(["league", "sb_name"]):
        ids = {i for x in g["mids"] for i in str(x).split(",") if i}
        agg.append(
            {
                "league": lg,
                "sb_name": sbn,
                "n_managers_seen": len(ids),
                "manager_coverage": float((g["mids"] != "").mean()),
            }
        )
    adf = pd.DataFrame(agg)
    ts["sb_name"] = [
        fd_to_sb.get((lg, t)) if season == "1516" else None
        for lg, season, t in zip(ts["league"], ts["season"], ts["team"], strict=True)
    ]
    ts = ts.merge(adf, on=["league", "sb_name"], how="left")
    ts["manager_change_flag"] = pd.array(
        [pd.NA if pd.isna(n) else bool(n > 1) for n in ts["n_managers_seen"]], dtype="boolean"
    )
    return ts.drop(columns=["sb_name"])
