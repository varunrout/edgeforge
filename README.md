# EdgeForge

> **Work in progress. All numbers in this repository are interim until the final audit (Phase 10).**
> Nothing here is a claim of commercial use; this is an independent, non-commercial portfolio project.

Football sportsbook pricing engine: the lifecycle of a football price across three information
states (opening, lineups, in-play). See `docs/PLAN.md` for the plan and `docs/GATES.md` for the
audited phase reports. No result is reported unless it was produced by code into
`artifacts/metrics/` with provenance (git SHA, data version, command, config hash, seed).

```
uv sync
uv run edgeforge --help
```

## Data credits

- Match results and bookmaker odds: [football-data.co.uk](https://www.football-data.co.uk). Raw files are not redistributed.
- Player, lineup and event data: **StatsBomb Open Data**, used under the
  [StatsBomb Public Data User Agreement](https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf).
  Analysis in this repository is based on StatsBomb data. Raw data, event-level tables and player
  names are not redistributed or committed; metrics files identify players by StatsBomb id only.

<!-- TODO(owner): StatsBomb requires its logo wherever analysis is published (user agreement 1.4).
     Add the logo from the StatsBomb media pack as assets/statsbomb/logo.png and replace this
     comment with: <img src="assets/statsbomb/logo.png" alt="StatsBomb" width="160"> -->
**StatsBomb logo: placeholder, to be added by the owner** (file not yet in the repository).
