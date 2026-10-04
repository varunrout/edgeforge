"""football-data.co.uk downloader (one CSV per league-season), cached under data/raw."""

import logging
from pathlib import Path
from typing import Any

from edgeforge.config import load_config, resolve_path
from edgeforge.data.http import CachedFetcher, FetchResult

log = logging.getLogger(__name__)


def season_url(base_url: str, season: str, league: str) -> str:
    return f"{base_url}/{season}/{league}.csv"


def make_fetcher(cfg: dict[str, Any]) -> CachedFetcher:
    http = cfg["http"]
    return CachedFetcher(
        cache_dir=resolve_path(cfg, "raw_dir") / "footballdata",
        min_interval_s=float(http["min_interval_s"]),
        timeout_s=float(http["timeout_s"]),
        user_agent_base=str(http["user_agent_base"]),
    )


def fetch_league_season(fetcher: CachedFetcher, base_url: str, season: str, league: str) -> FetchResult:
    return fetcher.get(season_url(base_url, season, league), f"{league}_{season}")


def fetch_proof(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    proof = cfg["footballdata"]["proof"]
    fetcher = make_fetcher(cfg)
    res = fetch_league_season(fetcher, cfg["footballdata"]["base_url"], proof["season"], proof["league"])
    if res.status != 200:
        raise RuntimeError(f"football-data returned HTTP {res.status} for {res.url}")
    log.info("cached %s (%d bytes, from_cache=%s)", res.path, res.bytes, res.from_cache)
    return res.path
