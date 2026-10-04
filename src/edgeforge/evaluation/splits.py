"""Validation splits per D-033, written as id sets (no data) to artifacts/splits/*.json.

Blocks:
  pillar_a       football-data, five leagues, by season (walk-forward by date)
  team_2015_16   football-data four leagues: history 2005/06-2014/15, decay tuning 2013/14-2014/15,
                 evaluation 2015/16 (all matches; matchweek 20-38 subset is the untouched test)
  player_2015_16 StatsBomb four leagues by match_week: 1-9 burn-in, 10-19 tune, 20-38 test
"""

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from edgeforge.config import PROJECT_ROOT, load_config, resolve_path

log = logging.getLogger(__name__)

SPLITS_DIR = PROJECT_ROOT / "artifacts" / "splits"
PILLAR_A_LEAGUES = ("E0", "D1", "SP1", "I1", "F1")
TEAM_LEAGUES = ("E0", "SP1", "I1", "F1")


class LeakageError(RuntimeError):
    """Fitting or tuning code touched a test-window match."""


def _ids(con: duckdb.DuckDBPyConnection, sql: str) -> list[Any]:
    return sorted(r[0] for r in con.execute(sql).fetchall())


def build_splits(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    scored = "fthg IS NOT NULL AND ftag IS NOT NULL"
    pl = ",".join(f"'{x}'" for x in PILLAR_A_LEAGUES)
    tl = ",".join(f"'{x}'" for x in TEAM_LEAGUES)

    def fd(where: str) -> list[Any]:
        return _ids(con, f"SELECT match_id FROM matches WHERE {scored} AND {where} ORDER BY 1")

    pillar_a = {
        "history_2005_2018": fd(f"league IN ({pl}) AND season_start_year BETWEEN 2005 AND 2018"),
        "burn_in_tune_2019_23": fd(f"league IN ({pl}) AND season_start_year BETWEEN 2019 AND 2023"),
        "test_primary_2024_25": fd(f"league IN ({pl}) AND season_start_year = 2024"),
        "test_secondary_2025_26": fd(f"league IN ({pl}) AND season_start_year = 2025"),
    }
    mw = {
        "mw1_19": "m.match_week BETWEEN 1 AND 19",
        "mw20_38": "m.match_week BETWEEN 20 AND 38",
    }

    def fd_by_mw(rule: str) -> list[Any]:
        return _ids(
            con,
            "SELECT l.fd_match_id FROM sb_match_link l JOIN sb_matches m USING (sb_match_id) "
            f"WHERE {rule}",
        )

    team = {
        "history_2005_2014": fd(f"league IN ({tl}) AND season_start_year BETWEEN 2005 AND 2014"),
        "decay_tuning_2013_14": fd(f"league IN ({tl}) AND season_start_year BETWEEN 2013 AND 2014"),
        "eval_2015_16_all": fd(f"league IN ({tl}) AND season_start_year = 2015"),
        "eval_2015_16_mw1_19": fd_by_mw(mw["mw1_19"]),
        "test_2015_16_mw20_38": fd_by_mw(mw["mw20_38"]),
    }

    def sb(rule: str) -> list[Any]:
        return _ids(con, f"SELECT sb_match_id FROM sb_matches m WHERE {rule} ORDER BY 1")

    player = {
        "burn_in_mw1_9": sb("m.match_week BETWEEN 1 AND 9"),
        "tune_mw10_19": sb("m.match_week BETWEEN 10 AND 19"),
        "test_mw20_38": sb("m.match_week BETWEEN 20 AND 38"),
    }
    out: dict[str, dict[str, Any]] = {
        "pillar_a": {
            "rule": "D-033: burn-in/tune 2019/20-2023/24, primary test 2024/25, secondary 2025/26; "
            "history = scores 2005/06-2018/19 (ragged-row seasons excluded)",
            "ids": pillar_a,
        },
        "team_2015_16": {
            "rule": "D-033: history 2005/06-2014/15, decay tuned on 2013/14-2014/15 only, "
            "walk-forward through 2015/16; matchweek 20-38 matches are the untouched test",
            "ids": team,
        },
        "player_2015_16": {
            "rule": "D-033: match_week 1-9 burn-in (never scored), 10-19 tune, 20-38 test",
            "ids": player,
        },
    }
    for block in out.values():
        block["counts"] = {k: len(v) for k, v in block["ids"].items()}
    return out


def write_splits(cfg: dict[str, Any] | None = None, out_dir: Path | None = None) -> Path:
    cfg = cfg or load_config("data")
    out_dir = out_dir or SPLITS_DIR
    con = duckdb.connect(str(resolve_path(cfg, "warehouse")), read_only=True)
    try:
        splits = build_splits(con)
    finally:
        con.close()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, block in splits.items():
        lines = [
            "{",
            f'"block": {json.dumps(name)},',
            f'"rule": {json.dumps(block["rule"])},',
            f'"counts": {json.dumps(block["counts"])},',
            '"ids": {',
        ]
        keys = list(block["ids"])
        for i, k in enumerate(keys):
            comma = "," if i < len(keys) - 1 else ""
            lines.append(
                f"{json.dumps(k)}: {json.dumps(block['ids'][k], separators=(',', ':'))}{comma}"
            )
        lines += ["}", "}"]
        body = "\n".join(lines) + "\n"
        (out_dir / f"{name}.json").write_text(body, encoding="utf-8", newline="\n")
        log.info("%s: %s", name, block["counts"])
    return out_dir


@dataclass
class SplitGuard:
    """Raises if tuning or fitting code reads a test-window match (D-033)."""

    test_fd_ids: frozenset[str]
    test_sb_ids: frozenset[int]
    reads: list[tuple[str, int]] = field(default_factory=list)

    def check_tuning(self, ids: Iterable[Any], context: str) -> None:
        """Hyper-parameter choices and any fit used to make them may read no test match."""
        ids = list(ids)
        self.reads.append((context, len(ids)))
        bad = [i for i in ids if i in self.test_fd_ids or i in self.test_sb_ids]
        if bad:
            raise LeakageError(f"{context}: read {len(bad)} test-window matches, e.g. {bad[:3]}")

    def check_fit_before(self, done_ts: Iterable[Any], cutoff: Any, context: str) -> None:
        """Walk-forward fits may read only matches completed by the cutoff."""
        late = [t for t in done_ts if t > cutoff]
        if late:
            raise LeakageError(f"{context}: {len(late)} matches complete after cutoff {cutoff}")


def load_split_ids(name: str, splits_dir: Path | None = None) -> dict[str, list[Any]]:
    path = (splits_dir or SPLITS_DIR) / f"{name}.json"
    ids: dict[str, list[Any]] = json.loads(path.read_text(encoding="utf-8"))["ids"]
    return ids


def load_guard(splits_dir: Path | None = None) -> SplitGuard:
    a = load_split_ids("pillar_a", splits_dir)
    t = load_split_ids("team_2015_16", splits_dir)
    p = load_split_ids("player_2015_16", splits_dir)
    fd = (
        set(a["test_primary_2024_25"])
        | set(a["test_secondary_2025_26"])
        | set(t["test_2015_16_mw20_38"])
    )
    return SplitGuard(frozenset(fd), frozenset(p["test_mw20_38"]))
