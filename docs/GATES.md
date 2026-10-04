# Gate Reports

Appended by Claude Code at the end of each phase.

---

## Gate 0: Setup and data verification

**Report date:** 2026-10-04. **Verdict by implementer: NOT MET. Blocked on Understat (D-021). Stopped for owner and lead.**

### What was built
- Repo skeleton per PLAN Phase 0, `pyproject.toml` (uv, ruff, mypy strict, pytest), typer CLI (`edgeforge version`, `data fetch-footballdata-proof`, `data fetch-footballdata`, `data audit-footballdata`), `.gitignore`, `.env.example`, `configs/data.yaml`.
- `edgeforge.data.http.CachedFetcher`: 1 req/s throttle, every response cached on disk, cache hits cost no request.
- `edgeforge.data.footballdata` downloader and `edgeforge.data.audit` (reads only cached payloads; writes `artifacts/metrics/data_audit_footballdata.json` with `git_sha`, `data_version`, `command`, `config_hash`, `created_at`, `seed`).
- GitHub Actions `ci.yml` (`uv sync --locked`, ruff check, ruff format --check, mypy, pytest); tests use synthetic data only.
- `docs/DATA.md` (football-data verified; Understat explicitly marked not verified), D-019, D-020, D-021 in `docs/DECISIONS.md`.
- No Understat scraper was written: it would be unusable without resolving D-021.

### Gate criteria
| # | Criterion | Result | Proof command |
|---|---|---|---|
| 1 | CLI runs | **PASS** | `uv run edgeforge --help` and `uv run edgeforge version` (prints `0.1.0`) |
| 2 | CI runs | **PARTIAL** | The workflow file exists and every command it runs passes in a fresh `git clone` of the repo: `uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q` (7 tests pass, mypy clean on 19 source files). **It has not run on GitHub**: there is no remote and pushing needs Varun's say-so. |
| 3a | One real league-season from football-data cached | **PASS** | `uv run edgeforge data fetch-footballdata-proof` (E0 2024/25, 197,110 bytes, 380 matches, HTTP 200); `git check-ignore -v data/raw/footballdata/E0_2425.*.body` confirms it is ignored |
| 3b | One real league-season from Understat cached | **FAIL (blocked)** | `curl -s https://understat.com/robots.txt` returns `User-agent: *` / `Disallow: /`. No data request was made. |
| 4a | DATA.md verified against real payloads: football-data | **PASS** | `uv run edgeforge data audit-footballdata`; every number in DATA.md section 2 traces to `artifacts/metrics/data_audit_footballdata.json` |
| 4b | DATA.md verified: Understat (starter flag, minutes, sub minute, shot minutes, own goals, card flags, red-card minute) | **FAIL (not verifiable)** | All marked NOT VERIFIED; no payload exists |
| 5 | Season range logged as a decision | **PARTIAL** | D-019, status Proposed: football-data side verified; final range conditional on player data |
| 6 | Red-card data route logged as a decision | **PARTIAL** | D-020, status Open: football-data has red-card counts only (`HR`/`AR`), no minutes; Understat route undetermined |

### Findings the lead should know
1. **Understat `robots.txt` disallows everything** (D-021). Owner decision required before any player-level data work.
2. **football-data `robots.txt`** allows `*` but names `Anthropic-AI`, `Claude-Web`, `ClaudeBot` and other AI crawlers as disallowed. The project downloader uses its own user agent. This reading is flagged in D-021 for owner veto.
3. **"Opening" odds are not verified as opening.** The non-`C` columns are a pre-closing snapshot with no capture timestamp. Pillar A and the `opening` state need this label (D-019).
4. **Pinnacle vanishes mid-2025/26** (last Pinnacle match 2026-01-08 in E0; none in 2026/27). Cause unknown.
5. Pinnacle O/U 2.5 and AH open+close exist only from 2019/20 (E0). 1X2 open+close from 2012/13.
6. Asian-handicap line columns (`AHh`, `AHCh`) are single columns shared across bookmakers; that Pinnacle's prices are at that line is not verified.
7. Ragged rows in E0 1993/94, 1994/95, 2003/04, 2004/05 (outside the recommended range).

### Deviations from plan
- Fetched 62 football-data CSVs rather than one league-season, because the opening/closing and season-start questions cannot be answered from one file (DATA.md section 2). About 7.9 MB, throttled at 1 req/s, cached and git-ignored.
- Process bugs found and fixed during the phase: an unanchored `.gitignore` rule `data/` excluded the `src/edgeforge/data` package from the first commit, and `ruff` skipped that package while it was ignored. Fixed in commits `cb6f25a` and `ab0dcd5`; the fresh-clone check above ran after both fixes.

### Open issues
- D-021 (owner): how to proceed on player-level data.
- CI has not run on GitHub (no remote).
- Phase 1 must not start until the lead passes this gate.

---

## Gate 0 (re-run, Phase 0b: data route switch)

**Report date:** 2026-10-04. Code state: commit `cb3881c` (metrics provenance), docs commit after it. **Implementer verdict: criteria met, with open items below for the lead.** Phase 1 not started.

### What was built or changed in Phase 0b
- Repo hygiene: `.gitattributes` (`* text=auto eol=lf`) added and renormalised (renormalisation changed nothing else). The prompt expected 6 modified files of line-ending noise; at the start only `docs/DECISIONS.md` was modified, and `git diff --ignore-all-space --stat` showed the same 25 insertions and 2 deletions, i.e. real content (D-022 to D-025), which was committed as content with D-022 and D-023 set to Locked (owner, 2026-10-04).
- Understat dropped. No references in code, configs or tests; `docs/PLAN.md` Phase 0/1 lines updated; `docs/DATA.md` section 1 is a pointer to D-021/D-022. Understat was never requested beyond `robots.txt`.
- StatsBomb fetcher (`edgeforge.data.statsbomb`), payload and scope audit (`edgeforge.data.statsbomb_audit`, CLI `data audit-statsbomb`, output `artifacts/metrics/data_audit_statsbomb.json`), cached-fetcher elapsed-time logging, 4 new tests (11 total, synthetic fixtures only).
- `docs/DATA.md` rebuilt; D-019 and D-020 statuses updated; D-026 (retrieval method) and D-027 (history range) added.

### Gate criteria
| # | Criterion | Result | Proof command |
|---|---|---|---|
| 1 | CLI runs | **PASS** | `uv run edgeforge --help` (exit 0), `uv run edgeforge version` (`0.1.0`), in a fresh clone |
| 2 | CI-equivalent checks pass | **PASS (local); GitHub run not done** | In a fresh `git clone`: `uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q` -> all pass, 11 tests, mypy clean on 21 files. No remote exists, so the workflow itself has not run on GitHub. |
| 3 | StatsBomb licence read and recorded; use not forbidden | **PASS, with flagged risks** | `data/raw/statsbomb/LICENSE.pdf` read in full; terms in `docs/DATA.md` section 3. Risks: public deployment (clauses 1.2.1, 1.2.2), logo attribution (1.4), registration request (2.2). |
| 4 | Scope counts confirmed from `matches/<comp>/<season>.json` | **PASS** | `uv run edgeforge data fetch-statsbomb-scope` and `uv run edgeforge data audit-statsbomb`: Premier League 380, La Liga 380, Serie A 380, Ligue 1 377 = 1,517; Bundesliga 34 (excluded). |
| 5 | Payload facts for events + lineups, from real files | **PASS (7 matches)** | `uv run edgeforge data fetch-statsbomb-match 3754217` then `uv run edgeforge data audit-statsbomb`; every row of the `docs/DATA.md` section 3 table traces to `sample_matches` in the JSON. Sample is 7 matches (5 PL, 1 La Liga, 1 Ligue 1), not random: the prompt asked for one match, but one match cannot show every event type (3754217 has an own goal, both dismissal types and subs; penalty and post needed 3754237). |
| 6 | Red-card route stated explicitly | **PASS (provisional)** | D-020: minute read directly from card events (also in lineups `cards[]`); positions/tactics are unreliable for a `Red Card` (derived 99.3 min for a player sent off at 45:44), correct for a `Second Yellow`. Based on two dismissals in one match. |
| 7 | Download size, time and retrieval method logged | **PASS** | `data_audit_statsbomb.json` `download_estimate`: 4,568,675,058 B events + 29,133,963 B lineups, 3,034 requests, 50.6 min throttle floor, about 1 h modelled; D-026 chooses per-file cached fetch. Time rests on 3 timed samples. |
| 8 | football-data 2015/16 coverage for E0, SP1, I1, F1 | **PASS** | `uv run edgeforge data audit-footballdata`: Pinnacle 1X2 early and close present in all four (SP1 early 379/380); Pinnacle O/U and AH absent, only Betbrain aggregates with no closing version. |
| 9 | History range for the team model chosen, with request count | **PASS** | D-027: 2005/06 to 2014/15, 4 leagues, 40 CSVs (30 requested now plus 10 cached E0), all 380 rows, 0 ragged. |
| 10 | DATA.md updated; D-019, D-020 statuses; team-name mapping problem flagged | **PASS** | `docs/DATA.md` sections 3 and 4; `team_names_2015_16` in the StatsBomb audit JSON: 9 to 16 of 20 names identical per league; (date, score) join recovers a complete, conflict-free 20/20 map in every league. |

### Open items and caveats for the lead
1. **Payload facts come from 7 matches.** Starter/bench/cards/shot-outcome vocabularies may be larger across 1,517 matches (for example `Saved Off Target`, other card reasons were not seen). Phase 1 must profile all of them before relying on these enumerations.
2. **Lineups are a post-match record.** That they equal the T-60min announced XI and bench is NOT VERIFIED. Matters for the `lineups` state and Pillar C.
3. **Red-card evidence is thin** (two dismissals, one match). The Phase 1 check over all matches is part of the route (D-020).
4. **Licence risk for Phase 9 (public deployment).** Needs a lead/owner decision before the Cloud Run image is designed; see DATA.md section 3.
5. **Owner actions:** register at `www.statsbomb.com/resource-centre` (requested by the agreement, clause 2.2); obtain the StatsBomb logo from their Media Pack for the README, reports and app (clause 1.4). I did not download the logo.
6. **Disk:** 24 GB free (96% used) before a 4.6 GB download.
7. **Minute clock:** `minute` is not monotonic across periods (restarts at 45 in period 2); in-play state must use `(period, minute, second)`.
8. **Pre-2019/20 odds:** the 2015/16 market anchor for totals is a Betbrain average with no closing price; AH has no usable anchor for 2015/16.
9. CI has not run on GitHub (no remote; pushing needs Varun's say-so).
10. D-019, D-020, D-026, D-027 are still "Proposed"; the lead should lock or revise.

### Requests made in Phase 0b
football-data: 33 new CSV requests (30 history + 3 for 2015/16; E0 files were cached). StatsBomb via the project fetcher: 21 cached responses (`competitions.json`, 5 `matches` files, events and lineups for 7 matches, the GitHub tree listing). Plus 4 direct reads outside the fetcher: README, two repo directory listings (GitHub API) and `LICENSE.pdf`. No Understat requests.

### Deviations from plan
- Fetched events and lineups for 7 matches rather than 1 (reason in criterion 5).
- Added a `.gitattributes` commit and a DECISIONS content commit before the planned work, because the working-tree state differed from the prompt's description (see first bullet above).

### Lead audit of Gate 0 (2026-10-04): PASSED
- All ten criteria accepted. GitHub CI run deferred until the owner creates a remote; it becomes a hard criterion at Gate 2.
- Locked: D-019, D-020, D-026, D-027. New: D-028 (licence and publishing), D-029 (confirmed-lineup assumption), D-030 (prop settlement conventions).
- Carried into Phase 1 as hard checks: full-corpus profiling of every enumeration (shot outcomes, card types, start/end reasons), dismissal-minute check across all 1,517 matches, SoT classification per D-030, minutes capped at dismissals, team-name map reviewed.
- Phase 1 may start.
