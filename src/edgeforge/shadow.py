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
from edgeforge.models.dixon_coles_shots import fit_dixon_coles_shots
from edgeforge.pricing.devig import proportional
from edgeforge.pricing.markets import market_probs
from edgeforge.provenance import config_hash, git_sha

log = logging.getLogger(__name__)
SHADOW_DIR = PROJECT_ROOT / "artifacts" / "shadow"
LOG_PATH = SHADOW_DIR / "log.jsonl"
SETTLED_PATH = SHADOW_DIR / "settled.jsonl"
MODEL_VERSION = "dc_opening_v1"
MODEL_VERSION_V2 = "dc_opening_v2"  # D-065, forward only
OMEGA_V2 = 0.25  # frozen in D-066
LEAGUES = ("E0", "D1", "SP1", "I1", "F1")
HALF_LIFE_DAYS = 365
WINDOW_DAYS = 1825
MIN_TRAIN = 150
COLD_START_MATCHES = 20  # fewer matches than this in the window: flagged, still predicted (D-060)
MIN_SETTLED_FOR_VERDICT = 200
MIN_SEGMENT_FOR_INTERVAL = 30
BASE_URL = "https://www.football-data.co.uk"
CLOSE_1X2 = (("PSCH", "PSCD", "PSCA"),)  # PP* is Paddy Power, not Pinnacle (D-059)
CLOSE_OU = (("PC>2.5", "PC<2.5"), ("PSC>2.5", "PSC<2.5"))
AVG_1X2, AVG_OU = ("AvgCH", "AvgCD", "AvgCA"), ("AvgC>2.5", "AvgC<2.5")


def _ver(rec: dict[str, Any]) -> str:
    """Model version of a log or settled record (records written before D-065 are v1)."""
    return str(rec.get("model_version", MODEL_VERSION))


def _key(rec: dict[str, Any]) -> str:
    """One prediction per fixture and version: v1 keeps the plain fixture id."""
    fid = str(rec["fixture_id"])
    return fid if _ver(rec) == MODEL_VERSION else f"{fid}|{_ver(rec)}"


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
        "hst": pd.to_numeric(r["HST"], errors="coerce").to_numpy(float) if "HST" in r else np.nan,
        "ast": pd.to_numeric(r["AST"], errors="coerce").to_numpy(float) if "AST" in r else np.nan,
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
                "home_n_matches": int(((win["home"] == r.HomeTeam) | (win["away"] == r.HomeTeam)).sum()),
                "away_n_matches": int(((win["home"] == r.AwayTeam) | (win["away"] == r.AwayTeam)).sum()),
                "cold_start_teams": [t for t in (str(r.HomeTeam), str(r.AwayTeam)) if int(((win["home"] == t) | (win["away"] == t)).sum()) < COLD_START_MATCHES],
                "lam_h": float(lam_h[i]), "lam_a": float(lam_a[i]), "rho": float(m.rho),
                "p_home": float(p1[0]), "p_draw": float(p1[1]), "p_away": float(p1[2]),
                "p_over_2_5": po, "p_under_2_5": 1.0 - po,
            })  # fmt: skip
    return recs, skipped


def predict_records_v2(
    fixtures: pd.DataFrame,
    history: pd.DataFrame,
    as_of: pd.Timestamp,
    cfg: dict[str, Any],
    existing: set[str],
    omega: float = OMEGA_V2,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Forward-only candidate dc_opening_v2 (D-065, D-066): v1 plus a shots-on-target likelihood.

    Same window, completion rule and record fields as v1; trains only on matches complete by
    `as_of`. `existing` holds the fixture ids that already have a v2 record."""
    skipped: list[str] = []
    recs: list[dict[str, Any]] = []
    fx = fixtures.copy()
    fx["ko_utc"] = kickoff_utc(fx)
    hist = history.copy()
    for col in ("hst", "ast"):
        if col not in hist:
            hist[col] = np.nan
    for league, g in fx.groupby("Div"):
        league = str(league)
        if league not in LEAGUES:
            skipped.append(f"{league}: league not covered by the model ({len(g)} fixtures)")
            continue
        h = hist[hist["league"] == league]
        win = h[(h["done_ts"] <= as_of) & (h["ko_late"] >= as_of - pd.Timedelta(days=WINDOW_DAYS))]
        if len(win) < MIN_TRAIN:
            skipped.append(f"{league}: only {len(win)} training matches")
            continue
        names = sorted(
            set(win["home"]) | set(win["away"]) | set(g["HomeTeam"]) | set(g["AwayTeam"])
        )
        idx = {t: i for i, t in enumerate(names)}
        age = ((as_of - win["ko_late"]).dt.total_seconds() / 86400.0).to_numpy(float)
        m = fit_dixon_coles_shots(
            win["home"].map(idx).to_numpy(),
            win["away"].map(idx).to_numpy(),
            win["fthg"].to_numpy(np.int64),
            win["ftag"].to_numpy(np.int64),
            win["hst"].to_numpy(float),
            win["ast"].to_numpy(float),
            age,
            names,
            float(np.log(2.0) / HALF_LIFE_DAYS),
            omega,
            fit_rho=True,
        )
        fp = hashlib.sha256(
            "\n".join(
                sorted(
                    f"{r.home}|{r.away}|{r.ko_late}|{r.fthg}|{r.ftag}|{r.hst}|{r.ast}"
                    for r in win.itertuples()
                )
            ).encode()
        ).hexdigest()[:16]
        lam_h, lam_a = m.lambdas(g["HomeTeam"], g["AwayTeam"])
        probs = market_probs(dc_grid(np.asarray(lam_h), np.asarray(lam_a), m.rho))
        n_shots = int((win["hst"].notna() & win["ast"].notna()).sum())

        def n_of(t: str, w: pd.DataFrame = win) -> int:
            return int(((w["home"] == t) | (w["away"] == t)).sum())

        for i, r in enumerate(g.itertuples()):
            ko = pd.Timestamp(r.ko_utc)  # type: ignore[arg-type]
            fid = f"{league}_{ko:%Y%m%d}_{slug(str(r.HomeTeam))}_{slug(str(r.AwayTeam))}"
            if fid in existing:
                skipped.append(f"{fid}: already has a v2 record (one prediction per fixture)")
                continue
            if pd.isna(ko) or ko.tz_localize(None) <= as_of:
                skipped.append(f"{fid}: kickoff {ko} is not after the as-of time {as_of}")
                continue
            p1 = probs["1x2"][i]
            po = float(probs["over_2.5"][i])
            recs.append({
                "fixture_id": fid, "league": league, "home": str(r.HomeTeam), "away": str(r.AwayTeam),
                "kickoff_utc": ko.strftime("%Y-%m-%dT%H:%M:%SZ"), "as_of_utc": as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "model_version": MODEL_VERSION_V2, "git_sha": git_sha(), "config_hash": config_hash(cfg),
                "data_fingerprint": fp, "n_train": int(len(win)), "n_train_with_shots": n_shots,
                "omega": float(omega),
                "home_n_matches": n_of(str(r.HomeTeam)), "away_n_matches": n_of(str(r.AwayTeam)),
                "cold_start_teams": [t for t in (str(r.HomeTeam), str(r.AwayTeam)) if n_of(t) < COLD_START_MATCHES],
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
    shots = con.execute("SELECT match_id, hst, ast FROM matches").df()
    history = history.merge(shots, on="match_id", how="left")
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
    prior = read_log(log_path)
    existing = {r["fixture_id"] for r in prior if _ver(r) == MODEL_VERSION}
    existing_v2 = {r["fixture_id"] for r in prior if _ver(r) == MODEL_VERSION_V2}
    recs, skipped = predict_records(fx, history, pd.Timestamp(now), cfg, existing)
    recs2, skipped2 = predict_records_v2(fx, history, pd.Timestamp(now), cfg, existing_v2)
    out: dict[str, Any] = {
        "as_of_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "logged": len(recs),
        "logged_v2": len(recs2),
        "skipped": skipped,
        "skipped_v2": skipped2,
        "commit": None,
    }
    if git_sha().endswith("-dirty"):
        out["warning"] = (
            "uncommitted code changes: the logged git_sha ends in -dirty; commit the code first"
        )
    if recs or recs2:
        append_jsonl(log_path, recs + recs2)
        if commit:
            out["commit"] = commit_log(
                [log_path],
                f"shadow: {len(recs)} pre-match predictions (v1) and {len(recs2)} (v2) as of {out['as_of_utc']} ({', '.join(sorted({r['league'] for r in recs + recs2}))})",
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
        if _key(r) in settled_ids:
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
            "fixture_id": r["fixture_id"], "model_version": _ver(r), "settled_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
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


def _build_report_v1(
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
    rep["model_implied_value_vs_close"]["note"] = (
        "non-negative by construction: both probability vectors sum to one, so the largest "
        "gap is never below zero; it measures how far the model disagrees with the close, "
        "not whether the disagreement is right, and its interval is not evidence of an edge"
    )
    cold = np.array([bool(r.get("cold_start_teams")) for r, _ in rows])
    segments: dict[str, Any] = {}
    for name, mask in (("cold_start", cold), ("established", ~cold)):
        k = int(mask.sum())
        seg: dict[str, Any] = {"n": k, "fixtures": [str(i) for i in ids[mask]]}
        if k:
            seg["log_loss_model_1x2"] = float(lm[mask].mean())
            seg["log_loss_closing_1x2"] = float(lk[mask].mean())
            seg["log_loss_diff_model_minus_market_1x2"] = float((lm - lk)[mask].mean())
        seg["interval"] = (
            "none: too few fixtures for a bootstrap interval to mean anything"
            if k < MIN_SEGMENT_FOR_INTERVAL
            else cluster_diff_summary((lm - lk)[mask], ids[mask], seed)
        )
        segments[name] = seg
    rep["segments"] = segments
    rep["segment_definition"] = (
        "cold_start: a fixture with a team that has fewer than "
        f"{COLD_START_MATCHES} matches in the training window (D-060); reported, never removed"
    )
    pick_idx = np.arange(len(rows))
    rep["per_fixture"] = [
        {
            "fixture_id": str(ids[i]),
            "result": rows[i][1]["result"],
            "cold_start": bool(cold[i]),
            "model_p_of_result": float(pm[i, y[i]]),
            "close_p_of_result": float(pk[i, y[i]]),
            "log_loss_model": float(lm[i]),
            "log_loss_closing": float(lk[i]),
        }
        for i in pick_idx
    ]
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


def _rows_of(
    log_records: list[dict[str, Any]], settled: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    by_id = {r["fixture_id"]: r for r in log_records}
    return [
        (by_id[s["fixture_id"]], s)
        for s in settled
        if s["fixture_id"] in by_id and "close_home" in s
    ]


def _ll_rows(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[Any, Any, Any]:
    y = np.array([{"home": 0, "draw": 1, "away": 2}[s["result"]] for _, s in rows])
    pm = np.array([[r["p_home"], r["p_draw"], r["p_away"]] for r, _ in rows])
    pk = np.array([[s["close_home"], s["close_draw"], s["close_away"]] for _, s in rows])
    return (
        M.log_loss_multiclass(pm, y),
        M.log_loss_multiclass(pk, y),
        np.array([r["fixture_id"] for r, _ in rows]),
    )


def _version_block(
    log_records: list[dict[str, Any]],
    settled: list[dict[str, Any]],
    v1_rows: list[tuple[dict[str, Any], dict[str, Any]]],
    version: str,
    seed: int,
) -> dict[str, Any]:
    """Report block for a forward-only candidate version (D-065), next to v1."""
    recs = [r for r in log_records if _ver(r) == version]
    sett = [s for s in settled if _ver(s) == version]
    rows = _rows_of(recs, sett)
    blk: dict[str, Any] = {
        "model_version": version,
        "n_logged": len(recs),
        "n_settled": len(sett),
        "n_with_closing_1x2": len(rows),
    }
    if rows:
        lm, lk, ids = _ll_rows(rows)
        blk["log_loss_model_1x2"] = float(lm.mean())
        blk["log_loss_closing_1x2"] = float(lk.mean())
        blk["log_loss_diff_model_minus_market_1x2"] = cluster_diff_summary(lm - lk, ids, seed)
        v1_by_id = {r["fixture_id"]: (r, s) for r, s in v1_rows}
        both = [(r, s) for r, s in rows if r["fixture_id"] in v1_by_id]
        if both:
            l2, _, ids2 = _ll_rows(both)
            l1, _, _ = _ll_rows([v1_by_id[r["fixture_id"]] for r, _ in both])
            blk["paired_with_v1"] = {
                "n": len(both),
                "log_loss_diff_v2_minus_v1_1x2": cluster_diff_summary(l2 - l1, ids2, seed),
            }
    n = len(rows)
    blk["verdict"] = (
        "inconclusive: no settled fixtures with closing prices yet"
        if n == 0
        else f"inconclusive: {n} settled fixtures, fewer than the {MIN_SETTLED_FOR_VERDICT} fixed in D-058; nothing can be concluded about the model"
        if n < MIN_SETTLED_FOR_VERDICT
        else "inconclusive: the verdict for a second version needs the paired interval to exclude zero"
    )
    return blk


def build_report(
    log_records: list[dict[str, Any]], settled: list[dict[str, Any]], seed: int, n_boot: int = 1000
) -> dict[str, Any]:
    """v1 numbers exactly as before (records without a version are v1); v2 next to them."""
    v1_log = [r for r in log_records if _ver(r) == MODEL_VERSION]
    v1_set = [s for s in settled if _ver(s) == MODEL_VERSION]
    rep = _build_report_v1(v1_log, v1_set, seed, n_boot)
    if any(_ver(r) == MODEL_VERSION_V2 for r in log_records):
        rep[MODEL_VERSION_V2] = _version_block(
            log_records, settled, _rows_of(v1_log, v1_set), MODEL_VERSION_V2, seed
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
    done = {_key(s) for s in settled}
    pending = [
        r
        for r in recs
        if _key(r) not in done
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
