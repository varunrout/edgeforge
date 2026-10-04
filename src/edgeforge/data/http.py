"""Throttled, cached HTTP GET. Every response is stored on disk; cache hits cost no request."""

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchResult:
    url: str
    path: Path
    status: int
    from_cache: bool
    bytes: int


class CachedFetcher:
    def __init__(
        self,
        cache_dir: Path,
        min_interval_s: float = 1.0,
        timeout_s: float = 30.0,
        user_agent_base: str = "edgeforge-research/0.1",
        session: requests.Session | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        contact = os.environ.get("EDGEFORGE_CONTACT", "").strip()
        self.user_agent = f"{user_agent_base} {contact}".strip()
        self._session = session or requests.Session()
        self._last_request = 0.0
        self.request_count = 0

    def _paths(self, url: str, name: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(url.encode()).hexdigest()[:12]
        body = self.cache_dir / f"{name}.{digest}.body"
        return body, body.with_suffix(".meta.json")

    def get(self, url: str, name: str) -> FetchResult:
        body, meta = self._paths(url, name)
        if body.exists() and meta.exists():
            return FetchResult(
                url, body, json.loads(meta.read_text())["status"], True, body.stat().st_size
            )
        wait = self.min_interval_s - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        self.request_count += 1
        log.info("GET %s", url)
        t0 = time.monotonic()
        resp = self._session.get(
            url, headers={"User-Agent": self.user_agent}, timeout=self.timeout_s
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        body.write_bytes(resp.content)
        meta.write_text(
            json.dumps(
                {
                    "url": url,
                    "status": resp.status_code,
                    "content_type": resp.headers.get("Content-Type"),
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "bytes": len(resp.content),
                    "elapsed_s": round(time.monotonic() - t0, 3),
                }
            )
        )
        return FetchResult(url, body, resp.status_code, False, len(resp.content))
