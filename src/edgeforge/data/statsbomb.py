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


def in_scope_matches(fetcher: CachedFetcher, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """All matches of the configured competition-seasons, annotated with competition name."""
    out: list[dict[str, Any]] = []
    for c in scope_competitions(fetch_competitions(fetcher, cfg), cfg):
        for m in fetch_matches(fetcher, cfg, c["competition_id"], c["season_id"]):
            out.append({**m, "_competition": c["competition_name"]})
    return out


def download_all(cfg: dict[str, Any]) -> Path:
    """Fetch events and lineups for every in-scope match (D-026). Resumable via the cache."""
    import time

    from edgeforge.provenance import provenance

    fetcher = make_fetcher(cfg)
    matches = in_scope_matches(fetcher, cfg)
    t0 = time.monotonic()
    new_bytes = cached_bytes = new_files = cached_files = 0
    failures: list[dict[str, Any]] = []
    for i, m in enumerate(matches, start=1):
        for kind in ("events", "lineups"):
            res = _get_soft(fetcher, cfg, f"{kind}/{m['match_id']}.json", f"{kind}_{m['match_id']}")
            if res.status != 200:
                failures.append({"match_id": m["match_id"], "kind": kind, "status": res.status})
            elif res.from_cache:
                cached_files += 1
                cached_bytes += res.bytes
            else:
                new_files += 1
                new_bytes += res.bytes
        if i % 50 == 0 or i == len(matches):
            log.info(
                "progress %d/%d matches, %.1f MB new, %.0f s elapsed, %d retries",
                i,
                len(matches),
                new_bytes / 1e6,
                time.monotonic() - t0,
                fetcher.retry_count,
            )
    raw = resolve_path(cfg, "raw_dir") / "statsbomb"
    from edgeforge.provenance import dir_digest

    out = {
        "provenance": provenance(
            "edgeforge data fetch-statsbomb-all", cfg, dir_digest(sorted(raw.glob("*.meta.json")))
        ),
        "n_matches": len(matches),
        "files_downloaded_this_run": new_files,
        "bytes_downloaded_this_run": new_bytes,
        "files_already_cached": cached_files,
        "bytes_already_cached": cached_bytes,
        "http_requests_this_run": fetcher.request_count,
        "retries_this_run": fetcher.retry_count,
        "elapsed_seconds_this_run": round(time.monotonic() - t0, 1),
        "failures": failures,
    }
    path = resolve_path(cfg, "metrics_dir") / "statsbomb_download.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    log.info("wrote %s", path)
    return path


def _get_soft(fetcher: CachedFetcher, cfg: dict[str, Any], rel: str, name: str) -> FetchResult:
    return fetcher.get(f"{cfg['statsbomb']['base_url']}/{rel}", name)
