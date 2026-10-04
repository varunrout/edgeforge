from pathlib import Path

import requests

from edgeforge.data.footballdata import season_url
from edgeforge.data.http import CachedFetcher


class FakeResponse:
    status_code = 200
    content = b"Div,Date\nE0,01/01/25\n"
    headers = {"Content-Type": "text/csv"}


class FakeSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def get(self, url, **kwargs):  # type: ignore[no-untyped-def,override]
        self.calls += 1
        return FakeResponse()


def test_second_get_is_served_from_cache(tmp_path: Path) -> None:
    sess = FakeSession()
    f = CachedFetcher(tmp_path, min_interval_s=0.0, session=sess)
    first = f.get("https://example.test/a.csv", "a")
    second = f.get("https://example.test/a.csv", "a")
    assert sess.calls == 1
    assert not first.from_cache and second.from_cache
    assert second.path.read_bytes() == FakeResponse.content


def test_throttle_spaces_requests(tmp_path: Path) -> None:
    import time

    f = CachedFetcher(tmp_path, min_interval_s=0.2, session=FakeSession())
    t0 = time.monotonic()
    f.get("https://example.test/a.csv", "a")
    f.get("https://example.test/b.csv", "b")
    assert time.monotonic() - t0 >= 0.19


def test_season_url() -> None:
    assert season_url("https://x/mmz4281", "2425", "E0") == "https://x/mmz4281/2425/E0.csv"


class FlakySession(requests.Session):
    def __init__(self, statuses: list[int]) -> None:
        super().__init__()
        self.statuses = statuses
        self.calls = 0

    def get(self, url, **kwargs):  # type: ignore[no-untyped-def,override]
        self.calls += 1
        r = FakeResponse()
        r.status_code = self.statuses.pop(0)
        return r


def test_errors_are_retried_and_not_cached(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import time

    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FlakySession([429, 503, 200])
    f = CachedFetcher(tmp_path, min_interval_s=0.0, session=sess)
    res = f.get("https://example.test/a.csv", "a")
    assert res.status == 200 and sess.calls == 3 and f.retry_count == 2
    sess2 = FlakySession([404])
    f2 = CachedFetcher(tmp_path / "x", min_interval_s=0.0, session=sess2)
    r404 = f2.get("https://example.test/b.csv", "b")
    assert r404.status == 404 and not r404.path.exists()
