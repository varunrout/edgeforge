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


@data_app.command("fetch-statsbomb-scope")
def fetch_statsbomb_scope() -> None:
    """Cache competitions.json and the matches file of each in-scope competition-season."""
    from edgeforge.config import load_config
    from edgeforge.data import statsbomb

    cfg = load_config("data")
    fetcher = statsbomb.make_fetcher(cfg)
    comps = statsbomb.fetch_competitions(fetcher, cfg)
    for c in statsbomb.scope_competitions(comps, cfg):
        matches = statsbomb.fetch_matches(fetcher, cfg, c["competition_id"], c["season_id"])
        typer.echo(f"{c['competition_name']} {c['season_name']}: {len(matches)} matches")


@data_app.command("fetch-statsbomb-match")
def fetch_statsbomb_match(match_id: int) -> None:
    """Cache events and lineups for one match."""
    from edgeforge.config import load_config
    from edgeforge.data import statsbomb

    cfg = load_config("data")
    ev, lu = statsbomb.fetch_match_files(statsbomb.make_fetcher(cfg), cfg, match_id)
    typer.echo(f"{ev}\n{lu}")


@data_app.command("audit-statsbomb")
def audit_statsbomb() -> None:
    """Audit cached StatsBomb payloads and write artifacts/metrics/data_audit_statsbomb.json."""
    from edgeforge.data.statsbomb_audit import run_statsbomb_audit

    typer.echo(str(run_statsbomb_audit()))


@data_app.command("fetch-statsbomb-all")
def fetch_statsbomb_all() -> None:
    """Fetch events + lineups for all in-scope matches (resumable; ~1 hour)."""
    from edgeforge.config import load_config
    from edgeforge.data.statsbomb import download_all

    typer.echo(str(download_all(load_config("data"))))


@data_app.command("profile-statsbomb")
def profile_statsbomb() -> None:
    """Profile all cached StatsBomb payloads; stops if a shot outcome is unclassified (D-030)."""
    from edgeforge.data.sb_profile import run_profile

    typer.echo(str(run_profile()))


@data_app.command("bootstrap-team-names")
def bootstrap_team_names() -> None:
    """Write candidate football-data <-> StatsBomb name pairs to configs/team_name_map.csv."""
    from edgeforge.data.warehouse import bootstrap_team_names as run

    typer.echo(str(run()))


@app.command("build-warehouse")
def build_warehouse() -> None:
    """Build Parquet tables and the DuckDB warehouse from the raw caches."""
    from edgeforge.data.warehouse import build_warehouse as run

    typer.echo(str(run()))


@app.command("validate")
def validate() -> None:
    """Run the Gate 1 checks against the warehouse; exit 1 if any check fails."""
    import json

    from edgeforge.data.validate import run_validate

    path = run_validate()
    typer.echo(str(path))
    if not json.loads(path.read_text(encoding="utf-8"))["gate1_pass"]:
        raise typer.Exit(code=1)


@app.command("splits")
def splits() -> None:
    """Write the D-033 train/tune/test match-id sets to artifacts/splits/*.json."""
    from edgeforge.evaluation.splits import write_splits

    typer.echo(str(write_splits()))


baselines_app = typer.Typer(help="Baseline models (Phase 2).", no_args_is_help=True)
app.add_typer(baselines_app, name="baselines")


@baselines_app.command("team")
def baselines_team() -> None:
    """League average, static Poisson and de-vigged market baselines."""
    from edgeforge.evaluation.baselines_team import run_team_baselines

    typer.echo(str(run_team_baselines()))


@baselines_app.command("player")
def baselines_player() -> None:
    """Player rolling per-90 x expected minutes prop baseline."""
    from edgeforge.evaluation.baselines_player import run_player_baselines

    typer.echo(str(run_player_baselines()))


@baselines_app.command("inplay")
def baselines_inplay() -> None:
    """Naive time-scaled in-play baseline (needs the team baseline run first)."""
    from edgeforge.evaluation.baselines_inplay import run_inplay_baseline

    typer.echo(str(run_inplay_baseline()))


team_app = typer.Typer(help="Phase 3 team model and Pillar A.", no_args_is_help=True)
app.add_typer(team_app, name="team-model")


@team_app.command("run")
def team_model_run() -> None:
    """Tune and evaluate the Dixon-Coles team model; calibration and cold-start studies."""
    from edgeforge.evaluation.team_model import run_team_model

    typer.echo(str(run_team_model()))


pillar_app = typer.Typer(
    help="Pillar A: market efficiency and model vs market.", no_args_is_help=True
)
app.add_typer(pillar_app, name="pillar-a")


@pillar_app.command("run")
def pillar_a_run() -> None:
    """Efficiency map (D-037), model vs market, encompassing, line movement, cold start."""
    from edgeforge.evaluation.pillar_a import run_pillar_a

    typer.echo(str(run_pillar_a()))


phase4_app = typer.Typer(help="Phase 4: participation and player models.", no_args_is_help=True)
app.add_typer(phase4_app, name="phase4")


@phase4_app.command("run")
def phase4_run() -> None:
    """Starter, minutes, team-shot and player-market models with evaluation and figures."""
    from edgeforge.evaluation.phase4_run import run_phase4

    typer.echo(str(run_phase4()))


@app.command("ci-local")
def ci_local() -> None:
    """Fresh-clone lint, type and test check; writes artifacts/metrics/ci_local.json (D-046)."""
    from edgeforge.ci_local import run_ci_local

    typer.echo(str(run_ci_local()))
