"""D-064: how much of each league season the StatsBomb Open Data actually covers.

Reads the competitions list and the match list of every men's season of the five leagues football-data
covers (match lists only, never event files) and writes `artifacts/metrics/statsbomb_candidates.json`.
"""

import logging
from pathlib import Path
from typing import Any

from edgeforge.config import load_config, resolve_path
from edgeforge.data import statsbomb
from edgeforge.evaluation.common import write_json
from edgeforge.provenance import provenance

log = logging.getLogger(__name__)
LEAGUES = ("Premier League", "La Liga", "Serie A", "Ligue 1", "1. Bundesliga")
MIN_COVERAGE = 0.90  # D-064
EXCLUDED_SEASON = "2015/2016"  # the season the in-play model was built on


def run_coverage(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    fetcher = statsbomb.make_fetcher(cfg)
    comps = statsbomb.fetch_competitions(fetcher, cfg)
    rows: list[dict[str, Any]] = []
    for c in sorted(comps, key=lambda x: (x["competition_name"], x["season_name"])):
        if c["competition_name"] not in LEAGUES or c.get("competition_gender") != "male":
            continue
        matches = statsbomb.fetch_matches(fetcher, cfg, c["competition_id"], c["season_id"])
        teams = {m["home_team"]["home_team_name"] for m in matches} | {
            m["away_team"]["away_team_name"] for m in matches
        }
        n_t = len(teams)
        expected = n_t * (n_t - 1)
        cov = len(matches) / expected if expected else 0.0
        rows.append(
            {
                "competition": c["competition_name"],
                "season": c["season_name"],
                "matches_listed": len(matches),
                "teams_seen": n_t,
                "fixtures_expected": expected,
                "coverage": cov,
                "eligible": bool(cov >= MIN_COVERAGE and c["season_name"] != EXCLUDED_SEASON),
            }
        )
    eligible = [r for r in rows if r["eligible"]]
    out = {
        "provenance": provenance("edgeforge data statsbomb-coverage", cfg, "statsbomb-open-data"),
        "decision": "D-064",
        "rule": f"coverage of at least {MIN_COVERAGE:.0%} of teams x (teams - 1), season other than "
        f"{EXCLUDED_SEASON}",
        "n_league_seasons_examined": len(rows),
        "n_eligible": len(eligible),
        "eligible": eligible,
        "max_coverage_outside_2015_16": max(
            (r["coverage"] for r in rows if r["season"] != EXCLUDED_SEASON), default=0.0
        ),
        "rows": rows,
    }
    path = resolve_path(cfg, "metrics_dir") / "statsbomb_candidates.json"
    write_json(path, out)
    log.info("%d league seasons examined, %d eligible", len(rows), len(eligible))
    return path
