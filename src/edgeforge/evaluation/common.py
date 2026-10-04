"""Shared helpers for baseline runs."""

from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from numpy.typing import NDArray

from edgeforge.config import PROJECT_ROOT, resolve_path
from edgeforge.evaluation import metrics as M
from edgeforge.features.pit import fd_clock
from edgeforge.provenance import dir_digest

FIGURES_DIR = PROJECT_ROOT / "artifacts" / "figures"
FloatArray = NDArray[np.float64]


def warehouse_version(cfg: dict[str, Any]) -> str:
    return dir_digest(sorted(resolve_path(cfg, "processed_dir").glob("*.parquet")))


def connect(cfg: dict[str, Any]) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(resolve_path(cfg, "warehouse")), read_only=True)


def load_fd_clock(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = con.execute(
        "SELECT match_id, league, season, season_start_year, date, kickoff_local, home, away,"
        " fthg, ftag FROM matches WHERE fthg IS NOT NULL AND ftag IS NOT NULL"
    ).df()
    return fd_clock(df)


def class_metrics(p: FloatArray, y: NDArray[np.int64]) -> dict[str, float]:
    return {
        "n": int(len(y)),
        "log_loss": float(M.log_loss_multiclass(p, y).mean()),
        "brier": float(M.brier_multiclass(p, y).mean()),
        "rps": float(M.rps(p, y).mean()),
        "ece": M.multiclass_ece(p, y),
    }


def binary_metrics(p: FloatArray, y: FloatArray) -> dict[str, float]:
    return {
        "n": int(len(y)),
        "log_loss": float(M.log_loss_binary(p, y).mean()),
        "brier": float(M.brier_binary(p, y).mean()),
        "ece": M.ece(p, y),
        "mean_pred": float(p.mean()),
        "obs_freq": float(y.mean()),
    }


def _clean(obj: Any) -> Any:
    """Replace non-finite floats with None so the output is strict JSON (empty segments)."""
    import math

    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.floating):
        return _clean(float(obj))
    return obj


def write_json(path: Path, obj: Any) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_clean(obj), indent=1, default=str, allow_nan=False), encoding="utf-8"
    )
