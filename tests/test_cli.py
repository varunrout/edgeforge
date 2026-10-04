from typer.testing import CliRunner

from edgeforge import __version__
from edgeforge.cli import app
from edgeforge.config import load_config

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_loads_and_has_seed() -> None:
    cfg = load_config("data")
    assert isinstance(cfg["seed"], int)
    assert cfg["http"]["min_interval_s"] >= 1.0
