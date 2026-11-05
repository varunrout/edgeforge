"""Pre-match shadow log (D-058): timestamped predictions before kickoff, settled against the
Pinnacle closing market afterwards. Pre-match only; no betting, no stakes, no P&L.

`predict` refits the frozen Pillar A Dixon-Coles model (half-life 365 days, five-year window,
fixed ridge, no calibration, no promoted-team prior) on matches complete before the as-of time and
appends one record per fixture to `artifacts/shadow/log.jsonl`. `settle` never edits that file: it
writes `artifacts/shadow/settled.jsonl` and `artifacts/metrics/shadow_report.json`.
"""

import hashlib
import io
import json
import logging
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from edgeforge.config import PROJECT_ROOT, load_config, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.evaluation.common import load_fd_clock, write_json
from edgeforge.evaluation.phase4 import cluster_diff_summary
from edgeforge.features.pit import RESULT_DELAY, fd_clock
from edgeforge.models.dixon_coles import dc_grid, fit_dixon_coles
from edgeforge.pricing.devig import proportional
from edgeforge.pricing.markets import market_probs
from edgeforge.provenance import config_hash, git_sha

log = logging.getLogger(__name__)
SHADOW_DIR = PROJECT_ROOT / "artifacts" / "shadow"
LOG_PATH = SHADOW_DIR / "log.jsonl"
SETTLED_PATH = SHADOW_DIR / "settled.jsonl"
MODEL_VERSION = "dc_opening_v1"
LEAGUES = ("E0", "D1", "SP1", "I1", "F1")
HALF_LIFE_DAYS = 365
WINDOW_DAYS = 1825
MIN_TRAIN = 150
MIN_SETTLED_FOR_VERDICT = 200
BASE_URL = "https://www.football-data.co.uk"
CLOSE_1X2 = (("PSCH", "PSCD", "PSCA"), ("PPCH", "PPCD", "PPCA"))
CLOSE_OU = (("PSC>2.5", "PSC<2.5"), ("PC>2.5", "PC<2.5"))
AVG_1X2, AVG_OU = ("AvgCH", "AvgCD", "AvgCA"), ("AvgC>2.5", "AvgC<2.5")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def season_code(d: pd.Timestamp) -> str:
    y = d.year if d.month >= 7 else d.year - 1
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


# ----------------------------------------------------------------------------- data access


def fetch_csv(url: str, raw_dir: Path | None = None) -> pd.DataFrame:
    """Fetch a football-data.co.uk CSV (never cached for reuse) and keep a dated copy."""
    r = requests.get(
        url, timeout=30, headers={"User-Agent": "edgeforge-research/0.1 (non-commercial)"}
    )
    r.raise_for_status()
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        (raw_dir / f"{stamp}_{slug(url)}.csv").write_bytes(r.content)
    return pd.read_csv(io.StringIO(r.content.decode("utf-8-sig")))


def parse_dates(df: pd.DataFrame) -> pd.Series:
    d = pd.to_datetime(df["Date"], format="%d/%m/%Y", errors="coerce")
    return d.fillna(pd.to_datetime(df["Date"], format="%d/%m/%y", errors="coerce"))


def kickoff_utc(df: pd.DataFrame) -> pd.Series:
    date = parse_dates(df)
    t = (
        pd.to_timedelta(df["Time"].fillna("12:00").astype(str) + ":00", errors="coerce")
        if "Time" in df
        else pd.Timedelta(hours=12)
    )
    local = (date + t).dt.tz_localize(ZoneInfo("Europe/London"), ambiguous="NaT", nonexistent="NaT")
    return local.dt.tz_convert("UTC")


def history_from_results(res: pd.DataFrame, league: str) -> pd.DataFrame:
    """Completed matches from a football-data results file in the warehouse clock format."""
    r = res.dropna(subset=["FTHG", "FTAG", "HomeTeam", "AwayTeam"]).copy()
    date = parse_dates(r)
    t = (
        pd.to_timedelta(r["Time"].fillna("").astype(str) + ":00", errors="coerce")
        if "Time" in r
        else pd.Series(pd.NaT, index=r.index)
    )
    out = pd.DataFrame({
        "match_id": [f"{league}_{d:%Y%m%d}_{slug(h)}_{slug(a)}" for d, h, a in zip(date, r["HomeTeam"], r["AwayTeam"], strict=True)],
        "league": league, "season": season_code(date.iloc[0]) if len(date) else "", "season_start_year": 0,
        "date": date, "kickoff_local": date + t, "home": r["HomeTeam"].to_numpy(), "away": r["AwayTeam"].to_numpy(),
        "fthg": r["FTHG"].astype(int).to_numpy(), "ftag": r["FTAG"].astype(int).to_numpy(),
    })  # fmt: skip
    return fd_clock(out)


# ----------------------------------------------------------------------------- predict


def predict_records(
    fixtures: pd.DataFrame,
    history: pd.DataFrame,
    as_of: pd.Timestamp,
    cfg: dict[str, Any],
    existing: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Frozen Dixon-Coles predictions for the fixtures; `as_of` is a naive UTC timestamp."""
    skipped: list[str] = []
    recs: list[dict[str, Any]] = []
    fx = fixtures.copy()
    fx["ko_utc"] = kickoff_utc(fx)
    for league, g in fx.groupby("Div"):
        league = str(league)
        if league not in LEAGUES:
            skipped.append(f"{league}: league not covered by the model ({len(g)} fixtures)")
            continue
        h = history[history["league"] == league]
        win = h[(h["done_ts"] <= as_of) & (h["ko_late"] >= as_of - pd.Timedelta(days=WINDOW_DAYS))]
        if len(win) < MIN_TRAIN:
            skipped.append(f"{league}: only {len(win)} training matches")
            continue
        names = sorted(
            set(win["home"]) | set(win["away"]) | set(g["HomeTeam"]) | set(g["AwayTeam"])
        )
        idx = {t: i for i, t in enumerate(names)}
        age = ((as_of - win["ko_late"]).dt.total_seconds() / 86400.0).to_numpy(float)
        m = fit_dixon_coles(
            win["home"].map(idx).to_numpy(), win["away"].map(idx).to_numpy(), win["fthg"].to_numpy(np.int64),
            win["ftag"].to_numpy(np.int64), age, names, float(np.log(2.0) / HALF_LIFE_DAYS), fit_rho=True,
        )  # fmt: skip
        fp = hashlib.sha256(
            "\n".join(
                sorted(f"{r.home}|{r.away}|{r.ko_late}|{r.fthg}|{r.ftag}" for r in win.itertuples())
            ).encode()
        ).hexdigest()[:16]
        lam_h, lam_a = m.lambdas(g["HomeTeam"], g["AwayTeam"])
        probs = market_probs(dc_grid(np.asarray(lam_h), np.asarray(lam_a), m.rho))
        for i, r in enumerate(g.itertuples()):
            ko = pd.Timestamp(r.ko_utc)  # type: ignore[arg-type]
            fid = f"{league}_{ko:%Y%m%d}_{slug(str(r.HomeTeam))}_{slug(str(r.AwayTeam))}"
            if fid in existing:
                skipped.append(f"{fid}: already in the log (one prediction per fixture)")
                continue
            if pd.isna(ko) or ko.tz_localize(None) <= as_of:
                skipped.append(f"{fid}: kickoff {ko} is not after the as-of time {as_of}")
                continue
            p1 = probs["1x2"][i]
            po = float(probs["over_2.5"][i])
            recs.append({
                "fixture_id": fid, "league": league, "home": str(r.HomeTeam), "away": str(r.AwayTeam),
                "kickoff_utc": ko.strftime("%Y-%m-%dT%H:%M:%SZ"), "as_of_utc": as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "model_version": MODEL_VERSION, "git_sha": git_sha(), "config_hash": config_hash(cfg),
                "data_fingerprint": fp, "n_train": int(len(win)),
                "lam_h": float(lam_h[i]), "lam_a": float(lam_a[i]), "rho": float(m.rho),
                "p_home": float(p1[0]), "p_draw": float(p1[1]), "p_away": float(p1[2]),
                "p_over_2_5": po, "p_under_2_5": 1.0 - po,
            })  # fmt: skip
    return recs, skipped


def read_log(path: Path = LOG_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def commit_log(paths: list[Path], message: str, repo: Path = PROJECT_ROOT) -> str:
    """Commit only the given files in the local repository (never pushes) and return the sha."""
    rel = [str(p.resolve().relative_to(repo.resolve())) for p in paths]
    subprocess.run(["git", "add", "--", *rel], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message, "--only", "--", *rel],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def run_predict(
    fixtures_csv: Path,
    as_of: datetime | None = None,
    commit: bool = False,
    cfg: dict[str, Any] | None = None,
    log_path: Path = LOG_PATH,
    fetch: Any = fetch_csv,
) -> dict[str, Any]:
    from edgeforge.evaluation.common import connect

    cfg = cfg or load_config("data")
    now = (as_of or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
    fx = pd.read_csv(fixtures_csv, encoding="utf-8-sig")
    need = {"Div", "Date", "HomeTeam", "AwayTeam"}
    if not need <= set(fx.columns):
        raise ValueError(
            f"fixtures file needs columns {sorted(need)} (football-data fixtures.csv format)"
        )
    fx = fx[fx["Div"].isin(LEAGUES)]
    if fx.empty:
        raise ValueError(
            f"no fixtures for {LEAGUES} in {fixtures_csv}; supply a CSV with columns Div,Date,Time,HomeTeam,AwayTeam"
        )
    con = connect(cfg)
    history = load_fd_clock(con)
    con.close()
    raw_dir = resolve_path(cfg, "raw_dir") / "shadow"
    fresh = []
    for league in sorted(set(fx["Div"])):
        d0 = parse_dates(fx[fx["Div"] == league]).min()
        res = fetch(f"{BASE_URL}/mmz4281/{season_code(d0)}/{league}.csv", raw_dir)
        fresh.append(history_from_results(res, str(league)))
    if fresh:
        add = pd.concat(fresh, ignore_index=True)
        key = ["league", "home", "away", "date"]
        history["date"] = pd.to_datetime(history["date"])
        add = add.merge(history[key], on=key, how="left", indicator=True)
        add = add[add["_merge"] == "left_only"].drop(columns="_merge")
        history = pd.concat([history, add], ignore_index=True)
    existing = {r["fixture_id"] for r in read_log(log_path)}
    recs, skipped = predict_records(fx, history, pd.Timestamp(now), cfg, existing)
    out: dict[str, Any] = {
        "as_of_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "logged": len(recs),
        "skipped": skipped,
        "commit": None,
    }
    if recs:
        append_jsonl(log_path, recs)
        if commit:
            out["commit"] = commit_log(
                [log_path],
                f"shadow: {len(recs)} pre-match predictions as of {out['as_of_utc']} ({', '.join(sorted({r['league'] for r in recs}))})",
            )
    return out


# ----------------------------------------------------------------------------- settle


def _pick(
    row: pd.Series, groups: tuple[tuple[str, ...], ...]
) -> tuple[tuple[str, ...], list[float]] | None:
    for cols in groups:
        if all(c in row.index and pd.notna(row[c]) and float(row[c]) > 1.0 for c in cols):
            return cols, [float(row[c]) for c in cols]
    return None


def settle_records(
    log_records: list[dict[str, Any]],
    results: dict[str, pd.DataFrame],
    settled_ids: set[str],
    now: pd.Timestamp,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in log_records:
        if r["fixture_id"] in settled_ids:
            continue
        ko = pd.Timestamp(r["kickoff_utc"]).tz_localize(None)
        if ko + RESULT_DELAY > now or r["league"] not in results:
            continue
        df = results[r["league"]]
        d = ko.normalize()
        m = df[
            (parse_dates(df).dt.normalize() == d)
            & (df["HomeTeam"] == r["home"])
            & (df["AwayTeam"] == r["away"])
        ]
        if m.empty or pd.isna(m.iloc[0].get("FTHG")):
            continue
        row = m.iloc[0]
        hg, ag = int(row["FTHG"]), int(row["FTAG"])
        rec: dict[str, Any] = {
            "fixture_id": r["fixture_id"], "settled_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fthg": hg, "ftag": ag, "result": "home" if hg > ag else "draw" if hg == ag else "away", "over_2_5": int(hg + ag > 2),
        }  # fmt: skip
        pick = _pick(row, CLOSE_1X2)
        source = "Pinnacle closing"
        if pick is None:
            pick = _pick(row, (AVG_1X2,))
            source = "market-average closing (no Pinnacle closing in the file)"
        if pick is not None:
            p = proportional(np.array([pick[1]]))[0]
            rec.update(
                {
                    "close_1x2_columns": list(pick[0]),
                    "close_1x2_source": source,
                    "close_home": float(p[0]),
                    "close_draw": float(p[1]),
                    "close_away": float(p[2]),
                }
            )
        ou = _pick(row, CLOSE_OU)
        ou_source = "Pinnacle closing"
        if ou is None:
            ou = _pick(row, (AVG_OU,))
            ou_source = "market-average closing"
        if ou is not None:
            q = proportional(np.array([ou[1]]))[0]
            rec.update(
                {
                    "close_ou_columns": list(ou[0]),
                    "close_ou_source": ou_source,
                    "close_over_2_5": float(q[0]),
                }
            )
        out.append(rec)
    return out


def build_report(
    log_records: list[dict[str, Any]], settled: list[dict[str, Any]], seed: int, n_boot: int = 1000
) -> dict[str, Any]:
    by_id = {r["fixture_id"]: r for r in log_records}
    rows = [
        (by_id[s["fixture_id"]], s)
        for s in settled
        if s["fixture_id"] in by_id and "close_home" in s
    ]
    rep: dict[str, Any] = {
        "n_logged": len(log_records), "n_settled": len(settled), "n_with_closing_1x2": len(rows),
        "no_betting_no_pnl": True, "pre_match_only": True,
    }  # fmt: skip
    if not rows:
        rep["verdict"] = "inconclusive: no settled fixtures with closing prices yet"
        return rep
    y = np.array([{"home": 0, "draw": 1, "away": 2}[s["result"]] for _, s in rows])
    pm = np.array([[r["p_home"], r["p_draw"], r["p_away"]] for r, _ in rows])
    pk = np.array([[s["close_home"], s["close_draw"], s["close_away"]] for _, s in rows])
    lm, lk = M.log_loss_multiclass(pm, y), M.log_loss_multiclass(pk, y)
    ids = np.array([r["fixture_id"] for r, _ in rows])
    diff = cluster_diff_summary(lm - lk, ids, seed)
    gap = pm / pk - 1.0
    best = gap.argmax(axis=1)
    value = gap[np.arange(len(rows)), best]
    rep.update({
        "closing_sources": sorted({s["close_1x2_source"] for _, s in rows}),
        "log_loss_model_1x2": float(lm.mean()), "log_loss_closing_1x2": float(lk.mean()),
        "log_loss_diff_model_minus_market_1x2": diff,
        "model_implied_value_vs_close": {
            "definition": "for each fixture the outcome with the largest model-minus-closing probability gap: model probability divided by closing probability minus one; NOT a realised CLV, no price was taken",
            "mean": float(value.mean()), "ci": cluster_diff_summary(value, ids, seed),
        },
    })  # fmt: skip
    ou_rows = [(r, s) for r, s in rows if "close_over_2_5" in s]
    if ou_rows:
        yo = np.array([s["over_2_5"] for _, s in ou_rows], dtype=float)
        po = np.array([r["p_over_2_5"] for r, _ in ou_rows])
        pc = np.array([s["close_over_2_5"] for _, s in ou_rows])
        d2 = M.log_loss_binary(po, yo) - M.log_loss_binary(pc, yo)
        rep["over_2_5"] = {
            "n": len(ou_rows), "closing_sources": sorted({s["close_ou_source"] for _, s in ou_rows}),
            "log_loss_model": float(M.log_loss_binary(po, yo).mean()), "log_loss_closing": float(M.log_loss_binary(pc, yo).mean()),
            "log_loss_diff_model_minus_market": cluster_diff_summary(d2, np.array([r["fixture_id"] for r, _ in ou_rows]), seed),
        }  # fmt: skip
    n = len(rows)
    ci_lo, ci_hi = diff["ci_low"], diff["ci_high"]
    if n < MIN_SETTLED_FOR_VERDICT:
        rep["verdict"] = (
            f"inconclusive: {n} settled fixtures, fewer than the {MIN_SETTLED_FOR_VERDICT} fixed in D-058; nothing can be concluded about the model"
        )
    elif ci_lo <= 0 <= ci_hi:
        rep["verdict"] = (
            "inconclusive: the confidence interval of the log-loss difference contains zero"
        )
    else:
        rep["verdict"] = (
            "model better than the closing market"
            if diff["estimate"] < 0
            else "model worse than the closing market"
        )
    rep["expectation_recorded_before_results"] = (
        "Pillar A found the closing market better than the model (D-058)"
    )
    return rep


def run_settle(
    cfg: dict[str, Any] | None = None,
    now: datetime | None = None,
    fetch: Any = fetch_csv,
    log_path: Path = LOG_PATH,
    settled_path: Path = SETTLED_PATH,
    report_path: Path | None = None,
) -> dict[str, Any]:
    from edgeforge.provenance import provenance

    cfg = cfg or load_config("data")
    n = pd.Timestamp((now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None))
    recs = read_log(log_path)
    settled = read_log(settled_path)
    done = {s["fixture_id"] for s in settled}
    pending = [
        r
        for r in recs
        if r["fixture_id"] not in done
        and pd.Timestamp(r["kickoff_utc"]).tz_localize(None) + RESULT_DELAY <= n
    ]
    results: dict[str, pd.DataFrame] = {}
    raw_dir = resolve_path(cfg, "raw_dir") / "shadow"
    for r in pending:
        if r["league"] not in results:
            season = season_code(pd.Timestamp(r["kickoff_utc"]))
            results[r["league"]] = fetch(f"{BASE_URL}/mmz4281/{season}/{r['league']}.csv", raw_dir)
    new = settle_records(recs, results, done, n)
    if new:
        append_jsonl(settled_path, new)
    report = build_report(recs, read_log(settled_path), int(cfg["seed"]))
    report["provenance"] = provenance("edgeforge shadow settle", cfg, MODEL_VERSION)
    write_json(report_path or resolve_path(cfg, "metrics_dir") / "shadow_report.json", report)
    return report
