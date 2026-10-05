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

---

## Gate 2: Evaluation harness, baselines, leakage tests

**Report date:** 2026-10-04. Code commit for all Phase 2 metrics: `0e54107`. **Implementer verdict: all Gate 2 criteria pass except the GitHub CI confirmation, which is blocked by an account billing lock (item 11).** Phase 3 not started. No real models were built.

### Step 0 (D-034) and push
- History rewritten with `git filter-repo --path artifacts/metrics/data_audit_statsbomb.json --invert-paths`, then the clean version re-added in a new commit. Before the rewrite five commits contained `"players"` rows in that file; after it, a search of every file under `artifacts/` in every commit finds the key `"players"` only in `gate1_validation.json`, where it is a count (`{"players": 10, ...}` for unused bench players), not rows. All commit hashes changed.
- Remote `origin` = `https://github.com/varunrout/edgeforge.git` added and `main` pushed (the repository was empty and public).
- **GitHub Actions did not run the job.** Run `37196854779` failed in 4 seconds with the annotation "The job was not started because your account is locked due to a billing issue." No step executed, so this is not a code failure and cannot be fixed in the repository. Owner action: resolve the GitHub billing/payment lock, then re-run the workflow.

### What was built
- `edgeforge splits` -> `artifacts/splits/{pillar_a,team_2015_16,player_2015_16}.json` (ids only).
- Point-in-time feature builder (`edgeforge.features.pit`): SQL on DuckDB, every row has `asof_ts` and a state tag (D-035).
- Metrics module and plots (`edgeforge.evaluation.metrics`, `plots`), de-vig (`pricing.devig`), team market grid (`pricing.markets`), static Poisson (`models.poisson_static`), experiment registry, baseline runners (`edgeforge baselines team|player|inplay`).
- 60 tests (up from 29), including leakage tests (a) to (d) and one real-data smoke test.

### Gate criteria
| # | Criterion | Result | Proving command | Observed (from metrics files unless stated) |
|---|---|---|---|---|
| 1 | Splitter implements the three D-033 blocks; id sets written | **PASS** | `uv run edgeforge splits` | Pillar A: history 2005-2018 19,306; burn-in/tune 2019/20-2023/24 8,955; test 2024/25 1,752; secondary 2025/26 1,752. Team 2015/16: history 15,200; decay tuning 2013/14-2014/15 3,040; evaluation 1,520; matchweek 20-38 test 758. Player: burn-in 360 + tune 399 + test 758 = 1,517 |
| 2 | Point-in-time feature builder with `asof_ts` and information-state tag (opening, lineups, in-play) | **PASS** | `uv run pytest tests/test_leakage.py` | lineups state carries only the announced XI and named bench (`announced_starter`); minutes, subs, cards are targets only; opening state has no lineup columns; in-play state function `inplay_state` |
| 3 | Metrics: log loss, Brier, RPS, ECE, reliability data and plots, PIT, central-interval coverage, MAE/RMSE, paired bootstrap | **PASS** | `uv run pytest tests/test_metrics_devig.py`; 11 figures in `artifacts/figures/` | bootstrap = 1,000 resamples of matches (seeded); 58 team comparisons plus 5 player comparisons carry CIs |
| 4a | Baseline: league-average frequency per market | **PASS** | `uv run edgeforge baselines team` -> `baseline_team.json` | 1X2 log loss on 2024/25: 1.0782 (RPS 0.2331); 2025/26: 1.0721; 2015/16 mw20-38: 1.0635 |
| 4b | Baseline: static Poisson (no decay) | **PASS** | same | 1X2 log loss 0.9899 (2024/25), 1.0010 (2025/26), 0.9839 (2015/16 mw20-38). vs league average on 1X2 log loss: 2024/25 -0.0883 [-0.1061, -0.0687]; 2015/16 mw20-38 -0.0795 [-0.1061, -0.0522]. On O/U 2.5 and BTTS the Poisson is **not** shown to beat league average outside 2024/25 O/U 2.5 (-0.0115 [-0.0214, -0.0016]); BTTS 2024/25 +0.0011 [-0.0068, +0.0087] |
| 4c | Baseline: de-vigged market, proportional / power / Shin | **PASS** | same | Pinnacle closing 1X2 log loss 0.9606 / 0.9601 / 0.9602 (2024/25, n=1,752); market average 0.9774 / 0.9766 / 0.9767 (2025/26, n=1,752); Pinnacle 2015/16 mw20-38 0.9579 / 0.9573 / 0.9575 (n=758). Market vs static Poisson on 2024/25 (Pinnacle, proportional): -0.0293 [-0.0385, -0.0202]. Power or Shin vs proportional on 2024/25: -0.0004 [-0.0010, +0.0003], i.e. **no demonstrable difference** between de-vig methods |
| 4d | Baseline: player rolling per-90 x expected minutes (props), choices on 2015/16 tune window only | **PASS** | `uv run edgeforge baselines player` -> `baseline_player.json` | chosen on mw10-19: all prior matches, 540 pseudo-minutes (interior of a 3 x 6 grid; mean log loss 0.3615 tune). Test mw20-38 (21,026 appeared player-matches): mean log loss 0.3629 vs position-average 0.3800. Log loss by market: shots 1+ 0.5462, shots 2+ 0.3983, SoT 1+ 0.4429, SoT 2+ 0.1727, anytime scorer 0.2545. vs position average: shots 1+ -0.0308 [-0.0339, -0.0275]; shots 2+ -0.0302 [-0.0335, -0.0270]; SoT 1+ -0.0139 [-0.0168, -0.0109]; SoT 2+ -0.0098 [-0.0120, -0.0075]; anytime scorer -0.0010 [-0.0037, +0.0013] (**no demonstrable gain** for goals). PIT histograms near uniform; 80% interval coverage 0.935 (shots), 0.956 (SoT), 0.964 (goals), over-covering as expected for discrete counts |
| 4e | Baseline: in-play naive, minutes 15/30/45/60/75 on the test window | **PASS** | `uv run edgeforge baselines inplay` -> `baseline_inplay.json` | 758 test matches; final-1X2 log loss 0.9454 / 0.9123 / 0.8058 / 0.7001 / 0.5681 at 15/30/45/60/75. The baseline **under-predicts** remaining goals at every checkpoint: P(more than 1.5 remaining) predicted vs observed 0.635 vs 0.682 (min 15), 0.397 vs 0.468 (45), 0.108 vs 0.137 (75). By score state (pooled) n = 1,736 level, 1,426 one-goal margin, 628 two-plus; after a dismissal n = 214 |
| 5 | Every baseline registered with status and provenance | **PASS** | `experiments/registry.jsonl` | 47 records: 30 `baseline`, 17 `rejected` (non-chosen player grid candidates), all with `git_sha` `0e54107...`, `data_version`, `command`, `config_hash`, `created_at`, `seed` |
| 6a | Leakage test (a): altering rows at or after `asof_ts` leaves features unchanged | **PASS** | `uv run pytest tests/test_leakage.py -k test_a` | player (both states), group rates, league and team features; includes the 3-hour completion window and a power check showing earlier rows do change features |
| 6b | Leakage test (b): minutes/subs/cards/post-kickoff stats never features in opening or lineups | **PASS** | `-k test_b` | `assert_no_forbidden` on both states; forbidden-column detection test |
| 6c | Leakage test (c): in-play state at t uses only events before t | **PASS** | `-k test_c` | an event exactly at t is excluded; rewriting all events at or after t leaves the state identical |
| 6d | Leakage test (d): no fitting/tuning code reads a test-window match_id | **PASS** | `-k test_d` and `-k real_data` | `SplitGuard` raises on test ids and on fits that read matches completing after the cutoff; the tuning stage passes its history and targets through it (reads logged); excluding test ids makes tuning features independent of them; real-data smoke test perturbs the whole test window and confirms tuning features are unchanged |
| 7 | Tests and CI-equivalent checks | **PASS (local)** | `uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q` in a fresh clone | In a fresh clone: 59 passed, 1 skipped (the real-data smoke test skips when no warehouse exists, as on CI); locally with the warehouse all 60 pass. ruff and mypy clean (43 source files). Note: the clone must live at a short path on Windows; my first attempt under a very long scratch path produced a corrupt scikit-learn install (260-character limit), unrelated to the repo |
| 8 | GitHub Actions run green | **BLOCKED** | `gh run list` | run `37196854779` not started: GitHub account billing lock (above) |
| 9 | D-034 history scrub verified and first push | **PASS** | search of all commits (above) | see Step 0 |

### Findings the lead should weigh
1. The market is the best team-market baseline on every block; static Poisson is second; league average third. Differences among proportional, power and Shin de-vig are within noise on the 2024/25 primary block, and borderline (about 0.001 log loss) on the 898-match Pinnacle subset of 2025/26.
2. Static Poisson shows no demonstrable edge over league-average frequencies for totals beyond 2024/25 O/U 2.5, or for BTTS: the bar for the Phase 3 team model on those markets is low.
3. The in-play naive baseline's remaining-goals bias is systematic (uniform scaling of a rate that rises late plus stoppage time); this is the headroom Pillar B must show it can remove.
4. Rolling per-90 beats the position average on shots and SoT but not demonstrably on anytime scorer.

### Deviations, decisions and caveats
1. **D-035 (new, Proposed)** records the completion-window eligibility rule (stricter than CLAUDE.md), weekly Monday cutoffs, fixed untuned baseline settings, settlement conventions and the in-play definition.
2. **Opening-state player baseline deferred** to Phase 4 (feature builder and leakage tests exist for it). The PLAN baseline list does not name a state; the lineups state was evaluated.
3. **Grid edge.** My first player grid chose the edge value (270 pseudo-minutes); I widened the grid before reporting so the chosen value is interior. Both grids were scored on the tune window only; the first-grid test numbers were seen in a shake-out run but did not drive the choice (the choice is the minimum of the wider tune-window grid).
4. **Guard scope.** `SplitGuard` is called explicitly by the tuning stage of the player baseline, the in-play length fit and each Poisson fit; it does not intercept arbitrary code. Test (d) therefore proves the guard and the exclusion mechanism, plus the real-data perturbation, not a static analysis of all future fitting code.
5. **D-034 residue.** The committed `data_audit_statsbomb.json` still lists scorers and the two dismissed players of the 7 sample matches (small event lists, no per-player rows); `gate1_validation.json` and `statsbomb_profile.json` list a few dozen player names in failure and example lists. The lead's D-034 text treats failure/example lists as acceptable; say if the sample-match event lists should go too.
6. Pre-existing: closing odds do not exist before 2012/13 and the football-data "early" snapshot is untimestamped (D-024); the 2015/16 totals market has no Pinnacle price, so 2.5-goal market baselines exist for Pillar A only.

### Lead audit of Gate 2 (2026-10-04): PASSED, conditional on CI
- Criteria 1 to 7 and 9 accepted. Criterion 8 (GitHub CI green) is blocked by an owner billing lock and becomes a hard criterion at Gate 3: Phase 3 may proceed, Gate 3 cannot pass without a green run.
- Grid-edge disclosure (deviation 3) accepted: the choice is the tune-window minimum of the widened grid. Keep disclosing it in the technical report.
- Locked: D-035 (opening-state player baseline mandatory at Gate 4). New: D-036 (proportional de-vig default), D-037 (pre-registered Pillar A segments with BH FDR control), D-038 (repo private until Phase 10).
- D-034 residue: the sample-match event lists may stay while the repo is private; remove them before the repo goes public.
- Phase 3 may start.

---

## Gate 3: Team model and Pillar A (market efficiency and cold start)

**Report date:** 2026-10-04. Metrics: `team_model.json` from commit `fddfc09`, `pillar_a.json` from commit `42bf956` (clean trees). **Implementer verdict: every analytical criterion is met; the hard criterion "green GitHub Actions run" is NOT met, so Gate 3 cannot pass yet.** Phase 4 not started.

### Step 0
- Lead edits to `DECISIONS.md` and `GATES.md` committed (D-036, D-037, D-038 read).
- D-038: repository set to private (`gh repo view` -> `"visibility":"PRIVATE"`).
- CI re-run once: the run now ends as `startup_failure` ("workflow file issue", 0 s, no jobs) instead of the earlier billing-lock failure. The workflow file is unchanged and valid YAML, the repo is private, and the Actions permissions API shows Actions enabled; I cannot read the account billing state with the current token scopes (and did not widen them). It most likely remains an account-level billing/plan block, but I have not been able to confirm the cause. Owner action: resolve the GitHub billing state, then `gh run rerun`.

### What was built
- Dixon-Coles with time decay, weekly refits, analytic-gradient fit (`models/dixon_coles.py`); walk-forward runner with decay and kappa tuning, comparison A, calibration study, persistence (`evaluation/team_model.py`, `edgeforge team-model run`).
- Pillar A analysis (`evaluation/pillar_a.py`, `edgeforge pillar-a run`), D-037 segments (`evaluation/segments.py`), bootstrap, batched logistic slopes and Benjamini-Hochberg (`evaluation/stats.py`).
- 75 tests (up from 60), including a null control (calibrated market gives no BH survivors), detection of a planted favourite-longshot distortion, and the encompassing isolation tests.

### Gate criteria
| # | Criterion | Result | Proving command | Observed (from metrics files) |
|---|---|---|---|---|
| 1 | Dixon-Coles with decay, per league, weekly refits, decay tuned on tuning windows only | **PASS** | `uv run edgeforge team-model run` -> `team_model.json` | half-life 365 days in both blocks, interior optimum (grids in D-039); Pillar A tuning 1X2 log loss 0.99327 vs static Poisson 0.99958 vs league average 1.07495; 2015/16 tuning 0.97901 vs 0.98530 vs 1.0655 |
| 2 | Comparison A on 1X2, O/U 0.5-4.5, BTTS with paired bootstrap CIs | **PASS** | same | 2024/25 test (n=1,752), DC minus static Poisson, 1X2 log loss -0.0046 [-0.0084, -0.0004], RPS -0.0016 [-0.0028, -0.0004]; O/U 2.5 -0.0004 [-0.0035, +0.0029]; BTTS -0.0011 [-0.0038, +0.0016]. DC minus league average: 1X2 -0.0929 [-0.1109, -0.0733]; O/U 2.5 -0.0119 [-0.0220, -0.0014]; O/U 3.5 -0.0121 [-0.0226, -0.0021]; O/U 4.5 -0.0120 [-0.0213, -0.0035]; O/U 0.5, 1.5 and BTTS not distinguishable from zero. 2025/26: DC minus static Poisson 1X2 -0.0041 [-0.0084, -0.0001]; totals and BTTS vs league average not distinguishable from zero. 2015/16 matchweeks 20-38 (n=758): DC minus static Poisson 1X2 -0.0018 [-0.0072, +0.0034] (no demonstrable gain); whole 2015/16 (n=1,520) -0.0047 [-0.0089, -0.0006] |
| 3 | Register promoted and rejected candidates | **PASS** | `experiments/registry.jsonl` | 151 records: 94 `team_model`, 10 `pillar_a`, 47 baselines; statuses promoted 27, rejected 90, baseline 34. Decay candidates, kappa candidates and every calibrator x window are individual records |
| 4 | Fit and persist the 2015/16-block team model | **PASS** | same | `data/processed/team_model_preds.parquet` (git-ignored) and committed `artifacts/models/team_dc_params.parquet` (13,901 parameter rows); kappa and half-life recorded in `team_model.json` |
| 5 | Post-hoc calibration (Platt, isotonic) on the tuning window; keep only with CI excluding zero; log either way | **PASS** | same | 1X2 calibration never helps (Platt differences range from -0.0002 to +0.0011 across the four windows, none with a CI below zero); adopted on primary windows: BTTS for Pillar A (2024/25 -0.0054 [-0.0102, -0.0008]); over 2.5 for 2015/16 (-0.0068 [-0.0132, -0.0013]). Verdicts differ across windows (56 tests); recorded as provisional in D-039. The model's totals and BTTS probabilities are over-confident (raw log loss close to ln 2) |
| 6 | Efficiency map on real odds vs outcomes, only the D-037 segments, paired bootstrap CIs, BH at 10% FDR, proportional de-vig with power and Shin alongside, early-snapshot labels | **PASS** | `uv run edgeforge pillar-a run` -> `pillar_a.json` | F1 2024/25: 47 claims in the BH family, 0 not estimable; all six D-037 segments reported (league 10 claims, season phase 2, favourite-longshot 10, draw 3, promoted 3, snapshot 2). Every claim carries estimate, 95% CI, p, BH q and power/Shin estimates. 18 `pillar_a_*` figures (6 types for each of F1, F2 and F4) |
| 7 | Favourite-longshot logit-slope test, closing and early | **PASS** | same | slope (1 = calibrated) closing 1.0695 [0.9654, 1.1798] and early snapshot 1.0712 [0.9663, 1.1815] in 2024/25; 2025/26 1.1047 [0.9908, 1.2304]; 2019/20-2023/24 1.0384 [0.9914, 1.0876]; 2015/16 0.9894 [0.8765, 1.1137]. **Not distinguishable from 1 in any block**; no favourite-longshot band bias survives BH |
| 8 | Early vs close: does the line move toward the outcome; closing vs early calibration | **PASS** | same | movement slope (0 = no information in the move, 1 = closing fully efficient): 2024/25 1.0865 [0.4465, 1.7139] q=0.016; 2019/20-2023/24 0.9620 [0.6760, 1.2578] q=0.009; 2015/16 1.0903 [0.3326, 1.9011] q=0.037; 2025/26 0.6841 [-0.0643, 1.4558] (p=0.065, same sign, not significant). Closing minus early log loss: 2019/20-2023/24 -0.0027 [-0.0043, -0.0011] q=0.009; 2024/25 -0.0029 [-0.0065, +0.0007]; 2025/26 -0.0009 [-0.0040, +0.0020]. All early-snapshot results carry the D-024 label |
| 9 | Head-to-head model vs market by D-037 segment | **PASS** | same | 2024/25 model minus Pinnacle-closing log loss is positive (market better) in all 14 segments; BH-surviving: league D1 +0.0378, I1 +0.0204, SP1 +0.0261; rest of season +0.0260; promoted later matches +0.0269; favourite-longshot bands 0 to 3 +0.0128, +0.0094, +0.0218, +0.0219; draw selection +0.0044. Replicated in 2025/26 (same sign, p<0.05): D1, rest of season, promoted later, bands 1 and 2. Did not survive BH: E0 (q=0.101, just above the threshold), F1, early phase, promoted first 10 matches |
| 10 | Forecast-encompassing test, weight with CI | **PASS (null result)** | same | model weight in the log-linear pool (fitted on the tuning window): 2024/25 -0.072 [-0.172, +0.046]; 2025/26 -0.083 [-0.182, +0.032]; 2015/16 -0.225 [-0.441, -0.031]. Pool minus recalibrated market on the test window: 2024/25 -0.0004 [-0.0010, +0.0001]; 2025/26 -0.0002 [-0.0009, +0.0005]; 2015/16 +0.0003 [-0.0017, +0.0023]. **The model adds no information beyond the closing market.** Descriptive: recalibrating the market alone (exponent 1.067 on the tuning window) improves 2024/25 log loss by -0.0014 [-0.0027, -0.0002], i.e. the closing market is mildly under-confident |
| 11 | Line movement toward the model; CLV-style description (no P&L) | **PASS (null result)** | same | slope of (close - early) on (model - early) probability: 2024/25 -0.0035 [-0.0259, +0.0184]; 2025/26 -0.0074 [-0.0275, +0.0121]; 2019/20-2023/24 -0.0082 [-0.0178, +0.0002]; 2015/16 +0.0128 [-0.0079, +0.0352]. By disagreement size the share of selections where the close moves toward the model is 0.497, 0.491 and 0.488 (chance level). No betting P&L is claimed |
| 12 | Cold start: model vs market error for promoted teams' first 10 matches; test ONE fix, chosen on the tuning window | **PASS (fix not demonstrated)** | same | model minus market log loss, promoted first 10 matches: 2019/20-2023/24 +0.0625 [+0.0360, +0.0883] (n=640) vs later matches +0.0273 [+0.0175, +0.0375]; 2024/25 first 10 +0.0287 [-0.0156, +0.0755] (n=132, not significant); 2025/26 +0.0640 [+0.0046, +0.1318] (n=131). Fix (promoted-team prior, kappa 80 / 20 chosen on tuning): improves every test window but no CI excludes zero (D-039): 2024/25 -0.0245 [-0.0493, +0.0003] (q=0.17), 2025/26 -0.0470 [-0.1045, +0.0035], 2015/16 -0.0371 [-0.0808, +0.0055]. Not adopted as default |
| 13 | Figures for each result | **PASS** | `ls artifacts/figures` | reliability by league, by season phase, by promoted status, early vs close, favourite-longshot plot, model-vs-market forest plot, for blocks F1, F2 and F4 (18 figures), alongside the 11 Phase 2 baseline figures: 29 PNGs in total |
| 14 | BH-adjusted list of surviving findings | **PASS** | `pillar_a.json` `bh_survivors` | see "Surviving findings" below |
| 15 | Registry updated | **PASS** | see criterion 3 | |
| 16 | Tests and CI-equivalent checks pass locally | **PASS** | `uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q` | 75 tests pass locally; ruff and mypy clean (49 source files). Fresh clone (short path): 74 passed, 1 skipped (the real-data smoke test needs the local warehouse), ruff and mypy clean |
| 17 | **Green GitHub Actions run** | **FAIL (blocked)** | `gh run list` | every run since the first push has failed before executing a step (billing lock, then `startup_failure`); not attributable to the code |

### Surviving findings (BH, 10% FDR; D-037 allows only these to be called findings)
**Efficiency of the market (what the pre-registered segments actually found):**
1. *Closing line vs early snapshot* (early-market snapshot, approx. 1 to 3 days pre-kickoff, not timestamped): the move to the close carries information about the outcome (slope near 1) and closing log loss is lower than early in 2019/20-2023/24. Survives BH in 2024/25, 2019/20-2023/24 and 2015/16; same sign but not significant in 2025/26.
2. *Bundesliga home-win bias in 2024/25* (closing market over-prices home wins by 6.95 percentage points, CI [-11.72, -1.88], q=0.042) **survives BH in the confirmatory block but does not replicate**: 2025/26 -0.66 points (p=0.81) and 2019/20-2023/24 -0.21 points (p=0.83). With 47 claims at 10% FDR a few false discoveries are expected; this one is treated as probable noise and is not offered as an inefficiency.

Everything else in the efficiency map is **not distinguishable from zero**: no league other than D1 (2024/25), no season-phase effect, no favourite-longshot bias (slope or bands, closing or early), no draw bias or draw slope effect, no promoted-team win bias in either the first 10 matches or later.

**Model vs market:** the market beats the model in every segment; the BH-surviving differences are listed under criterion 9. The model adds no information beyond the market (criterion 10) and the close does not move toward the model (criterion 11).

### Honest summary for the technical report
- The football market is hard to beat: against real Pinnacle closing odds on 2024/25 the best model (Dixon-Coles, tuned decay) has 1X2 log loss 0.9853 vs the market's 0.9606 (static Poisson 0.9899).
- The pre-registered inefficiency hunt found no replicated inefficiency. The only evidence of a market mechanism is that prices move toward the outcome between the early snapshot and the close.
- The model's value is as a structural source of probabilities for the markets without prices (totals, BTTS, props), not as an edge against the 1X2 close.

### Deviations, decisions and caveats
1. **D-039 (new, Proposed)** records the model specification, the selection and adoption rules, the analysis design and the correction record.
2. **A self-caught definition error** in the first complete Pillar A run (encompassing claim credited the model with the market's own sharpening) was corrected before anything was reported; see D-039. A regression test covers it.
3. **Grid edge, again.** The cold-start kappa grid first stopped at 40 with the Pillar A optimum on the edge; I widened it (to 160) and the optimum moved to an interior value (80). I had seen the test numbers of the first grid, though the choice is the minimum of the widened tuning-window grid. Disclose in the technical report.
4. **F3 is in-sample for the model** (decay tuned on that window); its model-vs-market numbers are exploratory. F4 uses the whole of 2015/16 (not only matchweeks 20-38) because the team model was not tuned on any 2015/16 match and the early-phase and promoted segments need the early weeks; the matchweek 20-38 team-model results are reported under criterion 2.
5. **Calibration decisions are provisional** (56 tests, verdicts differ across windows).
6. **Power and Shin** estimates are shown beside every proportional claim but are outside the BH family (D-036).
7. The registry file briefly contained lines from the discarded first Pillar A run; they were removed before commit (uncommitted working state, not history).
8. Fitted team parameters are committed (`artifacts/models/team_dc_params.parquet`, D-028 allows fitted parameters); per-match intensities stay git-ignored.

### Open issues
- **GitHub Actions has never executed a job** (hard criterion).
- Cold-start fix: suggestive but not demonstrated; revisit with more seasons or pooled evidence before relying on it.
- The football-data early snapshot remains untimestamped (D-024).

### Lead audit of Gate 3 (2026-10-05): analytical criteria PASSED; gate closes on a green CI run
- Criteria 1 to 16 accepted. The pre-registered, BH-controlled efficiency audit with a replication rule, the self-caught encompassing error and the planted-distortion test are exactly what a hostile reviewer looks for. The null results (no replicated inefficiency; model adds no information beyond the close; close does not move toward the model) are the honest headline and stay in.
- Criterion 17 (green GitHub Actions) is still required. With the repo public (D-041) it should run; Gate 3 is formally closed when the first green run is recorded below.
- Locked: D-039. New: D-040 (grid-edge rule), D-041 (repo public, supersedes D-038).
- Phase 4 may start in parallel with the CI fix.
- **CI result after Step 0 (implementer, 2026-10-05): NOT GREEN.** Repository set public (D-041) and pushed; run `37274346002` (commit `5b5e354`) failed in 3 seconds with 0 steps executed and the annotation "The job was not started because your account is locked due to a billing issue." (the 2026-10-04 `startup_failure` runs on the private repo were the same block seen differently). Making the repository public did not clear it, so the lock is on the GitHub account itself, not a private-repo minutes limit. Owner action: resolve the account billing/payment state at github.com/settings/billing, then `gh run rerun 37274346002`. Gate 3 stays open until a green run id is recorded here.
- D-041 done: player names replaced by StatsBomb ids in `data_audit_statsbomb.json`, `gate1_validation.json`, `statsbomb_profile.json` (generators changed to emit ids; `tests/test_no_player_names.py` fails if any committed `artifacts/**/*.json` or registry line has a player-name field); `docs/DATA.md` examples use ids. Two items outside the implementer's remit remain: the locked D-031 text and the lead's Gate 1 audit line each name one player as a worked example; edit them or accept. The StatsBomb logo file is not in the repository: README carries the credit and a placeholder line for the owner.

