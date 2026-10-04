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


---

## Gate 1: Ingestion and warehouse

**Report date:** 2026-10-04. Code commit for all regenerated metrics: `1095de4`. **Implementer verdict: all Gate 1 criteria pass** (`gate1_pass: true` in `artifacts/metrics/gate1_validation.json`). Phase 2 not started.

### What was built
- `fetch-statsbomb-all`: resumable download of events and lineups for the 1,517 in-scope matches (D-026). The fetcher now retries 429/5xx/connection errors with backoff and caches only HTTP 200 (previously it would have cached an error response permanently).
- `data profile-statsbomb`: full-corpus distinct values and dismissal statistics; stops if a shot outcome is unclassified under D-030.
- Tables (Parquet + DuckDB, git-ignored): `matches`, `odds` (long format, by column name), `team_name_map` (committed CSV, names only), `sb_matches`, `sb_match_link`, `player_match`, `shots`, `events_timeline`, `sb_match_checks`, `team_season`. Described in `docs/DATA.md` section 5.
- `build-warehouse` and `validate` commands; modules for the period-aware clock, dismissal cap, D-030 classification, date parsing; 29 tests on synthetic fixtures.

### Gate criteria
| # | Criterion | Result | Proof command | Observed (from `gate1_validation.json` unless stated) |
|---|---|---|---|---|
| 1 | All 1,517 matches downloaded (events + lineups), resumable, progress logged, time and bytes reported | **PASS** | `uv run edgeforge data fetch-statsbomb-all` -> `statsbomb_download.json` | 3,034 files, 4.60 GB, 0 failures, 4 retries; see DATA.md 5.1 for the time caveat |
| 2 | Every shot outcome classified under D-030 | **PASS** | `uv run edgeforge data profile-statsbomb` | 8 outcomes, 0 unclassified (`Saved Off Target` 145 treated as off target per D-030) |
| 3 | Dismissals counted and positions-closure rate measured on the full corpus | **PASS** | same -> `statsbomb_profile.json` | 413 dismissals (196 `Red Card`, 217 `Second Yellow`); positions closed at the card for 2 (D-031) |
| 4 | Row counts per league-season and per source | **PASS** | `uv run edgeforge validate` (`row_counts`) | matches 36,539; odds 1,879,580; sb_matches 1,517; player_match 57,665; shots 37,888; events_timeline 4,410; team_season 1,962; per league-season counts in the JSON |
| 5 | Odds-to-match join coverage >= 99%, unmatched listed | **PASS** | `validate` (`odds_rows_join_to_match`, `matches_have_1x2_odds_in_scope`) | odds rows with a match: 1,879,580 of 1,879,580; in-scope matches with a 1X2 row: 29,179 of 29,179 (early snapshot 99.997%, closing 63.5% because closing columns do not exist before 2012/13); 0 league-seasons below 99% |
| 6 | StatsBomb <-> football-data link: 1,517 linked, 3 Ligue 1 gaps named | **PASS** | `validate` (`statsbomb_football_data_link`) | 1,517 linked; unlinked football-data matches: Bastia v Ajaccio GFCO (2015-11-22), St Etienne v Paris SG (2016-01-31), Troyes v Bordeaux (2016-04-30) |
| 7 | Goal reconciliation: StatsBomb goals (shots + own goals) == StatsBomb score == football-data score, >= 99% | **PASS** | `validate` (`goal_reconciliation`) | 1,517 of 1,517 (100%); the goal timeline's final running score equals the final score in all of them |
| 8 | Player invariants: goals <= SoT <= shots, penalties consistent, off-pitch players record nothing | **PASS** | `validate` (`player_match_invariants`) | 0 violations on 57,665 player-matches; 0 shots by players outside the lineup; player shots sum equals shot events in every match |
| 9 | Minutes sanity | **PASS** | `validate` (`minutes_not_above_match_length`, `minutes_team_consistency`) | 0 players above match length; 3,034 team-matches: sum of minutes equals 11 x length minus vacancy within 2 s in all, 11 starters in all; 24,299 never-exiting starters play the full match (less temporary absences), 0 exceptions |
| 10 | Dismissal timeline count vs football-data HR/AR, mismatches listed | **PASS (informational)** | `validate` (`dismissals_vs_football_data`) | 1,514 of 1,517 agree (99.8%); mismatches: Chelsea v Newcastle 2016-02-13 (StatsBomb 0, football-data HR 1), Sassuolo v Bologna 2016-01-24 (0 vs 1), Crystal Palace v Watford 2016-02-13 (StatsBomb home 1 vs football-data 0). Totals: StatsBomb 413, football-data 414 |
| 11 | Early-vs-close 1X2 overround between 1.00 and 1.20, outliers listed | **PASS** | `validate` (`overround_1x2`) | 388,119 bookmaker snapshots; 13 outside [1.00, 1.20] (0.003%), all listed (one `Avg` closing snapshot at 0.929, Mallorca v Barcelona 2025-08-16; implied sums among the 13 range from 0.929 to 1.584; most are legacy bookmakers on single matches). Max-of-bookmakers aggregates excluded: 16,511 of 44,322 are below 1.00, as expected for a maximum |
| 12 | No duplicate keys in any table | **PASS** | `validate` (`no_duplicate_keys`) | 0 for all 10 tables plus both uniqueness constraints on the link and the name map |
| 13 | AH line-consistency check from DATA.md | **PASS** | `validate` (`ah_line_consistency`) | Pinnacle vs Bet365 and vs market average at the shared line: mean abs difference in de-vigged home-cover probability 0.0038 to 0.0059 across early/close; share above 0.10 at most 0.32%; the sign of the line agrees with the Pinnacle 1X2 favourite for 100.0% (early) and 99.99% (close) of 10,210 and 10,301 matches. No evidence of a line mismatch |
| 14 | Tests; CI-equivalent checks | **PASS (local)** | `uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q` in a fresh `git clone` of the repository at the Gate 1 commit | 29 tests pass, ruff check and format clean, mypy clean on 31 files (all run in that clone). The workflow has still not run on GitHub (no remote) |
| 15 | team_name_map reviewed and committed; team_season built | **PASS** | `validate` (`team_name_map_complete`, `team_season_summary`) | 80 of 80 teams mapped, reviewed (support 23 to 36 matches per team); promoted counts are within 2 to 4 in every league-season where derivable (no exceptions; 2 or 3 in the seasons inspected: 2015/16, 2019/20, 2024/25, 2025/26); manager-change flag for 80 teams (38 with a change), unavailable elsewhere |

### Deviations and decisions for the lead
1. **D-031 (new, Proposed):** minutes, substitution times and the starter flag come from the event stream, not lineup `positions`, because positions give minutes above match length (7 player-matches), disagree with events for 40 player-matches, mis-flag starters in 22, and almost never close at a dismissal. The clock and the cap are unchanged. This supersedes only the minutes source in the locked D-030.
2. **Invariant redefined.** My first invariant counted cards against players who never appeared. 10 unused bench players were carded (4 red, 6 yellow), which is real; the invariant now covers shots and minutes only, and the cards are reported separately. Their dismissals are included in the 413 and are not on-pitch dismissals.
3. **Implementer-chosen thresholds, for the lead to confirm or replace:** overround outliers < 1% outside [1.00, 1.20]; AH line test (< 1% of matches differing by more than 0.10 in de-vigged probability, line-sign agreement >= 95%); team minutes identity tolerance 2 s. PLAN fixed only the 99% criteria.
4. **D-028 hygiene.** `data_audit_statsbomb.json` (committed in Phase 0b) contained per-player rows for 7 matches; the audit no longer writes them and is limited to the 7 named sample matches (`audit_sample_match_ids`). The earlier version remains in git history. `gate1_validation.json` and `statsbomb_profile.json` list a small number of player names in failure and example lists (tens of rows, for audit); no table is committed.
5. **Provenance.** `statsbomb_download.json` records `git_sha ...-dirty` (code of commit `529ab82`; uncommitted Phase 1 work was in the tree). The run cannot be repeated without redownloading; every other metrics file was regenerated from commit `1095de4` on a clean tree.
6. The download's wall-clock time in the metrics includes a machine suspension (DATA.md 5.1).

### Open issues
- Single-season player data (2015/16) remains the main validity limit for Pillars B and C (D-022).
- 22 player-matches have a lineup-positions starter flag that disagrees with the `Starting XI` event; the event is used (D-031).
- Football-data odds timing is still unknown (D-024).
- CI has not run on GitHub (no remote; pushing needs Varun's say-so).

### Lead audit of Gate 1 (2026-10-04): PASSED
- All 15 criteria accepted. 100% goal reconciliation, zero invariant violations and the event-versus-positions finding (D-031) are exactly the standard wanted.
- Locked: D-031. New: D-032 (thresholds), D-033 (validation design per data block), D-034 (scrub player rows from history before first push).
- Still open: GitHub CI has never run. It is a hard criterion at Gate 2.
- Phase 2 may start.
