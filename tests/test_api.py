"""Integration tests through the API with a small synthetic serving bundle (no real data needed)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from edgeforge.api.app import create_app


@pytest.fixture(scope="module")
def client(bundle_dir: Path) -> TestClient:
    return TestClient(create_app(bundle_dir))


def test_health_and_events(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    ev = client.get("/events").json()
    assert ev["events"][0]["id"] == 1 and "inplay" in ev["events"][0]["states"]
    assert "not a live feed" in ev["disclaimer"] and "claims an edge" in ev["disclaimer"]


@pytest.mark.parametrize("state", ["opening", "lineups"])
def test_prematch_markets_sum_to_one_and_carry_the_margin(client: TestClient, state: str) -> None:
    r = client.get(
        "/events/1/markets", params={"state": state, "overround": 0.10, "method": "power"}
    ).json()
    one_x_two = [x for x in r["team"]["lines"] if x["market"] == "1x2"]
    assert sum(x["fair_probability"] for x in one_x_two) == pytest.approx(1.0, abs=1e-6)
    assert sum(1 / x["quoted_odds"] for x in one_x_two) == pytest.approx(1.10, abs=1e-4)
    over = {x["selection"]: x for x in r["team"]["lines"] if x["market"] == "total_2.5"}
    assert 1 / over["over"]["quoted_odds"] + 1 / over["under"]["quoted_odds"] == pytest.approx(
        1.10, abs=1e-4
    )
    assert r["props"]["props"] and all(0 < p["fair_probability"] < 1 for p in r["props"]["props"])
    assert all("player_name" not in p for p in r["props"]["props"])  # ids only


def test_lineups_start_settlement_offers_starters_only_and_appearance_offers_bench(
    client: TestClient,
) -> None:
    start = client.get(
        "/events/1/markets", params={"state": "lineups", "settlement": "start"}
    ).json()["props"]["props"]
    assert {p["player_id"] for p in start if p["offered"]} <= {100 + i for i in range(11)} | {
        114 + i for i in range(11)
    }
    appear = client.get(
        "/events/1/markets", params={"state": "lineups", "settlement": "appear"}
    ).json()["props"]["props"]
    assert sum(p["offered"] for p in appear) > sum(p["offered"] for p in start)


def test_inplay_uses_only_events_before_the_minute_and_has_no_props(client: TestClient) -> None:
    early = client.get("/events/1/markets", params={"state": "inplay", "minute": 15}).json()
    assert early["team"]["score"] == [0, 0] and "props" not in early
    after_goal = client.get("/events/1/markets", params={"state": "inplay", "minute": 21}).json()
    assert after_goal["team"]["score"] == [1, 0]
    after_red = client.get("/events/1/markets", params={"state": "inplay", "minute": 51}).json()
    assert after_red["team"]["dismissals"] == [0, 1]

    def home(r: dict[str, object]) -> float:
        lines = r["team"]["lines"]  # type: ignore[index]
        pick = [x for x in lines if x["market"] == "1x2" and x["selection"] == "home"]
        return float(pick[0]["fair_probability"])

    assert home(after_goal) > home(early)


def test_bad_requests_get_clear_errors(client: TestClient) -> None:
    assert client.get("/events/99/markets", params={"state": "opening"}).status_code == 404
    assert (
        client.get("/events/1/markets", params={"state": "inplay", "minute": 200}).status_code
        == 400
    )
    assert client.get("/events/1/markets", params={"state": "bogus"}).status_code == 400
    assert (
        client.get("/events/1/markets", params={"state": "opening", "method": "magic"}).status_code
        == 400
    )
    assert (
        client.get("/events/1/markets", params={"state": "opening", "overround": 0.5}).status_code
        == 422
    )


def test_sga_pricing_validation_and_dependence(client: TestClient) -> None:
    ok = client.post("/sga/price", json={"event_id": 1, "state": "lineups", "legs": [
        {"kind": "team_result", "result": "home"}, {"kind": "player_ags", "player_id": 100}]})  # fmt: skip
    j = ok.json()
    assert (
        ok.status_code == 200
        and 0 < j["joint"] <= min(j["leg_probabilities"])
        and j["adjustment_ratio"] > 1.0
    )
    assert j["joint_mc_se"] > 0 and "SIMULATION" in j["label"]
    for legs in (
        [{"kind": "total", "line": 0.5, "side": "under"}, {"kind": "player_ags", "player_id": 100}],
        [{"kind": "player_ags", "player_id": 9999}, {"kind": "team_result", "result": "home"}],
        [{"kind": "btts", "side": "yes"}, {"kind": "btts", "side": "no"}],
    ):
        assert (
            client.post(
                "/sga/price", json={"event_id": 1, "state": "lineups", "legs": legs}
            ).status_code
            == 422
        )


def test_shock_and_path(client: TestClient) -> None:
    s = client.get("/events/1/shock").json()
    assert (
        "UPPER BOUND" in s["label"]
        and s["rows"]
        and {"delta_log_odds", "relative_odds_change"} <= set(s["rows"][0])
    )
    p = client.get("/events/1/path", params={"step_min": 15}).json()
    assert len(p["in_play"]) >= 6 and abs(sum(p["pre_match"]["1x2"]) - 1) < 1e-6


def test_model_health_reports_the_nulls_from_the_metrics_files(client: TestClient) -> None:
    h = client.get("/model-health").json()
    text = " ".join(h["findings"])
    assert "the market is better" in text and "no information beyond the market" in text
    assert "no comparison with a market is possible" in text
    assert h["claims"]["a_market_ll"]["source"] == "baseline_team.json"
    assert h["ci_local"]["all_passed"] is True


def test_trader_app_runs_on_the_bundle_and_states_the_nulls(
    bundle_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("EDGEFORGE_BUNDLE", str(bundle_dir))
    app_file = Path(__file__).resolve().parents[1] / "apps" / "trader" / "app.py"
    at = AppTest.from_file(str(app_file), default_timeout=120).run()
    assert not at.exception
    assert any("does not beat the Pinnacle closing market" in w.value for w in at.warning)
    state = [r for r in at.radio if r.label == "Information state"][0]
    state.set_value("lineups").run()
    assert not at.exception
    [r for r in at.radio if r.label == "Information state"][0].set_value("inplay").run()
    assert not at.exception
