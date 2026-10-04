"""Append-only experiment registry (experiments/registry.jsonl, D-006)."""

import hashlib
import json
from pathlib import Path
from typing import Any

from edgeforge.config import PROJECT_ROOT
from edgeforge.provenance import provenance

REGISTRY = PROJECT_ROOT / "experiments" / "registry.jsonl"
STATUSES = ("promoted", "rejected", "baseline")


def make_record(
    experiment_id: str,
    cfg: dict[str, Any],
    data_version: str,
    command: str,
    feature_set: str,
    model: str,
    hyperparameters: dict[str, Any],
    validation_window: str,
    metrics: dict[str, Any],
    calibration: dict[str, Any],
    notes: str,
    status: str,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    return {
        "experiment_id": experiment_id,
        "dataset_version": data_version,
        "feature_set": feature_set,
        "model": model,
        "hyperparameters": hyperparameters,
        "validation_window": validation_window,
        "metrics": metrics,
        "calibration": calibration,
        "notes": notes,
        "status": status,
        **provenance(command, cfg, data_version),
    }


def _digest(rec: dict[str, Any]) -> str:
    core = {k: rec[k] for k in ("experiment_id", "metrics", "hyperparameters", "validation_window")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


def append_records(records: list[dict[str, Any]], path: Path = REGISTRY) -> int:
    """Append records; a record identical in id, metrics, hyper-parameters and window is skipped
    so that a rerun with unchanged results does not duplicate lines. Returns lines appended."""
    existing: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing.add(_digest(json.loads(line)))
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            d = _digest(rec)
            if d in existing:
                continue
            fh.write(json.dumps(rec, default=str, sort_keys=True) + "\n")
            existing.add(d)
            n += 1
    return n
