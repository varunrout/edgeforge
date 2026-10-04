"""Provenance fields required on every metrics JSON (CLAUDE.md evidence rule)."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from edgeforge.config import PROJECT_ROOT


def git_sha(root: Path = PROJECT_ROOT) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        )
        sha = out.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "src", "configs", "pyproject.toml"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def config_hash(cfg: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


def dir_digest(paths: list[Path]) -> str:
    """Content digest of a set of files (used as data_version)."""
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()[:16]


def provenance(command: str, cfg: dict[str, Any], data_version: str) -> dict[str, Any]:
    return {
        "git_sha": git_sha(),
        "data_version": data_version,
        "command": command,
        "config_hash": config_hash(cfg),
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": cfg["seed"],
    }
