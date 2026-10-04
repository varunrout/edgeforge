"""Audit of cached StatsBomb Open Data payloads (Phase 0b).

Reads only cached files. Every fact in docs/DATA.md section 3 traces to the JSON written here.
"""

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from edgeforge.config import load_config, resolve_path
from edgeforge.data import statsbomb
from edgeforge.data.audit import _read
from edgeforge.data.clock import minutes_played as _mp
from edgeforge.data.clock import period_ends as _period_ends
from edgeforge.data.dates import parse_fd_date as _parse_date
from edgeforge.data.http import CachedFetcher
from edgeforge.provenance import dir_digest, provenance

log = logging.getLogger(__name__)

TREE_URL = "https://api.github.com/repos/statsbomb/open-data/git/trees/master?recursive=1"
RED_CARDS = {"Red Card", "Second Yellow"}


def _minutes_played(lineup_player: dict[str, Any], ends: dict[int, int]) -> float:
    """Uncapped minutes from positions (the audit shows where this is wrong)."""
    return _mp(lineup_player["positions"], ends)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _cached(raw: Path, prefix: str) -> dict[int, Path]:
    out: dict[int, Path] = {}
    for p in raw.glob(f"{prefix}_*.body"):
        out[int(p.name.split(".")[0].split("_")[1])] = p
    return out


def audit_match(
    events: list[dict[str, Any]], lineups: list[dict[str, Any]], match: dict[str, Any]
) -> dict[str, Any]:
    ends = _period_ends(events)
    home = match["home_team"]["home_team_name"]
    away = match["away_team"]["away_team_name"]
    shots = [e for e in events if e["type"]["name"] == "Shot"]
    goals_by_team: Counter[str] = Counter()
    for e in shots:
        if e["shot"]["outcome"]["name"] == "Goal":
            goals_by_team[e["team"]["name"]] += 1
    own_for = [e for e in events if e["type"]["name"] == "Own Goal For"]
    for e in own_for:
        goals_by_team[e["team"]["name"]] += 1

    cards_events = []
    for e in events:
        for k in ("foul_committed", "bad_behaviour"):
            if k in e and "card" in e[k]:
                cards_events.append(
                    {
                        "source_event": e["type"]["name"],
                        "card": e[k]["card"]["name"],
                        "team": e["team"]["name"],
                        "player": e["player"]["name"],
                        "period": e["period"],
                        "minute": e["minute"],
                        "second": e["second"],
                    }
                )
    subs = [
        {
            "team": e["team"]["name"],
            "off": e["player"]["name"],
            "on": e["substitution"]["replacement"]["name"],
            "period": e["period"],
            "minute": e["minute"],
            "second": e["second"],
            "reason": e["substitution"]["outcome"]["name"],
        }
        for e in events
        if e["type"]["name"] == "Substitution"
    ]
    # Does the lineup 'positions' record close at a dismissal?
    dismissals = []
    for team in lineups:
        for p in team["lineup"]:
            for c in p["cards"]:
                if c["card_type"] in RED_CARDS:
                    last = p["positions"][-1]
                    pe = [e for e in events if e.get("player", {}).get("id") == p["player_id"]]
                    last_ev = max(pe, key=lambda e: e["index"]) if pe else None
                    dismissals.append(
                        {
                            "team": team["team_name"],
                            "player": p["player_name"],
                            "card_type": c["card_type"],
                            "card_clock": c["time"],
                            "card_period": c["period"],
                            "position_last_to": last["to"],
                            "position_last_end_reason": last["end_reason"],
                            "positions_closed_at_card": last["to"] == c["time"],
                            "last_event_type": last_ev["type"]["name"] if last_ev else None,
                            "last_event_clock": (
                                f"{last_ev['minute']}:{last_ev['second']:02d}" if last_ev else None
                            ),
                            "last_event_period": last_ev["period"] if last_ev else None,
                            "minutes_from_positions": _minutes_played(p, ends),
                        }
                    )
    tactical = [
        {
            "team": e["team"]["name"],
            "type": e["type"]["name"],
            "period": e["period"],
            "clock": f"{e['minute']}:{e['second']:02d}",
            "n_players": len(e["tactics"]["lineup"]),
        }
        for e in events
        if e["type"]["name"] in ("Starting XI", "Tactical Shift")
    ]
    players = []
    for team in lineups:
        for p in team["lineup"]:
            pos = p["positions"]
            players.append(
                {
                    "team": team["team_name"],
                    "player": p["player_name"],
                    "starter": bool(pos) and pos[0]["start_reason"] == "Starting XI",
                    "appeared": bool(pos),
                    "n_spans": len(pos),
                    "minutes_from_positions": _minutes_played(p, ends),
                    "cards": p["cards"],
                }
            )
    ev_keys = sorted({k for e in events for k in e})
    shot_keys = sorted({k for e in shots for k in e["shot"]})
    return {
        "match_id": match["match_id"],
        "match_date": match["match_date"],
        "home": home,
        "away": away,
        "final_score": [match["home_score"], match["away_score"]],
        "goals_from_events": {
            home: goals_by_team.get(home, 0),
            away: goals_by_team.get(away, 0),
        },
        "n_events": len(events),
        "period_end_clock_seconds": ends,
        "max_minute_by_period": {
            str(p): max(e["minute"] for e in events if e["period"] == p)
            for p in sorted({e["period"] for e in events})
        },
        "event_type_counts": dict(Counter(e["type"]["name"] for e in events)),
        "event_keys": ev_keys,
        "shot_keys": shot_keys,
        "shot_outcomes": dict(Counter(e["shot"]["outcome"]["name"] for e in shots)),
        "shot_types": dict(Counter(e["shot"]["type"]["name"] for e in shots)),
        "shots_with_xg": sum(1 for e in shots if "statsbomb_xg" in e["shot"]),
        "n_shots": len(shots),
        "goal_shots": [
            {
                "team": e["team"]["name"],
                "player": e["player"]["name"],
                "period": e["period"],
                "minute": e["minute"],
                "second": e["second"],
                "xg": e["shot"].get("statsbomb_xg"),
                "type": e["shot"]["type"]["name"],
            }
            for e in shots
            if e["shot"]["outcome"]["name"] == "Goal"
        ],
        "own_goal_events": [
            {
                "type": e["type"]["name"],
                "team": e["team"]["name"],
                "player": e.get("player", {}).get("name"),
                "period": e["period"],
                "minute": e["minute"],
                "second": e["second"],
            }
            for e in events
            if e["type"]["name"] in ("Own Goal For", "Own Goal Against")
        ],
        "card_events": cards_events,
        "substitutions": subs,
        "player_off_on_events": [
            {
                "type": e["type"]["name"],
                "player": e["player"]["name"],
                "period": e["period"],
                "clock": f"{e['minute']}:{e['second']:02d}",
            }
            for e in events
            if e["type"]["name"] in ("Player Off", "Player On")
        ],
        "lineup_player_keys": sorted({k for t in lineups for p in t["lineup"] for k in p}),
        "lineup_position_keys": sorted(
            {k for t in lineups for p in t["lineup"] for x in p["positions"] for k in x}
        ),
        "lineup_card_keys": sorted(
            {k for t in lineups for p in t["lineup"] for c in p["cards"] for k in c}
        ),
        "lineup_sizes": {t["team_name"]: len(t["lineup"]) for t in lineups},
        "n_starters": sum(1 for p in players if p["starter"]),
        "n_unused_bench": sum(1 for p in players if not p["appeared"]),
        "dismissals": dismissals,
        "tactical_lineup_sizes": tactical,
    }


def fetch_size_estimate(
    fetcher: CachedFetcher, cfg: dict[str, Any], match_ids: set[int], raw: Path
) -> dict[str, Any]:
    """Exact in-scope byte totals from the GitHub tree listing (one API request)."""
    res = fetcher.get(TREE_URL, "github_tree")
    if res.status != 200:
        return {"error": f"HTTP {res.status} for tree listing"}
    tree = _load(res.path)
    sizes: dict[str, int] = {}
    for item in tree["tree"]:
        if item["type"] == "blob" and "size" in item:
            sizes[item["path"]] = item["size"]
    ev = [sizes.get(f"data/events/{i}.json") for i in match_ids]
    lu = [sizes.get(f"data/lineups/{i}.json") for i in match_ids]
    missing = sum(1 for x in ev + lu if x is None)
    ev_b = sum(x for x in ev if x is not None)
    lu_b = sum(x for x in lu if x is not None)
    # Throughput measured on this machine from cached request metadata.
    metas = [json.loads(p.read_text()) for p in raw.glob("*.meta.json")]
    timed = [m for m in metas if "elapsed_s" in m and m["bytes"] > 100_000]
    total_s = sum(m["elapsed_s"] for m in timed)
    total_b = sum(m["bytes"] for m in timed)
    rate = total_b / total_s if total_s else None
    n_req = 2 * len(match_ids)
    out: dict[str, Any] = {
        "tree_truncated": bool(tree.get("truncated")),
        "n_match_ids": len(match_ids),
        "files_missing_from_tree": missing,
        "events_bytes_total": ev_b,
        "lineups_bytes_total": lu_b,
        "mean_events_bytes": ev_b / max(1, len(ev) - sum(1 for x in ev if x is None)),
        "n_requests_events_and_lineups": n_req,
        "throttle_floor_seconds": n_req * float(cfg["http"]["min_interval_s"]),
        "measured_samples": len(timed),
        "measured_bytes_per_second": rate,
    }
    if rate:
        out["transfer_seconds_at_measured_rate"] = (ev_b + lu_b) / rate
    # Whole-repo context for the sparse-clone alternative.
    out["repo_total_blob_bytes"] = sum(sizes.values())
    return out


LEAGUE_CODE = {"Premier League": "E0", "La Liga": "SP1", "Serie A": "I1", "Ligue 1": "F1"}


def _fd_path(cfg: dict[str, Any], league: str, season: str) -> Path | None:
    raw = resolve_path(cfg, "raw_dir") / "footballdata"
    for meta in raw.glob(f"{league}_{season}.*.meta.json"):
        return Path(str(meta).replace(".meta.json", ".body"))
    return None


def team_name_overlap(
    cfg: dict[str, Any], comp_name: str, sb_matches: list[dict[str, Any]], season: str = "1516"
) -> dict[str, Any]:
    """Compare team names across sources and test a (date, home goals, away goals) join."""
    code = LEAGUE_CODE[comp_name]
    path = _fd_path(cfg, code, season)
    if path is None:
        return {"league": comp_name, "error": "football-data file not cached"}
    header, rows, _ = _read(path)
    idx = {c: i for i, c in enumerate(header)}
    fd = []
    for r in rows:
        dt = _parse_date(r[idx["Date"]])
        fd.append(
            (
                dt.isoformat() if dt else None,
                r[idx["HomeTeam"]],
                r[idx["AwayTeam"]],
                r[idx["FTHG"]],
                r[idx["FTAG"]],
            )
        )
    sb = [
        (
            m["match_date"],
            m["home_team"]["home_team_name"],
            m["away_team"]["away_team_name"],
            str(m["home_score"]),
            str(m["away_score"]),
        )
        for m in sb_matches
    ]
    fd_names = {x[1] for x in fd} | {x[2] for x in fd}
    sb_names = {x[1] for x in sb} | {x[2] for x in sb}
    by_key: dict[tuple[str | None, str, str], list[tuple[str, str]]] = {}
    for sd, h, a, hs, as_ in sb:
        by_key.setdefault((sd, hs, as_), []).append((h, a))
    unique = ambiguous = zero = 0
    mapping: dict[str, set[str]] = {}
    for fdate, h, a, hs, as_ in fd:
        cands = by_key.get((fdate, hs, as_), [])
        if len(cands) == 1:
            unique += 1
            mapping.setdefault(h, set()).add(cands[0][0])
            mapping.setdefault(a, set()).add(cands[0][1])
        elif len(cands) > 1:
            ambiguous += 1
        else:
            zero += 1
    return {
        "league": comp_name,
        "football_data_code": code,
        "n_football_data_matches": len(fd),
        "n_statsbomb_matches": len(sb),
        "n_football_data_teams": len(fd_names),
        "n_statsbomb_teams": len(sb_names),
        "n_names_identical": len(fd_names & sb_names),
        "football_data_only_names": sorted(fd_names - sb_names),
        "statsbomb_only_names": sorted(sb_names - fd_names),
        "join_date_score_unique": unique,
        "join_date_score_ambiguous": ambiguous,
        "join_date_score_no_candidate": zero,
        "names_with_one_mapping": sum(1 for v in mapping.values() if len(v) == 1),
        "names_with_conflicting_mapping": sorted(k for k, v in mapping.items() if len(v) > 1),
        "football_data_names_unmapped": sorted(fd_names - set(mapping)),
    }


def run_statsbomb_audit(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    raw = resolve_path(cfg, "raw_dir") / "statsbomb"
    fetcher = statsbomb.make_fetcher(cfg)
    comps = statsbomb.fetch_competitions(fetcher, cfg)
    scope = []
    all_ids: set[int] = set()
    match_index: dict[int, dict[str, Any]] = {}
    for c in statsbomb.scope_competitions(comps, cfg):
        ms = statsbomb.fetch_matches(fetcher, cfg, c["competition_id"], c["season_id"])
        all_ids |= {m["match_id"] for m in ms}
        match_index.update({m["match_id"]: m for m in ms})
        scope.append(
            {
                "competition": c["competition_name"],
                "competition_id": c["competition_id"],
                "season": c["season_name"],
                "season_id": c["season_id"],
                "n_matches": len(ms),
                "match_status": dict(Counter(m["match_status"] for m in ms)),
                "first_date": min(m["match_date"] for m in ms),
                "last_date": max(m["match_date"] for m in ms),
                "n_with_match_week": sum(1 for m in ms if m.get("match_week") is not None),
                "n_with_kick_off": sum(1 for m in ms if m.get("kick_off")),
                "n_home_with_managers": sum(1 for m in ms if m["home_team"].get("managers")),
            }
        )
    names = [
        team_name_overlap(
            cfg,
            c["competition_name"],
            statsbomb.fetch_matches(fetcher, cfg, c["competition_id"], c["season_id"]),
        )
        for c in statsbomb.scope_competitions(comps, cfg)
    ]
    excluded = []
    for c in comps:
        if c["season_name"] == "2015/2016" and c["competition_name"] == "1. Bundesliga":
            n = len(statsbomb.fetch_matches(fetcher, cfg, c["competition_id"], c["season_id"]))
            excluded.append(
                {"competition": c["competition_name"], "season": c["season_name"], "n_matches": n}
            )
    ev_files = _cached(raw, "events")
    lu_files = _cached(raw, "lineups")
    samples = []
    wanted = set(cfg["statsbomb"]["audit_sample_match_ids"])
    for mid in sorted(set(ev_files) & set(lu_files) & wanted):
        samples.append(audit_match(_load(ev_files[mid]), _load(lu_files[mid]), match_index[mid]))
    bodies = sorted(raw.glob("*.body"))
    out = {
        "provenance": provenance("edgeforge data audit-statsbomb", cfg, dir_digest(bodies)),
        "scope": scope,
        "total_scope_matches": len(all_ids),
        "excluded_checked": excluded,
        "team_names_2015_16": names,
        "n_competition_seasons_in_catalogue": len(comps),
        "sample_matches": samples,
        "download_estimate": fetch_size_estimate(fetcher, cfg, all_ids, raw),
    }
    out_path = resolve_path(cfg, "metrics_dir") / "data_audit_statsbomb.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("wrote %s (%d sample matches)", out_path, len(samples))
    return out_path
