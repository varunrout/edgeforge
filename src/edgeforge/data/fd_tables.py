"""Build `matches` and long-format `odds` from cached football-data.co.uk CSVs.

Odds columns are detected by name from each file's header (column sets vary by season). Bookmaker
codes are derived from the header itself: a code `b` is an early-snapshot 1X2 bookmaker if
`bH, bD, bA` exist; if `b` ends in 'C' and `b[:-1]` is also a code, `b` is the closing snapshot of
`b[:-1]` (this is why `VCH` = VC Bet early, not a closing price).
"""

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from edgeforge.config import resolve_path
from edgeforge.data.dates import parse_fd_date, parse_kickoff

log = logging.getLogger(__name__)

AGGREGATES = frozenset({"Max", "Avg", "BbMx", "BbAv"})
OU_LINE = 2.5
RESULT_COLS = {
    "FTHG": "fthg",
    "FTAG": "ftag",
    "HTHG": "hthg",
    "HTAG": "htag",
    "HS": "hs",
    "AS": "as_",
    "HST": "hst",
    "AST": "ast",
    "HC": "hc",
    "AC": "ac",
    "HF": "hf",
    "AF": "af",
    "HY": "hy",
    "AY": "ay",
    "HR": "hr",
    "AR": "ar",
}


def season_start_year(season: str) -> int:
    a = int(season[:2])
    return 1900 + a if a >= 90 else 2000 + a


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def make_match_id(league: str, season: str, d: Any, home: str, away: str) -> str:
    return f"{league}_{season}_{d:%Y%m%d}_{slug(home)}_{slug(away)}"


@dataclass
class FdFile:
    league: str
    season: str
    path: Path


@dataclass
class OddsStats:
    price_cells_nonblank: int = 0
    price_cells_invalid: int = 0
    ah_rows_without_line: int = 0
    odds_rows_skipped_ragged_matches: int = 0
    rows: int = 0
    by_file: dict[str, dict[str, int]] = field(default_factory=dict)


def cached_football_data(cfg: dict[str, Any]) -> list[FdFile]:
    raw = resolve_path(cfg, "raw_dir") / "footballdata"
    out = []
    for meta in sorted(raw.glob("*.meta.json")):
        info = json.loads(meta.read_text())
        m = re.search(r"mmz4281/(\w+)/(\w+)\.csv", info["url"])
        if m is None or info["status"] != 200:
            continue
        out.append(FdFile(m.group(2), m.group(1), Path(str(meta).replace(".meta.json", ".body"))))
    return sorted(out, key=lambda f: (f.league, season_start_year(f.season)))


def read_csv_rows(path: Path) -> tuple[list[str], list[list[str]], list[bool]]:
    """Header, rows padded/truncated to header width, and a per-row 'ragged' flag."""
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    reader = csv.reader(text.splitlines())
    header = next(reader)
    seen: dict[str, int] = {}
    uniq = []
    for h in header:
        seen[h] = seen.get(h, 0) + 1
        uniq.append(h if seen[h] == 1 else f"{h}__dup{seen[h]}")
    width = len(uniq)
    ih = uniq.index("HomeTeam")
    rows, ragged = [], []
    for r in reader:
        if len(r) <= ih or not r[ih].strip():
            continue
        ragged.append(len(r) != width)
        rows.append((r + [""] * width)[:width])
    return uniq, rows, ragged


def build_matches_for_file(f: FdFile) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Return (matches, raw string frame aligned to matches, drop counts)."""
    header, rows, ragged = read_csv_rows(f.path)
    raw = pd.DataFrame(rows, columns=header, dtype="string")
    raw["__ragged"] = ragged
    dates = raw["Date"].map(lambda s: parse_fd_date(str(s)))
    keep = dates.notna()
    dropped = {"rows_read": len(raw), "rows_dropped_bad_date": int((~keep).sum())}
    raw, dates = raw[keep].reset_index(drop=True), dates[keep].reset_index(drop=True)
    time_col = raw["Time"] if "Time" in raw.columns else pd.Series([""] * len(raw), dtype="string")
    ko = time_col.map(lambda s: parse_kickoff(str(s)))
    m = pd.DataFrame(
        {
            "league": f.league,
            "season": f.season,
            "season_start_year": season_start_year(f.season),
            "date": pd.to_datetime(dates),
            "kickoff_time": ko.map(lambda t: t.strftime("%H:%M") if t else None),
            "home": raw["HomeTeam"].str.strip(),
            "away": raw["AwayTeam"].str.strip(),
        }
    )
    m["kickoff_local"] = [
        pd.Timestamp(f"{d:%Y-%m-%d} {t}") if t else pd.NaT
        for d, t in zip(m["date"], m["kickoff_time"], strict=True)
    ]
    m["match_id"] = [
        make_match_id(f.league, f.season, d, h, a)
        for d, h, a in zip(m["date"], m["home"], m["away"], strict=True)
    ]
    for src, dst in RESULT_COLS.items():
        m[dst] = (
            pd.to_numeric(raw[src], errors="coerce").astype("Int64")
            if src in raw.columns
            else pd.array([pd.NA] * len(raw), dtype="Int64")
        )
    m["ftr"] = raw["FTR"].astype("string") if "FTR" in raw.columns else pd.NA
    m["referee"] = raw["Referee"].astype("string") if "Referee" in raw.columns else pd.NA
    m["ragged_row"] = raw["__ragged"].to_numpy()
    m["source_file"] = f.path.name
    cols = [
        "match_id",
        "league",
        "season",
        "season_start_year",
        "date",
        "kickoff_time",
        "kickoff_local",
        "home",
        "away",
        "fthg",
        "ftag",
        "ftr",
        "hthg",
        "htag",
        "hs",
        "as_",
        "hst",
        "ast",
        "hc",
        "ac",
        "hf",
        "af",
        "hy",
        "ay",
        "hr",
        "ar",
        "referee",
        "ragged_row",
        "source_file",
    ]
    return m[cols], raw, dropped


@dataclass(frozen=True)
class OddsSpec:
    bookmaker: str
    market: str
    snapshot: str
    selections: dict[str, str]  # selection -> column
    line_col: str | None  # column holding the line (AH) or None
    line_value: float | None  # constant line (OU)
    line_source: str


def _codes(header: set[str], suffixes: list[str]) -> set[str]:
    """Codes b for which every `b + suffix` column exists."""
    first = suffixes[0]
    cands = {c[: -len(first)] for c in header if c.endswith(first) and len(c) > len(first)}
    return {b for b in cands if all(b + s in header for s in suffixes)}


def _split_snapshots(codes: set[str]) -> dict[str, str]:
    """code -> (base bookmaker, snapshot) encoded as 'base|snapshot'."""
    out = {}
    for b in codes:
        if b.endswith("C") and b[:-1] in codes:
            out[b] = f"{b[:-1]}|close"
        else:
            out[b] = f"{b}|early"
    return out


def detect_specs(header: list[str]) -> list[OddsSpec]:
    h = set(header)
    specs: list[OddsSpec] = []
    # 1X2
    for code, enc in _split_snapshots(_codes(h, ["H", "D", "A"])).items():
        base, snap = enc.split("|")
        specs.append(
            OddsSpec(
                base,
                "1X2",
                snap,
                {"home": code + "H", "draw": code + "D", "away": code + "A"},
                None,
                None,
                "",
            )
        )
    # Over/Under 2.5
    for code, enc in _split_snapshots(_codes(h, [">2.5", "<2.5"])).items():
        base, snap = enc.split("|")
        specs.append(
            OddsSpec(
                base,
                "OU",
                snap,
                {"over": code + ">2.5", "under": code + "<2.5"},
                None,
                OU_LINE,
                "",
            )
        )
    # Asian handicap (home line)
    for code, enc in _split_snapshots(_codes(h, ["AHH", "AHA"])).items():
        base, snap = enc.split("|")
        book_line = f"{code}AH"
        if book_line in h:
            line_col, src = book_line, "book"
        elif snap == "early" and base in ("BbMx", "BbAv") and "BbAHh" in h:
            line_col, src = "BbAHh", "bb"
        elif snap == "early" and "AHh" in h:
            line_col, src = "AHh", "shared"
        elif snap == "close" and "AHCh" in h:
            line_col, src = "AHCh", "shared"
        else:
            line_col, src = None, "none"
        specs.append(
            OddsSpec(
                base, "AH", snap, {"home": code + "AHH", "away": code + "AHA"}, line_col, None, src
            )
        )
    return specs


def build_odds_for_file(
    f: FdFile, matches: pd.DataFrame, raw: pd.DataFrame, stats: OddsStats
) -> list[pd.DataFrame]:
    parts: list[pd.DataFrame] = []
    ok = ~matches["ragged_row"].to_numpy()
    stats.odds_rows_skipped_ragged_matches += int((~ok).sum())
    mid = matches["match_id"].to_numpy()
    fstats = {"price_cells_nonblank": 0, "price_cells_invalid": 0, "rows": 0}
    for spec in detect_specs(list(raw.columns)):
        line = None
        if spec.line_col is not None:
            line = pd.to_numeric(raw[spec.line_col], errors="coerce").to_numpy(dtype=float)
        for sel, col in spec.selections.items():
            cell = raw[col]
            nonblank = cell.notna() & (cell.str.strip() != "")
            price = pd.to_numeric(cell, errors="coerce").to_numpy(dtype=float)
            valid = np.isfinite(price) & (price > 1.0)
            fstats["price_cells_nonblank"] += int(nonblank.sum())
            fstats["price_cells_invalid"] += int((nonblank.to_numpy() & ~valid).sum())
            if spec.market == "AH":
                has_line = np.isfinite(line) if line is not None else np.zeros(len(raw), bool)
                stats.ah_rows_without_line += int((valid & ok & ~has_line).sum())
                valid = valid & has_line
            sel_mask = valid & ok
            if not sel_mask.any():
                continue
            if spec.market == "AH" and line is not None:
                line_vals = line[sel_mask]
            elif spec.line_value is not None:
                line_vals = np.full(int(sel_mask.sum()), spec.line_value)
            else:
                line_vals = np.full(int(sel_mask.sum()), np.nan)
            part = pd.DataFrame(
                {
                    "match_id": mid[sel_mask],
                    "bookmaker": spec.bookmaker,
                    "is_aggregate": spec.bookmaker in AGGREGATES,
                    "market": spec.market,
                    "selection": sel,
                    "line": line_vals,
                    "price": price[sel_mask],
                    "snapshot": spec.snapshot,
                    "source_column": col,
                    "line_source": spec.line_source,
                }
            )
            fstats["rows"] += len(part)
            parts.append(part)
    stats.by_file[f"{f.league}_{f.season}"] = fstats
    stats.price_cells_nonblank += fstats["price_cells_nonblank"]
    stats.price_cells_invalid += fstats["price_cells_invalid"]
    stats.rows += fstats["rows"]
    return parts


def build_football_data(
    cfg: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    mats, odds_parts = [], []
    stats = OddsStats()
    drops: dict[str, dict[str, int]] = {}
    for f in cached_football_data(cfg):
        m, raw, dropped = build_matches_for_file(f)
        drops[f"{f.league}_{f.season}"] = dropped
        mats.append(m)
        odds_parts.extend(build_odds_for_file(f, m, raw, stats))
    matches = pd.concat(mats, ignore_index=True)
    odds = pd.concat(odds_parts, ignore_index=True)
    for col in ("bookmaker", "market", "selection", "snapshot", "source_column", "line_source"):
        odds[col] = odds[col].astype("category")
    info = {
        "n_files": len(mats),
        "drops_by_file": drops,
        "price_cells_nonblank": stats.price_cells_nonblank,
        "price_cells_invalid_or_le_1": stats.price_cells_invalid,
        "ah_price_rows_dropped_no_line": stats.ah_rows_without_line,
        "odds_rows_skipped_ragged_matches": stats.odds_rows_skipped_ragged_matches,
        "odds_rows": stats.rows,
        "odds_by_file": stats.by_file,
    }
    return matches, odds, info
