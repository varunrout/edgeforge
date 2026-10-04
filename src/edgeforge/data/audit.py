"""Audit of cached football-data.co.uk CSVs: which odds columns exist and how populated they are.

Reads only what is in the cached payloads; nothing is inferred from documentation.
"""

import csv
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from edgeforge.config import load_config, resolve_path
from edgeforge.provenance import dir_digest, provenance

log = logging.getLogger(__name__)

# Columns whose population is counted per file (Pinnacle first, then market aggregates).
KEY_COLUMNS: dict[str, list[str]] = {
    "1x2_open_pinnacle": ["PSH", "PSD", "PSA"],
    "1x2_close_pinnacle": ["PSCH", "PSCD", "PSCA"],
    "ou25_open_pinnacle": ["P>2.5", "P<2.5"],
    "ou25_close_pinnacle": ["PC>2.5", "PC<2.5"],
    "ah_open_pinnacle": ["AHh", "PAHH", "PAHA"],
    "ah_close_pinnacle": ["AHCh", "PCAHH", "PCAHA"],
    "1x2_close_market_avg": ["AvgCH", "AvgCD", "AvgCA"],
    "ou25_close_market_avg": ["AvgC>2.5", "AvgC<2.5"],
    "ah_close_market_avg": ["AvgCAHH", "AvgCAHA"],
    "red_cards": ["HR", "AR"],
}


def _read(path: Path) -> tuple[list[str], list[list[str]], int]:
    """Return (header, match rows truncated to header width, ragged row count)."""
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    reader = csv.reader(text.splitlines())
    header = next(reader)
    width = len(header)
    ih = header.index("HomeTeam")
    rows: list[list[str]] = []
    ragged = 0
    for r in reader:
        if len(r) <= ih or not r[ih].strip():
            continue  # blank / separator rows
        if len(r) != width:
            ragged += 1
        r = (r + [""] * width)[:width]
        rows.append(r)
    return header, rows, ragged


def _closing_bookmakers(header: list[str]) -> list[str]:
    """Bookmaker prefixes P for which both P+'H' (opening) and P+'CH' (closing) 1X2 exist."""
    cols = set(header)
    out = []
    for c in header:
        m = re.fullmatch(r"(.+)CH", c)
        if m and (m.group(1) + "H") in cols:
            out.append(m.group(1))
    return sorted(out)


def _parse_date(raw: str) -> date | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _open_vs_close(rows: list[list[str]], idx: dict[str, int], o: str, c: str) -> dict[str, Any]:
    """Compare an opening and a closing column over rows where both are populated."""
    if o not in idx or c not in idx:
        return {"n_pairs": 0}
    pairs = []
    for r in rows:
        try:
            pairs.append((float(r[idx[o]]), float(r[idx[c]])))
        except ValueError:
            continue
    if not pairs:
        return {"n_pairs": 0}
    n = len(pairs)
    return {
        "n_pairs": n,
        "share_identical": sum(1 for a, b in pairs if a == b) / n,
        "mean_abs_diff": sum(abs(a - b) for a, b in pairs) / n,
    }


def audit_file(path: Path, league: str, season: str) -> dict[str, Any]:
    header, rows, ragged = _read(path)
    idx = {c: i for i, c in enumerate(header)}
    nn = {
        c: sum(1 for r in rows if r[idx[c]].strip() != "") if c in idx else None
        for cols in KEY_COLUMNS.values()
        for c in cols
    }
    date_i = idx["Date"]
    sample_date = rows[0][date_i] if rows else None
    ps_dates = [
        d
        for r in rows
        if "PSH" in idx and r[idx["PSH"]].strip()
        for d in [_parse_date(r[date_i])]
        if d is not None
    ]
    all_dates = [d for r in rows for d in [_parse_date(r[date_i])] if d is not None]
    return {
        "league": league,
        "season": season,
        "rows": len(rows),
        "ragged_rows": ragged,
        "n_columns": len(header),
        "has_time_column": "Time" in idx,
        "sample_date": sample_date,
        "nonnull": nn,
        "closing_bookmakers_1x2": _closing_bookmakers(header),
        "has_pinnacle_1x2_rows": len(ps_dates),
        "first_match_date": min(all_dates).isoformat() if all_dates else None,
        "last_match_date": max(all_dates).isoformat() if all_dates else None,
        "pinnacle_1x2_last_date": max(ps_dates).isoformat() if ps_dates else None,
        "pinnacle_home_open_vs_close": _open_vs_close(rows, idx, "PSH", "PSCH"),
        "columns": header,
    }


def run_audit(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    raw = resolve_path(cfg, "raw_dir") / "footballdata"
    metas = sorted(raw.glob("*.meta.json"))
    bodies = [Path(str(m).replace(".meta.json", ".body")) for m in metas]
    files = []
    for meta, body in zip(metas, bodies, strict=True):
        info = json.loads(meta.read_text())
        m = re.search(r"mmz4281/(\w+)/(\w+)\.csv", info["url"])
        if m is None or info["status"] != 200:
            continue
        files.append(audit_file(body, league=m.group(2), season=m.group(1)))
    files.sort(key=lambda f: (f["league"], f["season"]))
    out = {
        "provenance": provenance(
            "edgeforge data audit-footballdata", cfg, dir_digest(bodies)
        ),
        "key_columns": KEY_COLUMNS,
        "files": files,
    }
    out_path = resolve_path(cfg, "metrics_dir") / "data_audit_footballdata.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    log.info("wrote %s (%d files)", out_path, len(files))
    return out_path
