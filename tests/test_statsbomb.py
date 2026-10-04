from typing import Any

from edgeforge.data.statsbomb import scope_competitions
from edgeforge.data.statsbomb_audit import _elapsed, _minutes_played, audit_match

CFG = {"statsbomb": {"scope_season_name": "2015/2016", "scope_competitions": ["Premier League"]}}


def test_scope_filter() -> None:
    comps = [
        {"competition_name": "Premier League", "season_name": "2015/2016"},
        {"competition_name": "Premier League", "season_name": "2016/2017"},
        {"competition_name": "1. Bundesliga", "season_name": "2015/2016"},
    ]
    assert scope_competitions(comps, CFG) == [comps[0]]


ENDS = {1: 2820, 2: 5700}  # P1 stoppage to 47:00, P2 ends 95:00 on the period clock


def test_elapsed_is_period_aware() -> None:
    assert _elapsed("47:00", 1, ENDS) == 2820
    # 45:00 on the period-2 clock is the start of the second half = end of first half in elapsed
    assert _elapsed("45:00", 2, ENDS) == 2820
    assert _elapsed("75:00", 2, ENDS) == 2820 + 1800


def _span(frm: str, p1: int, to: str | None, p2: int | None) -> dict[str, Any]:
    return {"from": frm, "from_period": p1, "to": to, "to_period": p2}


def test_minutes_played_full_match_and_cross_period_span() -> None:
    total = (2820 + 3000) / 60
    full = {"positions": [_span("00:00", 1, None, None)]}
    assert _minutes_played(full, ENDS) == total
    # span from 47:40-style stoppage into the second half must not subtract period clocks naively
    cross = {"positions": [_span("00:00", 1, "46:00", 1), _span("46:00", 1, "60:00", 2)]}
    assert _minutes_played(cross, ENDS) == (2820 + 900) / 60
    bench = {"positions": []}
    assert _minutes_played(bench, ENDS) == 0.0


def _ev(
    idx: int, typ: str, team: str, period: int, minute: int, second: int, **kw: Any
) -> dict[str, Any]:
    e: dict[str, Any] = {
        "index": idx,
        "type": {"name": typ},
        "team": {"name": team},
        "period": period,
        "minute": minute,
        "second": second,
    }
    e.update(kw)
    return e


def test_audit_match_reconciles_goals_and_flags_open_position_after_red() -> None:
    events = [
        _ev(1, "Half End", "A", 1, 46, 0),
        _ev(2, "Half End", "A", 2, 93, 0),
        _ev(
            3,
            "Shot",
            "A",
            1,
            10,
            5,
            player={"id": 1, "name": "p1"},
            shot={"outcome": {"name": "Goal"}, "type": {"name": "Open Play"}, "statsbomb_xg": 0.3},
        ),
        _ev(4, "Own Goal For", "B", 2, 80, 0),
        _ev(
            5,
            "Bad Behaviour",
            "A",
            1,
            30,
            0,
            player={"id": 2, "name": "p2"},
            bad_behaviour={"card": {"name": "Red Card"}},
        ),
    ]
    lineups = [
        {
            "team_id": 1,
            "team_name": "A",
            "lineup": [
                {
                    "player_id": 2,
                    "player_name": "p2",
                    "cards": [
                        {"time": "30:00", "card_type": "Red Card", "reason": "x", "period": 1}
                    ],
                    "positions": [
                        {
                            "from": "00:00",
                            "to": None,
                            "from_period": 1,
                            "to_period": None,
                            "start_reason": "Starting XI",
                            "end_reason": "Final Whistle",
                            "position": "CB",
                            "position_id": 3,
                        }
                    ],
                },
            ],
        },
        {"team_id": 2, "team_name": "B", "lineup": []},
    ]
    match = {
        "match_id": 9,
        "match_date": "2015-09-01",
        "home_score": 1,
        "away_score": 1,
        "home_team": {"home_team_name": "A"},
        "away_team": {"away_team_name": "B"},
    }
    out = audit_match(events, lineups, match)
    assert out["goals_from_events"] == {"A": 1, "B": 1}
    d = out["dismissals"][0]
    assert d["card_clock"] == "30:00" and d["positions_closed_at_card"] is False
