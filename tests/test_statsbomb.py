from typing import Any

from edgeforge.data.clock import clock_secs, event_presence, minutes_played
from edgeforge.data.clock import elapsed as _el
from edgeforge.data.statsbomb import scope_competitions
from edgeforge.data.statsbomb_audit import audit_match

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
    assert _el(clock_secs("47:00"), 1, ENDS) == 2820
    # 45:00 on the period-2 clock is the start of the second half = end of first half in elapsed
    assert _el(clock_secs("45:00"), 2, ENDS) == 2820
    assert _el(clock_secs("75:00"), 2, ENDS) == 2820 + 1800


def _span(frm: str, p1: int, to: str | None, p2: int | None) -> dict[str, Any]:
    return {"from": frm, "from_period": p1, "to": to, "to_period": p2}


def test_minutes_played_full_match_and_cross_period_span() -> None:
    total = (2820 + 3000) / 60
    full = {"positions": [_span("00:00", 1, None, None)]}
    assert minutes_played(full["positions"], ENDS) == total
    # span from 47:40-style stoppage into the second half must not subtract period clocks naively
    cross = {"positions": [_span("00:00", 1, "46:00", 1), _span("46:00", 1, "60:00", 2)]}
    assert minutes_played(cross["positions"], ENDS) == (2820 + 900) / 60
    bench = {"positions": []}
    assert minutes_played(bench["positions"], ENDS) == 0.0


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


def test_reversed_spans_telescope_and_cap_applies() -> None:
    # Real StatsBomb pattern: spans ordered by mm:ss ignoring the period.
    pos = [
        _span("00:00", 1, "45:12", 2),
        _span("45:12", 2, "47:40", 1),
        _span("47:40", 1, "75:12", 2),
        _span("75:12", 2, None, None),
    ]
    total = (2820 + 3000) / 60
    assert minutes_played(pos, ENDS) == total
    # dismissed at 60:00 of period 2 -> elapsed 2820 + 900
    assert minutes_played(pos, ENDS, cap_s=2820 + 900) == (2820 + 900) / 60


def _tev(idx: int, name: str, period: int, minute: int, second: int, **kw: Any) -> dict[str, Any]:
    e: dict[str, Any] = {
        "index": idx,
        "type": {"name": name},
        "period": period,
        "minute": minute,
        "second": second,
    }
    e.update(kw)
    return e


def test_event_presence_substitution_temp_absence_and_dismissal() -> None:
    # period 1 ends 46:00 (2760 s), period 2 ends 93:00 -> length 2760 + 2880 = 5640 s
    ends = {1: 2760, 2: 5580}
    events = [
        _tev(
            1,
            "Starting XI",
            1,
            0,
            0,
            tactics={
                "lineup": [{"player": {"id": 1}}, {"player": {"id": 2}}, {"player": {"id": 3}}]
            },
        ),
        _tev(2, "Player Off", 1, 10, 0, player={"id": 1}),
        _tev(3, "Player On", 1, 10, 30, player={"id": 1}),
        # player 2 off at 44:00, back at 45:30 (period-1 stoppage), then half-time substitution
        _tev(4, "Player Off", 1, 44, 0, player={"id": 2}),
        _tev(4.5, "Player On", 1, 45, 30, player={"id": 2}),
        _tev(
            5, "Substitution", 2, 45, 0, player={"id": 2}, substitution={"replacement": {"id": 9}}
        ),
        _tev(
            6,
            "Foul Committed",
            2,
            60,
            0,
            player={"id": 3},
            foul_committed={"card": {"name": "Second Yellow"}},
        ),
    ]
    pres = event_presence(events, ends)
    assert pres[1].seconds == 5640 - 30 and pres[1].gap_seconds == 30
    # player 2: 0-2640 on, 2640-2730 off, 2730-2760 on, then substituted: never restarted
    assert pres[2].seconds == 2640 + 30 and pres[2].gap_seconds == 90
    assert pres[2].sub_off_s == 2760
    assert pres[9].sub_on_s == 2760 and pres[9].seconds == 5640 - 2760
    assert pres[3].seconds == 2760 + 900  # dismissed at 60:00 of period 2
    assert pres[3].vacancy_s == 5640 - 3660 and pres[3].exit_kind == "dismissal"
    assert pres[1].vacancy_s == 30 and pres[2].vacancy_s == 90 and pres[9].vacancy_s == 0
    assert pres[2].exit_kind == "substitution"


def test_unreplaced_exit_and_late_replacement_vacancy() -> None:
    ends = {1: 2760, 2: 5580}
    events = [
        _tev(
            1,
            "Starting XI",
            1,
            0,
            0,
            tactics={"lineup": [{"player": {"id": 1}}, {"player": {"id": 2}}]},
        ),
        _tev(2, "Player Off", 2, 60, 0, player={"id": 1}),  # injured, never replaced
        _tev(3, "Player Off", 2, 70, 0, player={"id": 2}),  # injured, replaced 2 minutes later
        _tev(
            4, "Substitution", 2, 72, 0, player={"id": 2}, substitution={"replacement": {"id": 7}}
        ),
    ]
    pres = event_presence(events, ends)
    assert pres[1].exit_kind == "player_off" and pres[1].vacancy_s == 5640 - 3660
    assert pres[2].exit_kind == "substitution" and pres[2].vacancy_s == 120
    assert pres[7].seconds == 5640 - (2760 + 72 * 60 - 2700)
