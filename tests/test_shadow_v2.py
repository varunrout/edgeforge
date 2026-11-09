"""D-065/D-066: dc_opening_v2 in the shadow log: v1 untouched, no look-ahead, versions separate."""

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from edgeforge.shadow import (
    MODEL_VERSION,
    MODEL_VERSION_V2,
    append_jsonl,
    build_report,
    history_from_results,
    predict_records,
    predict_records_v2,
    read_log,
    settle_records,
)

ROOT = Path(__file__).resolve().parents[1]
CFG = {"seed": 1, "paths": {}}
TEAMS = [f"Team{i:02d}" for i in range(12)]
FIRST_BATCH_SHA256 = "77222d81dfa40c62d09564fa1b9c53a9b36cbf68633432251d9855e23bd1e2ff"


def _results(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    strength = rng.normal(0, 0.25, len(TEAMS))
    rows = []
    start = pd.Timestamp("2023-08-12")
    for s in range(3):
        for rnd in range(22):
            for a in range(0, len(TEAMS), 2):
                hi, wi = (a + rnd) % 12, (a + rnd + 1 + rnd // 3) % 12
                if hi == wi:
                    continue
                lam_h = np.exp(0.3 + strength[hi] - strength[wi])
                lam_a = np.exp(strength[wi] - strength[hi])
                d = start + pd.Timedelta(days=365 * s + 7 * rnd)
                rows.append(
                    {
                        "Div": "E0", "Date": d.strftime("%d/%m/%Y"), "Time": "15:00",
                        "HomeTeam": TEAMS[hi], "AwayTeam": TEAMS[wi],
                        "FTHG": rng.poisson(lam_h), "FTAG": rng.poisson(lam_a),
                        "HST": rng.poisson(3 * lam_h), "AST": rng.poisson(3 * lam_a),
                    }
                )  # fmt: skip
    return pd.DataFrame(rows)


def _fixtures() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Div": ["E0", "E0"], "Date": ["20/12/2026"] * 2, "Time": ["15:00", "17:30"],
            "HomeTeam": ["Team00", "Team02"], "AwayTeam": ["Team01", "Team03"],
        }
    )  # fmt: skip


AS_OF = pd.Timestamp("2026-12-18 09:00")


def test_the_first_batch_of_the_committed_log_is_byte_identical() -> None:
    raw = (ROOT / "artifacts" / "shadow" / "log.jsonl").read_bytes().replace(b"\r\n", b"\n")
    first = b"\n".join(raw.split(b"\n")[:10]) + b"\n"
    assert hashlib.sha256(first).hexdigest() == FIRST_BATCH_SHA256


def test_history_carries_shots_on_target() -> None:
    h = history_from_results(_results(), "E0")
    assert h["hst"].notna().all() and h["ast"].notna().all()


def test_v2_records_are_separate_and_v1_records_do_not_change(tmp_path: Path) -> None:
    h = history_from_results(_results(), "E0")
    v1, _ = predict_records(_fixtures(), h, AS_OF, CFG, set())
    v2, _ = predict_records_v2(_fixtures(), h, AS_OF, CFG, set())
    log = tmp_path / "log.jsonl"
    append_jsonl(log, v1)
    before = log.read_bytes()
    append_jsonl(log, v2)
    assert log.read_bytes().startswith(before)  # appending v2 never rewrites a v1 line
    assert all(r["model_version"] == MODEL_VERSION for r in v1)
    assert all(r["model_version"] == MODEL_VERSION_V2 and r["omega"] == 0.25 for r in v2)
    assert {r["fixture_id"] for r in v1} == {r["fixture_id"] for r in v2}
    again, _ = predict_records(_fixtures(), h, AS_OF, CFG, set())
    assert again == v1
    for r in v2:
        assert abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1.0) < 1e-9


def test_v2_never_reads_data_after_the_as_of_time() -> None:
    h = history_from_results(_results(), "E0")
    base, _ = predict_records_v2(_fixtures(), h, AS_OF, CFG, set())
    late = h.iloc[:20].copy()
    late["ko_late"] = AS_OF + pd.Timedelta(days=3)
    late["done_ts"] = late["ko_late"] + pd.Timedelta(hours=3)
    late["fthg"], late["ftag"], late["hst"], late["ast"] = 9, 0, 40.0, 0.0
    polluted = pd.concat([h, late], ignore_index=True)
    out, _ = predict_records_v2(_fixtures(), polluted, AS_OF, CFG, set())
    assert [(r["p_home"], r["p_draw"], r["p_away"], r["data_fingerprint"]) for r in out] == [
        (r["p_home"], r["p_draw"], r["p_away"], r["data_fingerprint"]) for r in base
    ]


def test_v2_with_zero_weight_is_close_to_v1_and_a_fixture_after_kickoff_is_refused() -> None:
    h = history_from_results(_results(), "E0")
    v1, _ = predict_records(_fixtures(), h, AS_OF, CFG, set())
    v2, _ = predict_records_v2(_fixtures(), h, AS_OF, CFG, set(), omega=0.0)
    for a, b in zip(v1, v2, strict=True):
        assert abs(a["p_home"] - b["p_home"]) < 1e-3
    none, skipped = predict_records_v2(_fixtures(), h, pd.Timestamp("2026-12-20 16:00"), CFG, set())
    assert len(none) == 1 and any("not after the as-of" in s for s in skipped)


def test_settlement_and_report_keep_versions_apart() -> None:
    h = history_from_results(_results(), "E0")
    v1, _ = predict_records(_fixtures(), h, AS_OF, CFG, set())
    v2, _ = predict_records_v2(_fixtures(), h, AS_OF, CFG, set())
    log = v1 + v2
    res = pd.DataFrame(
        {
            "Div": "E0", "Date": "20/12/2026", "HomeTeam": ["Team00", "Team02"],
            "AwayTeam": ["Team01", "Team03"], "FTHG": [2, 0], "FTAG": [1, 0],
            "AvgCH": [2.0, 2.5], "AvgCD": [3.4, 3.3], "AvgCA": [3.8, 3.0],
        }
    )  # fmt: skip
    now = pd.Timestamp("2026-12-22")
    s = settle_records(log, {"E0": res}, set(), now)
    assert len(s) == 4 and {x["model_version"] for x in s} == {MODEL_VERSION, MODEL_VERSION_V2}
    done = {
        x["fixture_id"]
        if x["model_version"] == MODEL_VERSION
        else f"{x['fixture_id']}|{x['model_version']}"
        for x in s
    }
    assert settle_records(log, {"E0": res}, done, now) == []
    only_v1 = build_report(v1, [x for x in s if x["model_version"] == MODEL_VERSION], 1, 50)
    both = build_report(log, s, 1, 50)
    assert MODEL_VERSION_V2 not in only_v1 and both[MODEL_VERSION_V2]["n_settled"] == 2
    assert both["log_loss_model_1x2"] == only_v1["log_loss_model_1x2"]  # v1 numbers unchanged
    assert both["n_logged"] == 2 and "paired_with_v1" in both[MODEL_VERSION_V2]
    assert read_log(Path("nonexistent.jsonl")) == []
