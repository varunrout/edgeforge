"""Scripted local CI (D-046): fresh clone at HEAD, locked sync, lint, types, tests.

Writes artifacts/metrics/ci_local.json (commit sha, each command, exit code, test counts,
timestamp). This replaces the GitHub Actions run, which is unavailable (account billing lock).
"""

import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from edgeforge.config import PROJECT_ROOT
from edgeforge.evaluation.common import write_json

log = logging.getLogger(__name__)
OUT = PROJECT_ROOT / "artifacts" / "metrics" / "ci_local.json"


def _uv() -> list[str]:
    """Find a working uv: the executable on PATH, else `python -m uv` from any interpreter."""
    candidates = [[p] for p in [shutil.which("uv")] if p]
    candidates += [[sys.executable, "-m", "uv"], ["python", "-m", "uv"], ["py", "-m", "uv"]]
    for cmd in candidates:
        try:
            if subprocess.run([*cmd, "--version"], capture_output=True).returncode == 0:
                return cmd
        except OSError:
            continue
    raise RuntimeError("uv not found: install it (https://docs.astral.sh/uv/)")


def _git(*args: str, cwd: Path = PROJECT_ROOT) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _counts(output: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for n, word in re.findall(
        r"(\d+) (passed|failed|skipped|error|errors|xfailed|warnings?)", output
    ):
        out[word.rstrip("s") if word.startswith("warning") else word] = int(n)
    return out


def run_ci_local(clone_dir: Path | None = None) -> Path:
    sha = _git("rev-parse", "HEAD")
    dirty = _git("status", "--porcelain", "--", "src", "tests", "configs", "pyproject.toml")
    if dirty:
        log.warning("working tree has uncommitted code changes; the check tests HEAD only")
    base = clone_dir or Path(tempfile.gettempdir()) / "efc"
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    subprocess.run(["git", "clone", "-q", str(PROJECT_ROOT), str(base)], check=True)
    uv = _uv()
    steps: list[tuple[str, list[str]]] = [
        ("uv sync --locked", [*uv, "sync", "--locked"]),
        ("uv run ruff check .", [*uv, "run", "ruff", "check", "."]),
        ("uv run ruff format --check .", [*uv, "run", "ruff", "format", "--check", "."]),
        ("uv run mypy", [*uv, "run", "mypy"]),
        ("uv run pytest -q", [*uv, "run", "pytest", "-q"]),
    ]
    results: list[dict[str, Any]] = []
    for name, cmd in steps:
        t0 = time.time()
        p = subprocess.run(cmd, cwd=base, capture_output=True, text=True)
        text = p.stdout + p.stderr
        rec: dict[str, Any] = {
            "command": name,
            "exit_code": p.returncode,
            "seconds": round(time.time() - t0, 1),
        }
        if name.endswith("pytest -q"):
            rec["test_counts"] = _counts(text.strip().splitlines()[-1] if text.strip() else "")
        results.append(rec)
        log.info("%s -> exit %d", name, p.returncode)
        if p.returncode != 0:
            rec["output_tail"] = text[-1500:]
            break
    payload = {
        "commit_sha": sha,
        "tested_tree": "fresh git clone at HEAD",
        "uncommitted_code_changes_ignored": bool(dirty),
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "all_passed": len(results) == len(steps) and all(r["exit_code"] == 0 for r in results),
        "steps": results,
        "note": "Replaces the GitHub Actions run (D-046): the account is billing-locked.",
    }
    write_json(OUT, payload)
    shutil.rmtree(base, ignore_errors=True)
    if not payload["all_passed"]:
        raise SystemExit("ci-local failed: see " + str(OUT))
    return OUT
