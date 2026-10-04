"""Config loading. All paths and seeds come from configs/*.yaml."""

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "configs"


def load_config(name: str = "data", config_dir: Path | None = None) -> dict[str, Any]:
    path = (config_dir or CONFIG_DIR) / f"{name}.yaml"
    with path.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict):
        raise ValueError(f"{path} must contain a mapping")
    return cfg


def resolve_path(cfg: dict[str, Any], key: str, root: Path | None = None) -> Path:
    """Resolve a path from cfg['paths'][key] relative to the project root."""
    return (root or PROJECT_ROOT) / Path(cfg["paths"][key])
