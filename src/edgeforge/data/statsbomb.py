"""StatsBomb Open Data fetcher (raw.githubusercontent.com), cached under data/raw/statsbomb.

Terms: StatsBomb Public Data User Agreement (see docs/DATA.md). Raw files are never committed.
"""

import json
import logging
from pathlib import Path
from typing import Any

from edgeforge.config import resolve_path
from edgeforge.data.http import CachedFetcher, FetchResult

log = logging.getLogger(__name__)


def make_fetcher(cfg: dict[str, Any]) -> CachedFetcher:
    http = cfg["http"]
    return CachedFetcher(
        cache_dir=resolve_path(cfg, "raw_dir") / "statsbomb",
        min_interval_s=float(http["min_interval_s"]),
        timeout_s=float(http["timeout_s"]),
        user_agent_base=str(http["user_agent_base"]),
    )


def _get(fetcher: CachedFetcher, cfg: dict[str, Any], rel: str, name: str) -> FetchResult:
    res = fetcher.get(f"{cfg['statsbomb']['base_url']}/{rel}", name)
    if res.status != 200:
        raise RuntimeError(f"StatsBomb returned HTTP {res.status} for {res.url}")
    return res


def load_json(res: FetchResult) -> Any:
    return json.loads(res.path.read_text(encoding="utf-8"))


def fetch_competitions(fetcher: CachedFetcher, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    data = load_json(_get(fetcher, cfg, "competitions.json", "competitions"))
    assert isinstance(data, list)
    return data


def scope_competitions(
    competitions: list[dict[str, Any]], cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    """Competition-seasons in scope: the configured season name and competition names."""
    sb = cfg["statsbomb"]
    return [
        c
        for c in competitions
        if c["season_name"] == sb["scope_season_name"]
        and c["competition_name"] in sb["scope_competitions"]
    ]


def fetch_matches(
    fetcher: CachedFetcher, cfg: dict[str, Any], competition_id: int, season_id: int
) -> list[dict[str, Any]]:
    rel = f"matches/{competition_id}/{season_id}.json"
    data = load_json(_get(fetcher, cfg, rel, f"matches_{competition_id}_{season_id}"))
    assert isinstance(data, list)
    return data


def fetch_match_files(
    fetcher: CachedFetcher, cfg: dict[str, Any], match_id: int
) -> tuple[Path, Path]:
    ev = _get(fetcher, cfg, f"events/{match_id}.json", f"events_{match_id}")
    lu = _get(fetcher, cfg, f"lineups/{match_id}.json", f"lineups_{match_id}")
    return ev.path, lu.path
