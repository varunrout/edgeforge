"""Shadow log (D-058): frozen model, no look-ahead, append-only log, honest settlement."""

import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from edgeforge.shadow import (
    append_jsonl,
    build_report,
    commit_log,
    history_from_results,
    predict_records,
    read_log,
    run_predict,
    settle_records,
)

CFG = {"seed": 1, "paths": {}}
TEAMS = [f"Team{i:02d}" for i in range(12)]


def _results(seed: int = 0, seasons: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    strength = rng.normal(0, 0.25, len(TEAMS))
    rows = []
    start = pd.Timestamp("2023-08-12")
    for s in range(seasons):
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
                    }
                )  # fmt: skip
    return pd.DataFrame(rows)


def _fixtures(date: str = "20/12/2026") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Div": ["E0", "E0", "E1"], "Date": [date] * 3, "Time": ["15:00", "17:30", "15:00"],
            "HomeTeam": ["Team00", "Team02", "Foo"], "AwayTeam": ["Team01", "Team03", "Bar"],
        }
    )  # fmt: skip


@pytest.fixture(scope="module")
def history() -> pd.DataFrame:
    return history_from_results(_results(), "E0")


def test_predictions_are_probabilities_and_carry_the_audit_fields(history: pd.DataFrame) -> None:
    as_of = pd.Timestamp("2026-12-18 09:00")
    recs, skipped = predict_records(_fixtures(), history, as_of, CFG, set())
    assert len(recs) == 2 and any("E1" in s for s in skipped)
    r = recs[0]
    assert r["p_home"] + r["p_draw"] + r["p_away"] == pytest.approx(1.0, abs=1e-9)
    assert r["p_over_2_5"] + r["p_under_2_5"] == pytest.approx(1.0)
    keys = (
        "fixture_id",
        "kickoff_utc",
        "as_of_utc",
        "model_version",
        "git_sha",
        "config_hash",
        "data_fingerprint",
        "n_train",
        "lam_h",
        "lam_a",
        "rho",
    )
    for k in keys:
        assert k in r
    assert r["as_of_utc"] == "2026-12-18T09:00:00Z" and r["kickoff_utc"].startswith(
        "2026-12-20T15:00"
    )


def test_no_look_ahead_and_no_prediction_after_kickoff(history: pd.DataFrame) -> None:
    as_of = pd.Timestamp("2026-12-18 09:00")
    base, _ = predict_records(_fixtures(), history, as_of, CFG, set())
    future = history.iloc[[0]].copy()  # a result that is not complete by the as-of time
    future[["fthg", "ftag"]] = 9
    future["ko_late"] = pd.Timestamp("2026-12-18 12:00")
    future["done_ts"] = future["ko_late"] + pd.Timedelta(hours=3)
    both = pd.concat([history, future], ignore_index=True)
    with_future, _ = predict_records(_fixtures(), both, as_of, CFG, set())
    assert [r["p_home"] for r in base] == [r["p_home"] for r in with_future]
    late, skipped = predict_records(
        _fixtures("18/12/2026"), history, pd.Timestamp("2026-12-18 16:00"), CFG, set()
    )
    assert [r["fixture_id"] for r in late] == ["E0_20261218_team02_team03"]
    assert any("not after the as-of" in s for s in skipped)


def test_log_is_append_only_one_prediction_per_fixture_and_commit_touches_only_the_log(
    tmp_path: Path, history: pd.DataFrame
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True)
    (repo / "other.txt").write_text("x")
    subprocess.run(
        ["git", "add", "other.txt"], cwd=repo, check=True
    )  # staged, must not be committed
    log = repo / "artifacts" / "shadow" / "log.jsonl"
    recs, _ = predict_records(_fixtures(), history, pd.Timestamp("2026-12-18 09:00"), CFG, set())
    append_jsonl(log, recs)
    first = log.read_text()
    sha = commit_log([log], "shadow batch", repo)
    shown = subprocess.run(
        ["git", "show", "--name-only", "--format=", sha],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert shown.stdout.split() == ["artifacts/shadow/log.jsonl"]
    ids = {r["fixture_id"] for r in read_log(log)}
    again, skipped = predict_records(
        _fixtures(), history, pd.Timestamp("2026-12-18 10:00"), CFG, ids
    )
    assert again == [] and sum("already in the log" in s for s in skipped) == 2
    append_jsonl(log, [])
    assert log.read_text() == first  # nothing rewrote earlier records


def test_run_predict_explains_what_to_supply_when_no_covered_fixtures(tmp_path: Path) -> None:
    f = tmp_path / "fx.csv"
    _fixtures().iloc[[2]].to_csv(f, index=False)
    with pytest.raises(ValueError, match="supply a CSV"):
        run_predict(f, cfg=CFG)


def _logged(n: int, noise: float) -> tuple[list[dict], dict[str, pd.DataFrame]]:  # type: ignore[type-arg]
    rng = np.random.default_rng(3)
    log, rows = [], []
    for i in range(n):
        close = rng.dirichlet([6, 3, 4])
        res = rng.choice(3, p=close)  # the market is calibrated by construction
        pm = np.clip(close + noise * rng.normal(0, 1, 3), 0.02, None)
        pm = pm / pm.sum()
        d = pd.Timestamp("2026-08-01") + pd.Timedelta(days=i)
        log.append(
            {
                "fixture_id": f"E0_{d:%Y%m%d}_a{i}_b{i}", "league": "E0", "home": f"a{i}", "away": f"b{i}",
                "kickoff_utc": d.strftime("%Y-%m-%dT15:00:00Z"),
                "p_home": float(pm[0]), "p_draw": float(pm[1]), "p_away": float(pm[2]), "p_over_2_5": 0.5, "p_under_2_5": 0.5,
            }
        )  # fmt: skip
        odds = 1.06 / close
        rows.append(
            {
                "Div": "E0", "Date": d.strftime("%d/%m/%Y"), "HomeTeam": f"a{i}", "AwayTeam": f"b{i}",
                "FTHG": [2, 1, 0][res], "FTAG": [0, 1, 2][res],
                "PPCH": odds[0], "PPCD": odds[1], "PPCA": odds[2], "AvgC>2.5": 1.9, "AvgC<2.5": 2.0,
            }
        )  # fmt: skip
    return log, {"E0": pd.DataFrame(rows)}


def test_settlement_uses_pinnacle_closing_waits_for_the_result() -> None:
    log, res = _logged(10, 0.0)
    now = pd.Timestamp("2026-08-05 12:00")
    s = settle_records(log, res, set(), now)
    assert len(s) == 4  # kickoffs on 1-4 August are three hours past; the 5th at 15:00 is not
    assert (
        s[0]["close_1x2_columns"] == ["PPCH", "PPCD", "PPCA"]
        and s[0]["close_1x2_source"] == "Pinnacle closing"
    )
    assert s[0]["close_home"] + s[0]["close_draw"] + s[0]["close_away"] == pytest.approx(1.0)
    assert s[0]["close_ou_source"] == "market-average closing"
    assert settle_records(log, res, {x["fixture_id"] for x in s}, now) == []


def test_report_says_inconclusive_for_small_n_and_detects_a_worse_model_for_large_n() -> None:
    log, res = _logged(60, 0.05)
    s = settle_records(log, res, set(), pd.Timestamp("2027-01-01"))
    small = build_report(log, s, 1, 200)
    assert small["n_settled"] == 60 and small["verdict"].startswith("inconclusive")
    assert "fewer than the 200" in small["verdict"] and small["no_betting_no_pnl"]
    assert "NOT a realised CLV" in small["model_implied_value_vs_close"]["definition"]
    log, res = _logged(900, 0.12)
    s = settle_records(log, res, set(), pd.Timestamp("2027-12-31"))
    big = build_report(log, s, 1, 300)
    assert big["verdict"] == "model worse than the closing market"
    assert big["log_loss_model_1x2"] > big["log_loss_closing_1x2"]
    assert build_report(log, [], 1)["verdict"].startswith("inconclusive: no settled")
