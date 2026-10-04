"""Tables from StatsBomb events and lineups: sb_matches, player_match, shots, events_timeline.

Conventions: D-030 (shots on target, goals, minutes capped at dismissal). All times are elapsed
match seconds on the period-aware clock (`edgeforge.data.clock`), alongside the raw
(period, minute, second).
"""

import logging
from collections import Counter
from collections.abc import Iterable
from typing import Any

import pandas as pd

from edgeforge.data.classify import is_on_target
from edgeforge.data.clock import (
    DISMISSAL_CARDS,
    dismissal_cap_s,
    elapsed,
    event_presence,
    match_length_s,
    minutes_played,
    period_ends,
    span_bounds,
)

log = logging.getLogger(__name__)

CARD_KEYS = ("foul_committed", "bad_behaviour")


class UnknownPositionError(ValueError):
    """A StatsBomb position name with no position group."""


def position_group(name: str) -> str:
    """Coarse group for shrinkage: GK / DEF / MID / FWD. Unknown names raise."""
    if name == "Goalkeeper":
        return "GK"
    if "Back" in name:  # Right Back, Center Back, Right Wing Back, ...
        return "DEF"
    if "Midfield" in name:
        return "MID"
    if "Forward" in name or "Striker" in name or name in ("Right Wing", "Left Wing"):
        return "FWD"
    raise UnknownPositionError(name)


def _ev_elapsed(e: dict[str, Any], ends: dict[int, int]) -> int:
    return elapsed(e["minute"] * 60 + e["second"], e["period"], ends)


def process_match(
    m: dict[str, Any], events: list[dict[str, Any]], lineups: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    mid = m["match_id"]
    home = m["home_team"]["home_team_name"]
    away = m["away_team"]["away_team_name"]
    ends = period_ends(events)
    length = match_length_s(ends)
    home_id, away_id = m["home_team"]["home_team_id"], m["away_team"]["away_team_id"]
    names = {home_id: home, away_id: away}  # canonical names: the match record
    other_id = {home_id: away_id, away_id: home_id}
    event_names = {e["team"]["id"]: e["team"]["name"] for e in events if "team" in e}
    name_mismatch = sum(1 for tid, n in event_names.items() if names.get(tid) != n)

    # --- shots
    shots: list[dict[str, Any]] = []
    for e in events:
        if e["type"]["name"] != "Shot":
            continue
        s = e["shot"]
        outcome = s["outcome"]["name"]
        loc = e.get("location") or [None, None]
        shots.append(
            {
                "sb_match_id": mid,
                "event_id": e["id"],
                "event_index": e["index"],
                "team": names[e["team"]["id"]],
                "team_id": e["team"]["id"],
                "player_id": e["player"]["id"],
                "player": e["player"]["name"],
                "period": e["period"],
                "minute": e["minute"],
                "second": e["second"],
                "elapsed_s": _ev_elapsed(e, ends),
                "outcome": outcome,
                "shot_type": s["type"]["name"],
                "body_part": s.get("body_part", {}).get("name"),
                "technique": s.get("technique", {}).get("name"),
                "xg": s.get("statsbomb_xg"),
                "on_target": is_on_target(outcome),
                "is_goal": outcome == "Goal",
                "is_penalty": s["type"]["name"] == "Penalty",
                "first_time": bool(s.get("first_time", False)),
                "x": loc[0],
                "y": loc[1],
            }
        )

    # --- timeline: goals, own goals, dismissals
    timeline: list[dict[str, Any]] = []
    for s in shots:
        if s["is_goal"]:
            timeline.append(
                {
                    "sb_match_id": mid,
                    "kind": "goal",
                    "team": s["team"],
                    "team_id": s["team_id"],
                    "player_id": s["player_id"],
                    "player": s["player"],
                    "period": s["period"],
                    "minute": s["minute"],
                    "second": s["second"],
                    "elapsed_s": s["elapsed_s"],
                    "event_index": s["event_index"],
                    "xg": s["xg"],
                    "is_penalty": s["is_penalty"],
                }
            )
    n_og_for = 0
    n_og_against = 0
    for e in events:
        tname = e["type"]["name"]
        if tname == "Own Goal For":
            n_og_for += 1
        elif tname == "Own Goal Against":
            n_og_against += 1
            timeline.append(
                {
                    "sb_match_id": mid,
                    "kind": "own_goal",
                    "team": names[other_id[e["team"]["id"]]],  # credited (scoring) team
                    "team_id": other_id[e["team"]["id"]],
                    "player_id": e["player"]["id"],
                    "player": e["player"]["name"],
                    "period": e["period"],
                    "minute": e["minute"],
                    "second": e["second"],
                    "elapsed_s": _ev_elapsed(e, ends),
                    "event_index": e["index"],
                    "xg": None,
                    "is_penalty": False,
                }
            )
        for k in CARD_KEYS:
            if k in e and "card" in e[k] and e[k]["card"]["name"] in DISMISSAL_CARDS:
                timeline.append(
                    {
                        "sb_match_id": mid,
                        "kind": "red_card"
                        if e[k]["card"]["name"] == "Red Card"
                        else "second_yellow",
                        "team": names[e["team"]["id"]],
                        "team_id": e["team"]["id"],
                        "player_id": e["player"]["id"],
                        "player": e["player"]["name"],
                        "period": e["period"],
                        "minute": e["minute"],
                        "second": e["second"],
                        "elapsed_s": _ev_elapsed(e, ends),
                        "event_index": e["index"],
                        "xg": None,
                        "is_penalty": False,
                    }
                )
    timeline.sort(key=lambda r: (r["elapsed_s"], r["event_index"]))
    score = {home: 0, away: 0}
    for r in timeline:
        if r["kind"] in ("goal", "own_goal"):
            score[r["team"]] += 1
        r["home_goals_after"] = score[home]
        r["away_goals_after"] = score[away]

    # --- event-card counts for reconciliation with lineup cards
    ev_cards: Counter[str] = Counter()
    for e in events:
        for k in CARD_KEYS:
            if k in e and "card" in e[k]:
                ev_cards[e[k]["card"]["name"]] += 1

    # --- players
    by_player: dict[int, list[dict[str, Any]]] = {}
    for s in shots:
        by_player.setdefault(s["player_id"], []).append(s)
    og_by_player: Counter[int] = Counter(
        r["player_id"] for r in timeline if r["kind"] == "own_goal"
    )
    presence = event_presence(events, ends)
    starting_ids = {
        x["player"]["id"]
        for e in events
        if e["type"]["name"] == "Starting XI"
        for x in e["tactics"]["lineup"]
    }
    players: list[dict[str, Any]] = []
    lineup_ids: set[int] = set()
    lu_cards: Counter[str] = Counter()
    for team in lineups:
        tname = names[team["team_id"]]
        for p in team["lineup"]:
            pid = p["player_id"]
            lineup_ids.add(pid)
            pos = p["positions"]
            started_lineup = bool(pos) and pos[0]["start_reason"] == "Starting XI"
            started = pid in starting_ids
            cap = dismissal_cap_s(p["cards"], ends)
            pres = presence.get(pid)
            mins = pres.seconds / 60 if pres else 0.0
            mins_pos = minutes_played(pos, ends, cap)
            main_pos = None
            first_pos = pos[0]["position"] if pos else None
            if pos:
                spans = [(span_bounds(x, ends), x) for x in pos]
                main_pos = max(spans, key=lambda t: t[0][1] - t[0][0])[1]["position"]
            ps = by_player.get(pid, [])
            for c in p["cards"]:
                lu_cards[c["card_type"]] += 1
            players.append(
                {
                    "sb_match_id": mid,
                    "team": tname,
                    "team_id": team["team_id"],
                    "is_home": tname == home,
                    "player_id": pid,
                    "player_name": p["player_name"],
                    "position": main_pos,
                    "position_first": first_pos,
                    "position_group": position_group(main_pos) if main_pos else None,
                    "started": started,
                    "named_bench": not started,
                    "appeared": bool(pos),
                    "minutes": mins,
                    "minutes_positions": mins_pos,
                    "dismissal_cap_s": cap,
                    "gap_minutes": (pres.gap_seconds / 60) if pres else 0.0,
                    "n_spans": len(pos),
                    "started_lineup": started_lineup,
                    "vacancy_s": pres.vacancy_s if pres else 0,
                    "exit_kind": pres.exit_kind if pres else None,
                    "exit_s": pres.exit_s if pres else None,
                    "sub_on_s": pres.sub_on_s if pres else None,
                    "sub_off_s": pres.sub_off_s if pres else None,
                    "shots": len(ps),
                    "shots_on_target": sum(1 for s in ps if s["on_target"]),
                    "goals": sum(1 for s in ps if s["is_goal"]),
                    "penalties_taken": sum(1 for s in ps if s["is_penalty"]),
                    "penalty_goals": sum(1 for s in ps if s["is_penalty"] and s["is_goal"]),
                    "xg": float(sum(s["xg"] or 0.0 for s in ps)),
                    "own_goals": og_by_player.get(pid, 0),
                    "yellow": sum(1 for c in p["cards"] if c["card_type"] == "Yellow Card"),
                    "second_yellow": sum(
                        1 for c in p["cards"] if c["card_type"] == "Second Yellow"
                    ),
                    "red": sum(1 for c in p["cards"] if c["card_type"] == "Red Card"),
                }
            )

    checks = {
        "sb_match_id": mid,
        "event_team_names_differ_from_match_record": name_mismatch,
        "max_period": max(ends),
        "half_end_events_p1": sum(
            1 for e in events if e["type"]["name"] == "Half End" and e["period"] == 1
        ),
        "half_end_events_p2": sum(
            1 for e in events if e["type"]["name"] == "Half End" and e["period"] == 2
        ),
        "shots_by_non_lineup_players": sum(1 for s in shots if s["player_id"] not in lineup_ids),
        "own_goal_for_events": n_og_for,
        "own_goal_against_events": n_og_against,
        "event_cards_yellow": ev_cards.get("Yellow Card", 0),
        "event_cards_second_yellow": ev_cards.get("Second Yellow", 0),
        "event_cards_red": ev_cards.get("Red Card", 0),
        "lineup_cards_yellow": lu_cards.get("Yellow Card", 0),
        "lineup_cards_second_yellow": lu_cards.get("Second Yellow", 0),
        "lineup_cards_red": lu_cards.get("Red Card", 0),
    }
    mg = m["home_team"].get("managers") or []
    ag = m["away_team"].get("managers") or []
    match_row = {
        "sb_match_id": mid,
        "competition": m["_competition"],
        "season": m["season"]["season_name"],
        "match_date": m["match_date"],
        "kick_off": m.get("kick_off"),
        "match_week": m.get("match_week"),
        "home": home,
        "away": away,
        "home_team_id": m["home_team"]["home_team_id"],
        "away_team_id": m["away_team"]["away_team_id"],
        "home_score": m["home_score"],
        "away_score": m["away_score"],
        "home_manager_ids": ",".join(str(x["id"]) for x in mg),
        "away_manager_ids": ",".join(str(x["id"]) for x in ag),
        "referee": (m.get("referee") or {}).get("name"),
        "p1_end_s": ends[1],
        "p2_end_clock_s": ends[2],
        "match_length_s": length,
        "n_events": len(events),
    }
    return {
        "sb_matches": [match_row],
        "player_match": players,
        "shots": shots,
        "events_timeline": timeline,
        "sb_match_checks": [checks],
    }


def build_sb_tables(
    payloads: Iterable[tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]],
) -> dict[str, pd.DataFrame]:
    acc: dict[str, list[dict[str, Any]]] = {
        k: [] for k in ("sb_matches", "player_match", "shots", "events_timeline", "sb_match_checks")
    }
    n = 0
    for m, events, lineups in payloads:
        out = process_match(m, events, lineups)
        for k, rows in out.items():
            acc[k].extend(rows)
        n += 1
        if n % 200 == 0:
            log.info("processed %d matches", n)
    tables = {k: pd.DataFrame(v) for k, v in acc.items()}
    tables["sb_matches"]["match_date"] = pd.to_datetime(tables["sb_matches"]["match_date"])
    return tables
