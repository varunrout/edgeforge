"""Build the Parquet tables and the DuckDB warehouse (D-005, D-028: data/ is git-ignored)."""

import json
import logging
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from edgeforge.config import PROJECT_ROOT, load_config, resolve_path
from edgeforge.data import fd_tables, names, statsbomb
from edgeforge.data.sb_tables import build_sb_tables
from edgeforge.data.team_season import build_team_season
from edgeforge.provenance import dir_digest, provenance

log = logging.getLogger(__name__)

TABLES = (
    "matches",
    "odds",
    "team_name_map",
    "sb_matches",
    "sb_match_link",
    "player_match",
    "shots",
    "events_timeline",
    "sb_match_checks",
    "team_season",
)


def sb_match_frame(cfg: dict[str, Any]) -> pd.DataFrame:
    """StatsBomb in-scope matches (from the cached `matches` files only)."""
    fetcher = statsbomb.make_fetcher(cfg)
    rows = []
    for m in statsbomb.in_scope_matches(fetcher, cfg):
        rows.append(
            {
                "sb_match_id": m["match_id"],
                "league": names.LEAGUE_CODE[m["_competition"]],
                "match_date": pd.Timestamp(m["match_date"]),
                "home": m["home_team"]["home_team_name"],
                "away": m["away_team"]["away_team_name"],
                "home_score": m["home_score"],
                "away_score": m["away_score"],
            }
        )
    return pd.DataFrame(rows)


def bootstrap_team_names(cfg: dict[str, Any] | None = None) -> Path:
    """Write candidate name pairs (reviewed=pending) to the configured CSV."""
    cfg = cfg or load_config("data")
    sb = sb_match_frame(cfg)
    parts = []
    for f in fd_tables.cached_football_data(cfg):
        if f.season == "1516" and f.league in set(sb["league"]):
            parts.append(fd_tables.build_matches_for_file(f)[0])
    fd = pd.concat(parts, ignore_index=True)
    cand = names.bootstrap_candidates(fd, sb)
    path = resolve_path(cfg, "team_name_map")
    names.write_map(cand, path)
    log.info("wrote %d candidate pairs to %s", len(cand), path)
    return path


def build_warehouse(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    out_dir = resolve_path(cfg, "processed_dir")
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("football-data tables")
    matches, odds, fd_info = fd_tables.build_football_data(cfg)
    log.info("StatsBomb tables")
    sb = build_sb_tables(statsbomb.iter_payloads(cfg))
    name_map = names.load_map(resolve_path(cfg, "team_name_map"))

    sbm = sb["sb_matches"].copy()
    sbm["league"] = sbm["competition"].map(names.LEAGUE_CODE)
    fd1516 = matches[matches["season"] == "1516"]
    fd1516 = fd1516[fd1516["league"].isin(set(sbm["league"]))]
    links, fd_unlinked, sb_unlinked = names.link_matches(fd1516, sbm, name_map)
    team_season = build_team_season(matches, sbm, links, name_map)

    tables: dict[str, pd.DataFrame] = {
        "matches": matches,
        "odds": odds,
        "team_name_map": name_map,
        "sb_matches": sbm,
        "sb_match_link": links,
        "player_match": sb["player_match"],
        "shots": sb["shots"],
        "events_timeline": sb["events_timeline"],
        "sb_match_checks": sb["sb_match_checks"],
        "team_season": team_season,
    }
    for name, df in tables.items():
        df.to_parquet(out_dir / f"{name}.parquet", index=False)
        log.info("%s: %d rows", name, len(df))

    db_path = resolve_path(cfg, "warehouse")
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    for name in TABLES:
        pq = (out_dir / f"{name}.parquet").as_posix()
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{pq}')")
    con.close()

    info = {
        "provenance": provenance(
            "edgeforge build-warehouse",
            cfg,
            dir_digest(sorted(resolve_path(cfg, "raw_dir").glob("*/*.meta.json"))),
        ),
        "row_counts": {k: len(v) for k, v in tables.items()},
        "football_data_build": fd_info,
        "fd_2015_16_unlinked": fd_unlinked[["match_id", "league", "date", "home", "away"]]
        .astype({"date": str})
        .to_dict("records"),
        "sb_unlinked": sb_unlinked[["sb_match_id", "league", "home", "away"]].to_dict("records"),
    }
    path = resolve_path(cfg, "metrics_dir") / "warehouse_build.json"
    path.write_text(json.dumps(info, indent=1, default=str), encoding="utf-8")
    log.info("warehouse at %s", db_path.relative_to(PROJECT_ROOT))
    return db_path
