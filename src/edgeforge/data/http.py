"""Throttled, cached HTTP GET. 200 responses are stored on disk; cache hits cost no request."""

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
        max_retries: int = 5,
    ) -> None:
        self.cache_dir = cache_dir
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        contact = os.environ.get("EDGEFORGE_CONTACT", "").strip()
        self.user_agent = f"{user_agent_base} {contact}".strip()
        self._session = session or requests.Session()
        self._last_request = 0.0
        self.request_count = 0
        self.retry_count = 0
        self.max_retries = max_retries

    def _paths(self, url: str, name: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(url.encode()).hexdigest()[:12]
        body = self.cache_dir / f"{name}.{digest}.body"
        return body, body.with_suffix(".meta.json")

    def get(self, url: str, name: str) -> FetchResult:
        """Return a cached 200 response, else fetch with retries.

        Only HTTP 200 responses are cached. 429/5xx and connection errors are retried with
        exponential backoff; other statuses (e.g. 404) are returned uncached.
        """
        body, meta = self._paths(url, name)
        if body.exists() and meta.exists():
            info = json.loads(meta.read_text())
            if info["status"] == 200:
                return FetchResult(url, body, 200, True, body.stat().st_size)
        resp: requests.Response | None = None
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval_s - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            self.request_count += 1
            log.debug("GET %s (attempt %d)", url, attempt + 1)
            t0 = time.monotonic()
            try:
                resp = self._session.get(
                    url, headers={"User-Agent": self.user_agent}, timeout=self.timeout_s
                )
            except requests.RequestException as exc:
                log.warning("request error for %s: %s", url, exc)
                resp = None
            else:
                if resp.status_code != 429 and resp.status_code < 500:
                    break
                log.warning("HTTP %d for %s", resp.status_code, url)
            if attempt < self.max_retries:
                self.retry_count += 1
                time.sleep(min(2.0**attempt * 2, 120.0))
        if resp is None:
            raise RuntimeError(f"no response for {url} after {self.max_retries + 1} attempts")
        if resp.status_code != 200:
            return FetchResult(url, body, resp.status_code, False, len(resp.content))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = body.with_suffix(".tmp")
        tmp.write_bytes(resp.content)
        tmp.replace(body)
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
        return FetchResult(url, body, 200, False, len(resp.content))
