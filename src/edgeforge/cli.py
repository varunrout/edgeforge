"""EdgeForge command line interface. Phase 0 exposes only data-access stubs."""

import typer

from edgeforge import __version__
from edgeforge.logging_setup import configure_logging

app = typer.Typer(help="EdgeForge: football sportsbook pricing engine.", no_args_is_help=True)
data_app = typer.Typer(help="Data acquisition.", no_args_is_help=True)
app.add_typer(data_app, name="data")


@app.callback()
def main() -> None:
    configure_logging()


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@data_app.command("fetch-footballdata-proof")
def fetch_footballdata_proof() -> None:
    """Phase 0: cache one football-data.co.uk league-season (see configs/data.yaml)."""
    from edgeforge.data.footballdata import fetch_proof

    path = fetch_proof()
    typer.echo(str(path))


@data_app.command("audit-footballdata")
def audit_footballdata() -> None:
    """Audit cached football-data CSVs and write artifacts/metrics/data_audit_footballdata.json."""
    from edgeforge.data.audit import run_audit

    typer.echo(str(run_audit()))


@data_app.command("fetch-footballdata")
def fetch_footballdata(
    leagues: str = typer.Argument(..., help="Comma-separated league codes, e.g. E0,D1"),
    seasons: str = typer.Argument(..., help="Comma-separated season codes, e.g. 2324,2425"),
) -> None:
    """Fetch and cache football-data league-seasons (1 request/second, cached)."""
    from edgeforge.config import load_config
    from edgeforge.data.footballdata import fetch_league_season, make_fetcher

    cfg = load_config("data")
    fetcher = make_fetcher(cfg)
    base = cfg["footballdata"]["base_url"]
    for lg in leagues.split(","):
        for season in seasons.split(","):
            res = fetch_league_season(fetcher, base, season, lg)
            typer.echo(f"{lg} {season} HTTP {res.status} cache={res.from_cache}")
