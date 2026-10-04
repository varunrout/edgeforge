# EdgeForge

Football sportsbook pricing engine: the lifecycle of a football price across three information
states (opening, lineups, in-play). Work in progress; see `docs/PLAN.md`. No results are reported
until they are produced by code into `artifacts/metrics/`.

```
uv sync
uv run edgeforge --help
```

## Data credits

- Match results and bookmaker odds: [football-data.co.uk](https://www.football-data.co.uk). Raw files are not redistributed.
- Player, lineup and event data: StatsBomb Open Data, provided under the StatsBomb Public Data User Agreement. Raw data is not redistributed. The StatsBomb logo is required wherever analysis is published and is still to be added (see `docs/DATA.md` section 3).
