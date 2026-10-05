"""D-028 / D-041: no committed metrics file may contain player names (StatsBomb ids only)."""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# keys whose string values would be a person's name; ids are ints
NAME_KEYS = {"player", "player_name", "player_nickname", "scorer", "off", "on", "nickname"}


def name_fields(obj: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in NAME_KEYS and isinstance(v, str):
                hits.append(f"{path}/{k}")
            if k == "players" and isinstance(v, list) and v and isinstance(v[0], dict):
                hits.append(f"{path}/{k}")  # per-player rows
            hits += name_fields(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += name_fields(v, f"{path}[{i}]")
    return hits


def test_detector_flags_name_fields_and_allows_ids() -> None:
    assert name_fields({"a": [{"player_name": "Someone"}]})
    assert name_fields({"substitutions": [{"off": "A", "on": "B"}]})
    assert name_fields({"players": [{"minutes": 90}]})
    assert not name_fields({"a": [{"player_id": 12, "off_player_id": 3}], "players": 10})
    assert not name_fields({"keys": {"player_match": "sb_match_id, player_id"}})


def test_committed_metrics_and_registry_contain_no_player_names() -> None:
    files = sorted((ROOT / "artifacts").rglob("*.json"))
    bad = {}
    for f in files:
        hits = name_fields(json.loads(f.read_text(encoding="utf-8")))
        if hits:
            bad[f.name] = hits[:5]
    reg = ROOT / "experiments" / "registry.jsonl"
    for i, line in enumerate(reg.read_text(encoding="utf-8").splitlines()):
        if line.strip():
            hits = name_fields(json.loads(line))
            if hits:
                bad[f"registry line {i + 1}"] = hits[:5]
    assert not bad, bad
