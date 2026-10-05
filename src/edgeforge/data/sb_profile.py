"""Full-corpus profile of StatsBomb payloads (Phase 1): every distinct categorical value."""

import json
import logging
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from edgeforge.config import load_config, resolve_path
from edgeforge.data import statsbomb
from edgeforge.data.classify import UnclassifiedOutcomeError, is_on_target
from edgeforge.data.clock import DISMISSAL_CARDS, clock_secs
from edgeforge.provenance import dir_digest, provenance

log = logging.getLogger(__name__)

Payload = tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]


def _counter_dict(c: Counter[str]) -> dict[str, int]:
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def _closure_state(last: dict[str, Any] | None, card: dict[str, Any]) -> str:
    if last is None:
        return "no_positions"
    if last["to"] is None:
        return "open_until_final_whistle"
    if last["to_period"] == card["period"] and clock_secs(last["to"]) == clock_secs(card["time"]):
        return "closed_at_card"
    return "closed_elsewhere"


def profile_payloads(payloads: Iterable[Payload]) -> dict[str, Any]:
    names = (
        "shot_outcome",
        "shot_type",
        "event_card_name",
        "event_card_source_event",
        "lineup_card_type",
        "lineup_card_reason",
        "start_reason",
        "end_reason",
        "substitution_outcome",
        "event_period",
        "position_from_period",
        "position_to_period",
        "position_name",
        "event_type",
    )
    c: dict[str, Counter[str]] = {k: Counter() for k in names}
    n_matches = 0
    missing_half_end: list[dict[str, Any]] = []
    dismissals_events: Counter[str] = Counter()
    dismissals_lineups: Counter[str] = Counter()
    closure: Counter[str] = Counter()
    closure_examples: list[dict[str, Any]] = []
    events_after_card: Counter[str] = Counter()
    for m, events, lineups in payloads:
        n_matches += 1
        halves: Counter[int] = Counter()
        last_index_by_player: dict[int, int] = {}
        for e in events:
            pid = e.get("player", {}).get("id")
            if pid is not None:
                last_index_by_player[pid] = max(last_index_by_player.get(pid, 0), e["index"])
        for e in events:
            tname = e["type"]["name"]
            c["event_type"][tname] += 1
            c["event_period"][str(e["period"])] += 1
            if tname == "Half End":
                halves[e["period"]] += 1
            if tname == "Shot":
                c["shot_outcome"][e["shot"]["outcome"]["name"]] += 1
                c["shot_type"][e["shot"]["type"]["name"]] += 1
            if tname == "Substitution":
                c["substitution_outcome"][e["substitution"]["outcome"]["name"]] += 1
            for k in ("foul_committed", "bad_behaviour"):
                if k in e and "card" in e[k]:
                    card_name = e[k]["card"]["name"]
                    c["event_card_name"][card_name] += 1
                    c["event_card_source_event"][f"{tname}:{card_name}"] += 1
                    if card_name in DISMISSAL_CARDS:
                        dismissals_events[card_name] += 1
                        later = last_index_by_player[e["player"]["id"]] > e["index"]
                        tag = "has_later_events" if later else "none"
                        events_after_card[f"{card_name}:{tag}"] += 1
        if not (halves.get(1) and halves.get(2)):
            missing_half_end.append({"match_id": m["match_id"], "half_end_counts": dict(halves)})
        for team in lineups:
            for p in team["lineup"]:
                for pos in p["positions"]:
                    c["start_reason"][pos["start_reason"]] += 1
                    c["end_reason"][pos["end_reason"]] += 1
                    c["position_from_period"][str(pos["from_period"])] += 1
                    c["position_to_period"][str(pos["to_period"])] += 1
                    c["position_name"][pos["position"]] += 1
                for card in p["cards"]:
                    c["lineup_card_type"][card["card_type"]] += 1
                    c["lineup_card_reason"][card["reason"]] += 1
                    if card["card_type"] in DISMISSAL_CARDS:
                        dismissals_lineups[card["card_type"]] += 1
                        last = p["positions"][-1] if p["positions"] else None
                        state = _closure_state(last, card)
                        closure[f"{card['card_type']}:{state}"] += 1
                        if state != "closed_at_card" and len(closure_examples) < 25:
                            closure_examples.append(
                                {
                                    "match_id": m["match_id"],
                                    "player_id": p["player_id"],
                                    "card_type": card["card_type"],
                                    "card_time": card["time"],
                                    "card_period": card["period"],
                                    "last_position_to": last["to"] if last else None,
                                    "last_end_reason": last["end_reason"] if last else None,
                                    "state": state,
                                }
                            )
    unclassified = []
    for outcome in c["shot_outcome"]:
        try:
            is_on_target(outcome)
        except UnclassifiedOutcomeError:
            unclassified.append(outcome)
    return {
        "n_matches": n_matches,
        "distinct_values": {k: _counter_dict(v) for k, v in c.items()},
        "shot_outcomes_unclassified_by_d030": unclassified,
        "matches_missing_half_end": missing_half_end,
        "dismissals": {
            "from_event_cards": dict(dismissals_events),
            "from_lineup_cards": dict(dismissals_lineups),
            "positions_vs_card": _counter_dict(closure),
            "events_by_dismissed_player_after_card": _counter_dict(events_after_card),
            "non_closing_examples_first_25": closure_examples,
        },
    }


def run_profile(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    out = profile_payloads(statsbomb.iter_payloads(cfg))
    raw = resolve_path(cfg, "raw_dir") / "statsbomb"
    out = {
        "provenance": provenance(
            "edgeforge data profile-statsbomb", cfg, dir_digest(sorted(raw.glob("*.meta.json")))
        ),
        **out,
    }
    path = resolve_path(cfg, "metrics_dir") / "statsbomb_profile.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("wrote %s", path)
    if out["shot_outcomes_unclassified_by_d030"]:
        raise SystemExit(
            f"STOP: shot outcomes not classified in D-030: "
            f"{out['shot_outcomes_unclassified_by_d030']}"
        )
    return path
