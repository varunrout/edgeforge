# Decision Log

Format: ID | Decision | Why | Rejected alternatives | Status. Locked entries are only changed by a superseding entry.

---

### D-001 Football only
- **Decision:** Build for football only. Brief sections 5 (NBA markets), 33F (multi-sport comparison) and all NBA content are out of scope.
- **Why:** Two-week timebox. A shallow NBA bolt-on would weaken the core claim more than it adds. Football is the owner's strongest domain.
- **Rejected:** Thin NBA prop module (reads as box-ticking under hostile review).
- **Consequence:** No "multi-sport" claim may appear anywhere. Shared abstractions (leg DSL, pricing, margin, risk) should still be sport-agnostic in design, and docs may say the pricing layer is sport-agnostic only if code demonstrates it with tests.
- **Status:** Locked (owner, 2026-10-03)

### D-002 No Go component
- **Decision:** Brief section 18 is dropped.
- **Why:** A Go gateway that validates and forwards to FastAPI duplicates Pydantic validation and adds no analytical value. Within two weeks it would be decorative. The brief itself permits omission with a documented reason.
- **Status:** Locked (owner, 2026-10-03)

### D-003 Data sources
- **Decision:**
  - Understat (top five European leagues, multiple seasons): match results, per-player match data (minutes, position, starter/sub, shots, goals, xG, assists, key passes) and shot-level data (minute, player, result, xG). Primary modelling source.
  - football-data.co.uk: historical bookmaker odds for 1X2 and O/U 2.5 (and closing odds where present) for de-vig, model vs market and CLV on team markets.
  - StatsBomb Open Data: not used in the two-week scope (optional later for cards/fouls/saves).
- **Why:** Understat gives enough seasons for genuine walk-forward validation of team and player models. StatsBomb's open coverage is either single-season or selectively sampled.
- **Constraints:** Understat has no official API and unclear redistribution terms; owner accepts local scraping. Raw data cached locally, never committed or shipped in images. Throttle to at most 1 request per second, cache every response, resumable. Exact seasons/fields to be verified and recorded in `docs/DATA.md` during Phase 0.
- **Rejected:** FBref (anti-scraping terms), paid odds APIs (core must run without credentials).
- **Status:** Locked (owner, 2026-10-03); season range set in Phase 0

### D-004 Pricing cutoff: confirmed lineups
- **Decision:** All prices are generated as at kickoff minus 60 minutes with confirmed starting XI and bench known. Historical "confirmed lineups" are reconstructed from actual starters and named substitutes.
- **Why:** Matches how Bet Builder products are typically priced near kickoff. Makes participation modelling tractable: starters' minutes and bench appearance probability still carry real uncertainty and must be modelled.
- **Leakage note:** Actual minutes played, substitution times and anything after kickoff are targets, never features.
- **Rejected:** Pre-lineup opening prices (out of scope).
- **Status:** Superseded by D-015 (2026-10-04)

### D-005 Storage: Parquet + DuckDB, local
- **Decision:** Raw cache as JSON/Parquet under `data/raw`, cleaned tables in Parquet, a DuckDB warehouse for SQL feature building. No database server.
- **Why:** Data is a few GB at most, single writer. DuckDB gives real SQL (point-in-time joins, window functions) with zero ops.
- **Rejected:** Postgres/Supabase (no concurrency need; keyword-stuffing).
- **Status:** Locked (lead)

### D-006 Experiment tracking: committed JSONL registry
- **Decision:** `experiments/registry.jsonl`, appended by code, committed to git.
- **Why:** Auditable on GitHub, including rejected models. An MLflow store would be invisible to reviewers.
- **Status:** Locked (lead)

### D-007 Metrics provenance and rendered docs
- **Decision:** All metrics written to `artifacts/metrics/*.json` with provenance fields. Numeric claims in docs are rendered from these files; CI checks consistency.
- **Why:** Enforces the brief's evidence rule mechanically.
- **Status:** Locked (lead)

### D-008 Deployment: Google Cloud Run
- **Decision:** One Docker image serving FastAPI and the Streamlit app (or two services from one image if needed), deployed to Cloud Run, scale to zero, budget alert set. Image contains derived artefacts only (fitted parameters, precomputed simulations for a demo fixture set), never raw data.
- **Blocker owned by Varun:** a GCP project with billing enabled and `gcloud` authenticated, needed by Phase 9.
- **Rejected:** Azure, Hugging Face Spaces (owner preference for GCP).
- **Status:** Locked (owner, 2026-10-03)

### D-009 Cross-platform CLI instead of Makefile
- **Decision:** `uv run edgeforge <command>` via typer for every pipeline step.
- **Why:** Owner develops on Windows; Make is not native there.
- **Status:** Locked (lead)

### D-010 Gated phases with lead audit
- **Decision:** Claude Code stops at each phase gate. The lead audits from the repo before the next phase starts. Overrides the brief's "never stop" instruction.
- **Why:** Independent audit instead of self-certification. Catches leakage and circularity early.
- **Status:** Locked (lead)

### D-011 Two-week timebox and cut order (cut order superseded by D-018)
- **Decision:** Hard deadline of 14 working days. If behind, cut in this order: (1) risk layer depth, (2) parameter-uncertainty pricing, (3) xG-informed team model variant, (4) cloud deployment (fall back to documented local Docker). Never cut: leakage tests, baselines, joint calibration evidence, evidence pack integrity.
- **Status:** Locked (owner timebox, lead cut order)

### D-012 SGA dependence must be validated empirically
- **Decision:** Dependency-aware SGA prices are only claimed better than naive multiplication if they are better on observed joint outcomes in held-out matches (joint reliability, log loss and Brier on realised multi-leg outcomes). Simulated dependence alone is not evidence.
- **Why:** Otherwise the headline finding is circular: it only measures the simulator's own assumptions.
- **Status:** Locked (lead)

### D-013 Player-prop evaluation without prop odds
- **Decision:** No free historical player-prop odds exist. Props are evaluated on probability quality only (log loss, Brier, calibration, PIT). Market comparison, de-vig, CLV and hypothetical returns are reported for team markets only.
- **Status:** Locked (lead)

### D-014 Simulator design: default hypothesis
- **Decision (to be tested, not assumed):** Per simulation: (1) draw scoreline from the team model; (2) draw team shot counts conditional on team goals; (3) draw minutes for each player given the confirmed XI and bench; (4) allocate team shots across on-pitch player-time by player shot rate; (5) assign goals to shots by xG share, with goals counted as on target; (6) thin remaining shots to on target by player rate.
- **Requirement:** Team-goal marginals in the simulator must match the team model within Monte Carlo error. Any alternative design must beat this on held-out marginal and joint calibration to replace it.
- **Status:** Proposed (lead); confirmed or superseded at Phase 5 gate

### D-015 Project framing: the lifecycle of a football price
- **Decision:** One engine prices every market in three information states, and each state carries one research pillar:
  - **Opening (T-48h, no lineups). Pillar A: market efficiency and cold start.** Where, when and why is the closing market systematically wrong (league, season phase, favourite/longshot, draw, promoted teams, new managers)? Does the model beat the market in those segments?
  - **Lineups (T-60min). Pillar C: lineup shock and adverse selection on player props.** How much do props and SGAs move when lineups land, which markets and player types are most exposed, what does an informed bettor extract from pre-lineup prices, and what pre-lineup margin or stake limit neutralises it?
  - **In-play (minute m). Pillar B: in-play team-market pricing.** How fair prices for 1X2 and remaining-goals totals evolve with time, score and dismissals, calibrated against realised outcomes.
- **Why:** Combines the three highest-value problems into one coherent story instead of three disconnected mini-projects. All pillars share the team model, player models and simulator.
- **Supersedes:** D-004 (confirmed lineups only).
- **Status:** Locked (owner, 2026-10-04)

### D-016 Pillar evidence standards
- **Pillar A** is evaluated only on real bookmaker odds vs real outcomes (football-data.co.uk). Opening vs closing comparisons only where both columns exist.
- **Pillar C** pre-lineup model uses only historical appearance data. Real bookmakers also know injury news, so the measured lineup shock is an **upper bound** and must be labelled as such everywhere. The informed-bettor and margin-policy results are simulations and must be labelled as such.
- **Pillar B** has no in-play odds; it is evaluated on calibration against realised outcomes only. No claims about in-play market efficiency.
- **Status:** Locked (lead)

### D-017 In-play scope
- **Decision:** In-play covers team markets only (1X2, remaining-goals totals, BTTS) at a minute-level grid, with state = minute, score, dismissals per team. Model: time-inhomogeneous goal intensities derived from the pre-match team model, with score-state and red-card multipliers estimated from data. No in-play player props, no live feed, no latency claims.
- **Data risk:** Red-card minutes may not be directly available in Understat. Phase 0 must verify. Fallbacks in order: derive from player minutes and card flags; estimate red-card effects on StatsBomb Open Data; drop dismissals from the state and document it.
- **Status:** Locked (lead)

### D-018 Revised cut order (zero slack plan)
- **Decision:** If behind, cut in this order: (1) Cloud Run deployment, fall back to documented local Docker; (2) dismissals in the in-play state; (3) SGA under pre-lineup uncertainty (keep single-leg props); (4) cold-start model fixes (keep the diagnosis only); (5) post-hoc calibration experiments beyond Platt/isotonic.
- **Never cut:** leakage tests, baselines, Pillar A on real odds, Pillar C lineup-shock measurement, in-play calibration, evidence-pack integrity.
- **Status:** Locked (lead)

### D-019 Season range and leagues (football-data side verified; final range conditional)
- **Decision (proposed):** Leagues E0, D1, SP1, I1, F1. Seasons 2019/20 to 2025/26 (35 league-seasons, 35 CSV requests, about 35 seconds at 1 request/second, already cached). Pillar A comparisons that need opening and closing on all of 1X2, O/U 2.5 and AH use 2019/20 to 2024/25 (30 league-seasons). 2025/26 is included but flagged: Pinnacle columns stop after 2026-01-08 in the E0 file (`data_audit_footballdata.json`, `pinnacle_1x2_last_date`) and are absent in 2026/27; market `Avg*`/`Max*` closing columns stay complete.
- **Why:** Evidence in `docs/DATA.md` section 2. Pinnacle 1X2 open and close exist from 2012/13 (E0), but Pinnacle O/U 2.5 and AH open and close exist only from 2019/20, and before that O/U and AH are Betbrain aggregates with no closing version. Coverage for 2019/20 to 2024/25 is near-complete in all five leagues.
- **Conditional on Understat/player data:** the range may only shrink, never grow, once the player-data source is settled (D-021). A walk-forward design needs enough seasons for tuning plus a held-out final season; that count cannot be set until player-data coverage is known.
- **Caveat that must travel with every Pillar A result:** the non-"C" odds are a pre-closing snapshot with no capture timestamp in the payload. They are not verified as market-open or as T-48h (DATA.md section 2).
- **Rejected:** Using 2012/13 to 2018/19 for Pillar A beyond 1X2 (no Pinnacle O/U or AH there); excluding 2025/26 entirely (Avg/Max closing is complete and it is the most recent season).
- **Status:** Locked (lead, Gate 0 audit, 2026-10-04)

### D-020 Red-card minute data route (D-017): StatsBomb event cards
- **Decision:** The dismissal minute is read directly from StatsBomb card events (`foul_committed.card` or `bad_behaviour.card` with name `Red Card` or `Second Yellow`, giving `period, minute, second`), cross-checked against `lineups[].cards[]`, which repeats them. It is not derived from lineup `positions` or `Tactical Shift` lineups. football-data `HR/AR` (red-card counts per team-match) are a sanity check only.
- **Evidence (`docs/DATA.md` section 3; `data_audit_statsbomb.json`):** In match 3754217 a `Red Card` (45:44, period 1) left the player in `positions` until the final whistle (derived minutes 99.3, the full match) and in every later `Tactical Shift` lineup (11 players), while a `Second Yellow` (78:23, period 2) closed `positions` correctly. Hence the rule: cap a dismissed player's minutes at the card timestamp.
- **Limits:** two dismissals in one match out of 7 sampled matches. Phase 1 must count, across all 1,517 matches, how often `positions` fail to close at a dismissal and reconcile card counts per team-match with football-data `HR/AR`.
- **Consequence for D-017:** dismissals stay in the in-play state (route found); the D-018 cut order is unchanged. The in-play clock must use `(period, minute, second)` because `minute` restarts at 45 in period 2 while period-1 stoppage runs past 45.
- **Replaces:** the earlier "undetermined" text of this entry (which was blocked by D-021).
- **Status:** Locked (lead, 2026-10-04): dismissal minute read from card events; Phase 1 verifies across all 1,517 matches

### D-021 Understat access: robots.txt disallows all crawling; owner decision required
- **Decision:** No request to any Understat page or endpoint other than `/robots.txt` has been made. `https://understat.com/robots.txt` returned HTTP 200 with `User-agent: *` / `Disallow: /`.
- **Options for the owner:** (1) explicitly accept local, non-commercial, throttled caching despite `Disallow: /` (supersedes the D-003 constraint wording and carries terms-of-service risk), (2) ask Understat for permission, (3) select another player-level source, which requires a separate payload-verified evaluation. No alternative's fields or coverage have been checked.
- **Related note, football-data.co.uk:** its `robots.txt` allows `*` but separately disallows named AI crawlers, including `Anthropic-AI`, `Claude-Web` and `ClaudeBot`. The project downloader uses its own user agent and is a user-run script caching CSVs locally, so it falls under `*`, and 62 CSVs were fetched on that basis. The owner may veto this reading, in which case the football-data cache must be deleted and an alternative odds source chosen.
- **Rejected:** Proceeding with a spoofed user agent or a third-party scraper (would be working around the block silently).
- **Status:** Resolved by D-022 and D-023 (locked, owner, 2026-10-04)

### D-022 Player and event data: StatsBomb Open Data replaces Understat (resolves D-021)
- **Decision:** Understat is not used. Its `robots.txt` disallows all crawling, and a portfolio aimed at sportsbook employers (who care about data rights) should not be built on a source that explicitly refuses access. No Understat request beyond `/robots.txt` will be made.
- **Replacement, verified by the lead from the public repo on 2026-10-04** (`statsbomb/open-data`, `data/competitions.json` and `data/matches/<comp>/<season>.json`): full 2015/16 seasons for Premier League (380 matches), La Liga (380), Serie A (380) and Ligue 1 (377). Bundesliga 2015/16 has only 34 matches and is excluded. All other league-seasons in the repo are partial or club-selected and are excluded.
- **Data split by pillar:**
  - **Pillar A (market efficiency, cold start):** football-data.co.uk only, five leagues, 2019/20 to 2025/26 per D-019. Needs no player data.
  - **Pillars B and C, plus all player props and SGA:** StatsBomb 2015/16 for the four leagues (1,517 matches). Event data gives lineups, substitution minutes, card minutes, shots with outcome and xG, goals with minute.
  - **Team model:** Dixon-Coles needs only scores, so it trains on football-data results for the same leagues from earlier seasons (to be fetched) and updates through 2015/16. football-data 2015/16 odds give a market anchor for team markets in that season.
- **Validation consequence:** player models validate walk-forward **within** 2015/16 (for example, train on matchweeks before k, predict k; report on the second half of the season), with position-level shrinkage to handle thin early-season data. Single season of player data is a stated limitation everywhere.
- **Licence:** StatsBomb Open Data is published under its own user agreement with attribution requirements. Phase 0b must read it, record the terms in DATA.md and comply (attribution in README and app).
- **Status:** Locked (owner, 2026-10-04)

### D-023 football-data.co.uk access (resolves the D-021 note)
- **Decision:** Keep using football-data.co.uk. The site exists to distribute these CSVs for download; its AI-crawler entries in `robots.txt` target bulk crawlers, not a user-run script downloading a few dozen published files. Volume stays minimal (one request per file, cached, never re-fetched without cause), raw files are never committed or redistributed, and the source is credited in the README.
- **Status:** Locked (owner, 2026-10-04)

### D-024 `opening` state label
- **Decision:** The `opening` information state means "football-data pre-closing snapshot". Per the site's notes, weekend odds are collected Friday afternoon and midweek odds Tuesday afternoon, so roughly one to three days before kickoff, but no row-level timestamp exists. Every Pillar A output and the app must use the label "early-market snapshot (approx. 1 to 3 days pre-kickoff, not timestamped)", never "opening price".
- **Status:** Locked (lead)

### D-025 Pillar A test seasons
- **Decision:** 2024/25 is the primary held-out test season for Pillar A (Pinnacle complete). 2025/26 is a secondary out-of-sample check using market average (`Avg*`) closing only, because Pinnacle columns stop after 2026-01-08. Tuning uses 2019/20 to 2023/24.
- **Status:** Locked (lead)

### D-026 StatsBomb retrieval method: per-file raw fetch with cache
- **Decision:** Retrieve the in-scope StatsBomb files (events and lineups for the 1,517 matches of D-022, plus the five `matches` files and `competitions.json`) one file at a time from `raw.githubusercontent.com` through the existing throttled (1 request/second), cached, resumable `CachedFetcher`. Run in Phase 1, not before.
- **Why (numbers from `data_audit_statsbomb.json`):** in-scope events are 4.57 GB and lineups 29.1 MB over 3,034 requests; the whole repository is 16.13 GB, so a per-file fetch avoids the other ~11.5 GB. Throttle floor 50.6 minutes; measured transfer (3 samples, 2.10 MB/s) 36.4 minutes; modelled sequential time about 1 hour. Per-file caching reuses the existing code, resumes after interruption and keeps the request rate explicit.
- **Rejected:** sparse partial `git clone` of the repository. Not tested, transfer volume unmeasured; would add a second retrieval path and a different on-disk layout.
- **Risks:** GitHub raw rate limiting over about 3,000 requests is unknown (retry is cheap because of the cache); disk is 96% used with 24 GB free, so 4.6 GB raw leaves about 19 GB.
- **Status:** Locked (lead, 2026-10-04)

### D-027 Team-model history range (scores only)
- **Decision:** The Dixon-Coles team model for E0, SP1, I1 and F1 is trained on football-data results from 2005/06 to 2014/15 (10 seasons, 40 CSV files, 380 matches each), then updated through 2015/16 walk-forward. Requested in Phase 0b: 30 new CSVs (SP1, I1, F1) plus 3 for 2015/16; the 10 E0 files and E0 2015/16 were already cached.
- **Why:** all 40 files have 380 rows, 0 ragged rows and the five fields needed (`Date, HomeTeam, AwayTeam, FTHG, FTAG`), per `data_audit_footballdata.json`. Ten seasons give a burn-in plus a pre-2015/16 tuning window for the time-decay hyperparameter, so tuning never touches the 2015/16 evaluation season. The known ragged-row seasons (1993/94, 1994/95, 2003/04, 2004/05; E0) are avoided.
- **Limits:** the history covers only these four top flights, so relegated-from or promoted-to-lower-division strength is not observable (relevant to the cold-start fix in Phase 3; the D1/second-tier files are not fetched). Bundesliga is not in the player-data scope and is used for Pillar A only.
- **Rejected:** going back to 1993/94 (parsing hazards, four decades of rule and style drift); fewer than 10 seasons (no clean tuning window).
- **Status:** Locked (lead, 2026-10-04)

### D-028 StatsBomb licence and what may be published
- **Decision:** The repository, reports and any deployed app contain code, fitted parameters, model outputs (probabilities, prices) and aggregate metrics only. No raw StatsBomb JSON, no event-level or player-match tables, and no per-event views are committed, baked into images or displayed. The project is labelled a non-commercial portfolio project everywhere. The StatsBomb logo and source credit appear in the README, reports and app (clause 1.4).
- **Owner actions:** register at the StatsBomb resource centre (clause 2.2) and download the logo from their Media Pack. Before Phase 9, optionally email StatsBomb describing the public demo; if they object, deployment shows Pillar A only (football-data based) and B/C stay local.
- **Status:** Locked (lead, 2026-10-04)

### D-029 Confirmed-lineup assumption
- **Decision:** For the `lineups` state, the StatsBomb lineup file (starting XI plus named bench) is treated as the lineup announced at T-60min. Late withdrawals between announcement and kickoff are rare but not observable in this data. This is an assumption and is labelled as one in every Pillar C output. Who actually came on, minutes and cards remain targets, never features.
- **Status:** Locked (lead, 2026-10-04)

### D-030 Prop settlement conventions
- **Shots on target:** shot outcome in {`Goal`, `Saved`, `Saved to Post`}. Not on target: `Off T`, `Wayward`, `Blocked`, `Post`, and any `Saved Off Target`-type outcome. Any outcome value not listed here that Phase 1 profiling discovers must be classified in a new decision before use.
- **Shots:** every `Shot` event, including penalties and blocked shots. Own goals are not shots.
- **Anytime goalscorer:** goals from `Shot` events with outcome `Goal`, penalties included; own goals do not count for the scorer. Player who does not take part: bet void (priced conditional on appearance in `lineups` state and documented for `opening`).
- **Minutes:** computed on the period-aware clock from `positions`, capped at the dismissal timestamp for `Red Card` and `Second Yellow`.
- **Why:** mirrors common bookmaker settlement rules closely enough for pricing; exact rules vary by operator and that variation is a stated limitation.
- **Status:** Locked (lead, 2026-10-04)

### D-031 Minutes, substitution times and starter flag come from the event stream (supersedes the minutes source in D-030)
- **Decision:** `player_match.minutes`, `sub_on_s`, `sub_off_s`, `gap_minutes`, `vacancy_s` and `started` are derived from the StatsBomb **event stream** (`Starting XI`, `Substitution`, `Player Off`, `Player On`, and `Red Card` / `Second Yellow` card events, processed in event `index` order) on the period-aware elapsed clock. Lineup `positions` are kept for the player's position and as a cross-check (`minutes_positions`, `started_lineup`). The clock, the dismissal cap and all other D-030 conventions (shots on target, goals, penalties, own goals) are unchanged. D-030 said "minutes computed ... from `positions`"; only that source is superseded.
- **Why (full corpus, `statsbomb_profile.json`, `gate1_validation.json`):**
  - Lineup `positions` spans are ordered by `mm:ss` ignoring the period, so a span can run backwards (for example from 45:12 of period 2 to 47:40 of period 1) and a player substituted at half-time can be "restarted" by a period-1 stoppage-time tactical shift. Positions-based minutes exceed the match length for 7 player-matches and differ from the event-based figure by more than 1 second for 40 of 57,665 player-matches (36 of them higher). Example: a player in match 3825739, 91.6 minutes from positions versus 44.6 from events (Player Off 44:35, Player On 46:01, half-time substitution).
  - `positions` almost never close at a dismissal: of 413 dismissals (196 `Red Card`, 217 `Second Yellow`), the final position closes at the card in 2; 406 stay open until the final whistle, 1 closes elsewhere and 4 players have no positions (unused bench players sent off). This corrects the D-020 text, which implied a `Second Yellow` closes correctly (true for one match in the Phase 0b sample, not in general).
  - No dismissed player has any event after the card (413 of 413), so the event stream is consistent with the cap.
  - The lineup-based starter flag disagrees with the `Starting XI` event for 22 player-matches and leaves two teams with no starters.
- **Consequences:** a team can play short without a dismissal (a player leaves with `Player Off (Permanent)` after substitutions are used up, or a replacement arrives late). The Gate 1 identity is therefore `sum of minutes = 11 x match length - sum of vacancy`, where vacancy is derived from events. Unused bench players can receive cards (10 player-matches: 4 red, 6 yellow); they are in `player_match` with `minutes = 0` and are not on-pitch dismissals.
- **Rejected:** keeping positions as the source and clamping minutes to the match length (hides the error); using only positions after reordering spans (the half-time restart case cannot be repaired from positions alone).
- **Status:** Locked (lead, Gate 1 audit, 2026-10-04). Good catch; the half-time example (match 3825739) goes in the technical report as a data-quality finding.

### D-032 Gate 1 validation thresholds
- **Decision:** The implementer's thresholds are confirmed as standing data-quality checks, re-run whenever the warehouse is rebuilt: overround outliers under 1% outside [1.00, 1.20] (Max aggregates excluded); AH line test under 1% of matches differing by more than 0.10 in de-vigged probability with line-sign agreement at least 95%; team minutes identity within 2 seconds.
- **Status:** Locked (lead, 2026-10-04)

### D-033 Validation design per data block
- **Pillar A (football-data, five leagues):** walk-forward by date. Burn-in and tuning 2019/20 to 2023/24 (the team model may also use earlier scores as history), primary test 2024/25, secondary check 2025/26 per D-025. Refit cadence: weekly (each Monday cutoff) unless timing shows it is impractical, then monthly, logged.
- **Team model for the 2015/16 block:** trained on 2005/06 to 2014/15 scores (D-027), time-decay tuned on 2013/14 to 2014/15 only, then walk-forward through 2015/16 with weekly refits.
- **Player models, Pillars B and C (StatsBomb 2015/16, four leagues):** split by `match_week`. Matchweeks 1 to 9 are burn-in only (features accumulate, nothing scored). Matchweeks 10 to 19 are the tuning window. **Matchweeks 20 to 38 are the untouched test window.** Walk-forward: a prediction for matchweek k uses only matches with kickoff before that match. Player history before 2015/16 does not exist in this data; early-season thinness is handled by shrinkage, and matchweek-1-to-9 predictions are never reported.
- **In-play (Pillar B):** same matchweek split; the in-play models are fitted on matchweeks 1 to 19 and evaluated on 20 to 38.
- **Leakage guard:** the splitter writes the exact train/tune/test match_id sets to `artifacts/splits/*.json` (committed: ids only, no data), and a test asserts no tuning or fitting code reads a test-window match.
- **Status:** Locked (lead, 2026-10-04)

### D-034 Scrub StatsBomb player rows from git history before the first push
- **Decision:** Commit `562d584` contains a version of `artifacts/metrics/data_audit_statsbomb.json` with per-player rows for 7 matches. Nothing has been pushed yet, so rewrite history before the first push so that no commit contains player-level StatsBomb rows (for example `git filter-repo` on that path, then re-add the current clean version), and verify with a search of all commits. Player names inside failure/example lists in current metrics files are acceptable (tens of rows, audit purpose).
- **Status:** Locked (lead, 2026-10-04)

### D-035 Point-in-time eligibility and baseline conventions (Phase 2)
- **Eligibility.** A prior match may feed a feature only if it was complete by `asof_ts`: its latest possible kickoff plus 3 hours must not exceed `asof_ts`. This is stricter than "kickoff strictly before asof_ts" in CLAUDE.md, which would admit a match still in play. The target's kickoff is its earliest possible time and a history match's is its latest possible time when no kick-off time exists (football-data before 2019/20), so uncertainty never leaks forward. `asof_ts` is kickoff minus 48 h (`opening`) or minus 60 min (`lineups`); in-play state at elapsed second t uses events strictly before t.
- **Weekly refit.** Team baselines are fitted at the Monday 00:00 cutoff on or before the match's opening as-of time (D-033 weekly cutoffs), so a fit never sees a result that completed after the as-of time.
- **Fixed, untuned baseline settings.** League-average frequencies and the static Poisson use a trailing 1,095-day window; the Poisson has a fixed ridge of 1e-4 for identifiability. Neither is tuned.
- **Market baselines** use closing odds: Pinnacle (`PS`) and market average (`Avg`) for Pillar A (D-025 uses `Avg` for 2025/26 because Pinnacle stops on 2026-01-08), `PS` only for 2015/16.
- **Props** are settled on players who appear (void otherwise, D-030); expected minutes are conditional on appearance. Rolling-rate settings (window N, shrinkage k pseudo-minutes toward the position-group rate) are chosen on matchweeks 10 to 19 only, with every test-window match excluded from the history those choices read. The comparator is the position-group average rate with the same minutes.
- **In-play naive baseline.** Pre-match static-Poisson intensities times the share of the mean match length left (mean fitted on matchweeks 1 to 19), ignoring score and dismissals; the final result is the current score plus the remaining Poisson goals. Checkpoints are elapsed real-time minutes.
- **Deferred.** An opening-state player baseline is not evaluated in Phase 2: the opening-state feature builder exists and is leakage-tested (candidates are players who appeared in the team's previous five matches), but a defensible opening baseline needs the starter-probability model of Phase 4.
- **Status:** Locked (lead, Gate 2 audit, 2026-10-04). The opening-state player baseline is mandatory at Gate 4.

### D-036 Default de-vig method
- **Decision:** Proportional de-vig is the default everywhere. Power and Shin showed no demonstrable difference on the primary block (2024/25: -0.0004 log loss, CI [-0.0010, +0.0003], `baseline_team.json`), so the simplest method wins. All three stay implemented and are reported side by side in Pillar A.
- **Status:** Locked (lead, 2026-10-04)

### D-037 Pillar A pre-registered segments and multiple-testing control
- **Decision:** Pillar A tests only these segments, fixed before any test-season result is examined:
  1. League (E0, D1, SP1, I1, F1).
  2. Season phase: each team's league matches 1 to 6 of the season versus the rest (a match counts as early if either team is in its first six).
  3. Favourite-longshot: selection implied probability bands < 0.20, 0.20 to 0.40, 0.40 to 0.60, > 0.60, with a logit-slope test of outcome on implied probability.
  4. Draw: calibration of the draw outcome specifically.
  5. Promoted teams: matches involving a promoted team, first 10 league matches of their season versus later.
  6. Early snapshot versus close: does the line move toward the outcome, and does closing calibration beat early calibration?
- **Statistics:** every segment result reports a paired bootstrap CI over matches. Across all segment-level significance claims, Benjamini-Hochberg at 10% false discovery rate. Only BH-surviving findings may be called findings in docs; the rest are reported as "not distinguishable from zero".
- **Why:** many segments times many metrics guarantees spurious "inefficiencies". Pre-registration plus FDR control is what a hostile reviewer will ask for first.
- **Status:** Locked (lead, 2026-10-04)

### D-038 Repository visibility
- **Decision:** The repo was created public. Switch it to private until Phase 10 (owner approves going public after the final audit). Work in progress, interim numbers and sample-match event lists should not be public before the evidence pack exists.
- **Status:** Superseded by D-041 (owner keeps the repo public, 2026-10-05)

### D-039 Phase 3 team model: specification, selection rules and decisions
- **Model.** Dixon-Coles per league: independent Poisson goals with a low-score correlation parameter (rho, bounded to +-0.3), home advantage, attack and defence effects, weekly Monday refits under the D-035 eligibility rule, five years of history with exponential time decay, and a fixed weak ridge of 0.5 log-likelihood units. Fitted by weighted maximum likelihood with analytic gradients (verified against a Poisson GLM and by recovering a planted rho in tests).
- **Decay selection.** The half-life is chosen per block by the lowest tuning-window 1X2 log loss over 90, 180, 270, 365, 540, 730 days and no decay. Pillar A (tuned on 2019/20-2023/24): 365 days (0.99327; static Poisson 0.99958; no decay 0.99854). 2015/16 block (tuned on 2013/14-2014/15): 365 days (0.97901; static Poisson 0.98530; no decay 0.98664). Both optima are interior.
- **Calibration rule.** Platt and isotonic maps are fitted on the tuning window and judged on held-out windows. A calibrator is adopted for a market only if, on the block's primary test window (2024/25; 2015/16 matchweeks 20-38), log loss improves with a paired-bootstrap CI that excludes zero. 56 market x method x window tests were run and the verdicts differ across windows, so the adoptions are provisional. Adopted: BTTS (Platt, slope 0.52, intercept 0.14; isotonic kept but not persisted) for Pillar A; over 2.5 (Platt, slope 0.63; isotonic also over 3.5) for the 2015/16 block. 1X2 calibration never helps (no window). The adopted Platt parameters are stored in `team_model.json`; persisted predictions are the raw model outputs.
- **Cold-start fix (one fix, D-037 promoted-team prior).** Promoted teams in their first season are shrunk toward the average first-season rating of promoted teams (from fully completed seasons, pooled across leagues) with strength kappa in log-likelihood units. Kappa was chosen on the tuning window from 0, 2, 5, 10, 20, 40, 80, 160 (the first grid stopped at 40 with the optimum on its edge; it was widened before reporting, after the test numbers for that grid had been seen): Pillar A kappa 80 (promoted first-10 log loss 1.0107 -> 0.9663, n=640), 2015/16 kappa 20 (0.9801 -> 0.9554, n=228). **Test result: not demonstrated.** The fix lowers log loss on promoted teams' first 10 matches in every test window (2024/25 -0.0245 [-0.0493, +0.0003]; 2025/26 -0.0470 [-0.1045, +0.0035]; 2015/16 -0.0371 [-0.0808, +0.0055]) but no 95% CI excludes zero. The fix is therefore **not** the default; predictions with it are persisted in `*_fix` columns for use with that label.
- **Persistence.** `data/processed/team_model_preds.parquet` (git-ignored: per-match intensities, rho, tuning and test windows) and `artifacts/models/team_dc_params.parquet` (committed fitted parameters per league, cutoff and team for the Pillar A test seasons and the whole of 2015/16, 13,901 rows). Phases 4-7 take the 2015/16 intensities from the former.
- **Pillar A analysis design (D-036, D-037).** Four analysis blocks, each with its own BH family at 10% FDR over its estimable claims: F1 2024/25 with Pinnacle closing (confirmatory), F2 2025/26 with market-average closing (replication), F3 2019/20-2023/24 with Pinnacle (exploratory: the model was tuned on this window), F4 the whole of 2015/16 with Pinnacle (team block; the team model was not tuned on it). Claims are enumerated in `build_claims` (efficiency segments, model-vs-market by segment, encompassing, line movement toward the model, cold-start fix) and every one is reported, including null and non-estimable results (a claim with no matches in a block is marked not estimable and left out of the family). Proportional de-vig is the test statistic; power and Shin estimates are shown alongside. A BH survivor in F1 counts as replicated only if F2 shows the same sign with p < 0.05. Everything using the early snapshot carries the D-024 label. The encompassing claim compares the model+market combination with the **recalibrated market alone**, so that the market's own sharpening is not credited to the model.
- **Correction record.** A first complete Pillar A run compared the combination with the raw market; its encompassing claim survived BH purely because the fitted market exponent exceeded 1 (the closing market is mildly under-confident), while the model weight was about zero. That definition was corrected before any result was reported or committed, its registry lines were discarded, and a regression test now checks that an under-confident market with an uninformative model is not credited with model information.
- **Status:** Locked (lead, Gate 3 audit, 2026-10-05). Calibration adoptions stay provisional; the cold-start fix stays a labelled variant, not the default.

### D-040 Grid-edge rule (process fix)
- **Decision:** It happened twice (player rolling grid, cold-start kappa grid): the first grid put the optimum on its edge and test numbers were seen before widening. From Phase 4 on, every tuning routine must assert the chosen value is interior to its grid **before** any test-window metric is computed; if it is on the edge, the code widens the grid and re-tunes automatically, and the pipeline only then computes test metrics. A test enforces the order. Both past cases stay disclosed in the technical report.
- **Status:** Locked (lead, 2026-10-05)

### D-041 Repository stays public (owner decision)
- **Decision:** The owner keeps `varunrout/edgeforge` public from now on. Consequences, all binding:
  - README gets a short "work in progress, numbers are interim until the final audit" banner until Phase 10.
  - D-028 applies in full to everything public: from now on no committed file contains StatsBomb player names, event lists or player-match rows. Failure and example lists in metrics files use StatsBomb ids only. Replace the existing name lists in `data_audit_statsbomb.json`, `gate1_validation.json` and `statsbomb_profile.json` with ids. A history rewrite for these small lists is not required (D-034 already removed the player-row tables).
  - The StatsBomb logo and credit go into the README now, not at Phase 10.
  - Public repositories get GitHub Actions without the private-repo billing requirement, which should also clear the CI blocker.
- **Status:** Locked (owner, 2026-10-05)

### D-042 Phase 4 conventions: participation, minutes, shots and player markets
- **Candidates (opening state).** The start/bench/out model scores players who appeared for the team in its previous five matches (asof T-48h, D-035 eligibility). A player who is a candidate for both clubs of a match (a mid-season mover) is kept for both, and rows are aligned by position, never merged on (match, player) alone. Actual starters outside the candidate set are counted and reported (2.7% in the test window).
- **Models.** Start/bench/out: multinomial logistic. Minutes: discrete-time hazards in 5-minute bins plus a 90+ bin for starter exits and bench entries, logistic with position-group bin effects, refit at biweekly Monday cutoffs. Team shots: Dixon-Coles ratings on football-data HS/AS (weekly refit) mapped to StatsBomb shots by a log-linear Poisson regression; Poisson vs negative binomial chosen on tuning-window log likelihood. Players: shots NB(mean = omega_p x team shots x minutes / (11 x mean match length)), SoT beta-binomial posterior predictive, goals by binomial thinning with shrunk xG per shot times a heavily shrunk finishing multiplier. Position groups with fewer than 20 prior shots fall back to pooled priors.
- **Tuning.** Every grid goes through `tune_interior` (D-040) on the tune window (matchweeks 10-19) with test matches excluded from training rows and features; test code receives only `require_tuned` values. Player parameters are tuned coordinate-wise in a fixed order (k, alpha, a, b, c), so the joint optimum is not guaranteed; this is disclosed. Grid widenings are recorded in the metrics (`n_widenings`).
- **Settlement.** D-030: markets are priced conditional on appearance and evaluated on players who appeared (void otherwise). Lineups state uses actual starters/bench with the minutes mixture. Opening state uses the mixture over start/bench/out, conditional on appearance.
- **Baselines.** Lineups: the Phase 2 rolling per-90 baseline in its chosen configuration (Comparison E). Opening: the same rolling rate times mean minutes per past appearance, with no participation model. The opening baseline is weak by construction, so opening gains overstate the value of the participation model against a stronger baseline.
- **Statistics.** Paired match-cluster bootstrap (1000 draws) on log-loss differences; BH at 10% FDR across the six markets within each comparison.
- **Status:** Locked (lead, Gate 4 audit, 2026-10-05)

### D-043 Calibrator selection must not use the test window (fix to D-039)
- **Problem found in the Gate 4 audit:** D-039 adopts a post-hoc calibrator for a market only if it improves log loss **on the primary test window**. That uses the test window for model selection, so the reported test improvement of an adopted calibrator (BTTS for Pillar A, over 2.5 for 2015/16) is optimistically biased.
- **Decision:** Calibrator adoption is decided inside the tuning window only: fit on its first part and choose on its last part (e.g. Pillar A: fit 2019/20 to 2022/23, choose on 2023/24; 2015/16 team block: fit 2013/14, choose on 2014/15; 2015/16 player block: fit matchweeks 10 to 14, choose on 15 to 19). The adopted set is then evaluated once on the test window and reported whatever the result. Re-run the Phase 3 calibration study under this rule, update `team_model.json` and the Gate 3 numbers that depend on it, and record both the old (biased) and new adoptions. Apply the same rule to any calibration in Phase 4 onwards.
- **Status:** Locked (lead, 2026-10-05)

### D-044 Participation recalibration before simulation
- **Decision:** Bench appearance is worse calibrated than its baseline (ECE 0.0383 vs 0.0195) and early starter exits are under-predicted (24.9% predicted vs 27.1% actual leave early). Both feed every simulated prop and SGA. Before the simulator consumes them, test a post-hoc recalibration of bench P(appear) and of the starter exit hazard level under the D-043 rule (fit and choose inside matchweeks 10 to 19). Adopt only if chosen inside the tuning window; report test results either way.
- **Status:** Locked (lead, 2026-10-05)

### D-045 Simulation storage
- **Decision:** Do not persist full simulation arrays for all test matches (758 matches x tens of thousands of sims x about 36 players would be several GB on a nearly full disk). Simulations are regenerated deterministically from (match_id, state, seed, model version). Cache only summary tensors needed for evaluation (per-leg indicator matrices for the pre-registered SGA templates) and full arrays for a small demo fixture set used by the API and app.
- **Status:** Locked (lead, 2026-10-05)

### D-046 GitHub Actions unavailable: scripted local CI replaces it
- **Decision:** The owner's GitHub account is billing-locked and will not be fixed during this project, so GitHub Actions cannot run. The hard "green GitHub Actions run" criterion is replaced by a scripted local check:
  - `uv run edgeforge ci-local` clones the repo at HEAD into a short temporary path, runs `uv sync --locked`, `ruff check`, `ruff format --check`, `mypy`, `pytest -q`, and writes `artifacts/metrics/ci_local.json` (commit sha, each command, exit code, test counts, timestamp). That file is committed at every gate and is the CI evidence.
  - The workflow file stays in the repo (it is correct and documents the intended CI), but its triggers change to `workflow_dispatch` only, so the public repo does not show a red failure on every push.
  - README and every claim say "tests and lint run via a scripted fresh-clone check; GitHub Actions workflow included but not executed (account limitation)". Never write "CI passes" or show a CI badge.
  - The brief's definition-of-done item "CI passes" is reported as replaced, with this reason.
- **Effect on gates:** Gates 3 and 4 close on a committed `ci_local.json` showing all checks passing at their gate commits (or at current HEAD if the gate code is unchanged).
- **Status:** Locked (owner and lead, 2026-10-05)

### D-047 Pre-registered SGA joint validation (D-012), written before any Phase 5 test result
- **Status of this entry:** written and committed before the Phase 5 evaluation code was run on the test window (matchweeks 20-38, 2015/16). Nothing below may change after results are seen; additions go in a new decision.
- **Who is named (mechanical, no discretion).** For each match, state (`lineups`, `opening`) and team, rank the team's players by the unconditional anytime-scorer probability s = P(appears) x P(scores | appears), taken from the **Phase 4 standalone model** (not from the simulator, to avoid circularity). Lineups: P(appears) = 1 for starters, 1 - P(never enters) for bench players. Opening: P(start) + P(bench) x P(appears | bench). `top1` is the highest s, `top2` the second (ties broken by lower StatsBomb player id). A team with fewer than two candidates yields no instance for templates that need `top2`.
- **Templates** (own = the team whose player is named; H/A = home/away; an instance is built for each side where the text says "per side"):
  | id | legs | definition | relationship tag |
  |---|---|---|---|
  | T01 | 2 | own win + own top1 AGS (per side) | player vs team result, same side |
  | T02 | 2 | win + opponent top1 AGS (per side) | player vs team result, opposing |
  | T03 | 2 | top1 AGS + Over 2.5 goals (per side) | player vs total |
  | T04 | 3 | BTTS yes + H top1 AGS + A top1 AGS | opponents + BTTS |
  | T05 | 2 | top1 AGS + top2 AGS, same team (per side) | teammates |
  | T06 | 2 | top1 SoT 2+ + same player AGS (per side) | same player |
  | T07 | 2 | Under 2.5 goals + top1 AGS (per side) | player vs total |
  | T08 | 3 | own win + Over 2.5 + own top1 AGS (per side) | mixed |
  | T09 | 4 | BTTS yes + Over 2.5 + H top1 AGS + A top1 AGS | mixed |
  | T10 | 5 | own win + Over 1.5 + own top1 AGS + own top1 SoT 2+ + own top2 shots 1+ (per side) | mixed |
  | T11 | 4 | own win + own top1 AGS + own top2 AGS + Over 2.5 (per side) | mixed, teammates |
  | T12 | 2 | own win + own top1 shots 3+ (per side) | player vs team result, same side |
  | T13 | 2 | H top1 AGS + A top1 AGS | opponents |
- **Settlement (D-030 void rule).** An instance is priced conditional on every named player appearing and evaluated only on matches where every named player appeared. Naive price = product of the standalone leg probabilities (each conditional on its own player appearing, from the same simulation). Simulated price = joint frequency over simulations in which all named players are on the pitch.
- **Competing prices.** (a) naive product of the simulator's standalone legs, (b) simulated joint. Both come from the same simulation, so they have identical marginals; the comparison isolates the dependence. The Phase 4 standalone models are not used as legs, so a gain cannot be attributed to better marginals.
- **Tests.** Per template and state: log loss and Brier of simulated minus naive on realised joint outcomes, paired match-cluster bootstrap (1000 draws), 26 tests (13 templates x 2 states) in one Benjamini-Hochberg family at 10% FDR. Reported also: pooled and by leg count joint reliability diagrams for both prices; the dependence ratio (joint / naive) distribution by template, leg count and relationship tag; ECE.
- **Possible outcomes we commit to reporting:** the simulated joint is better, equal or worse than naive. A template where the simulated joint is worse is reported as such and the dependence structure is called unvalidated for that template.
- **Curated examples:** the three largest positive, three largest negative and three nearest-to-one dependence ratios among instances with joint probability above 1% in the lineups state, listed by match id, template and StatsBomb player ids, with the command that regenerates them (`edgeforge phase5 example`).
- **Status:** Locked (implementer, pre-registered before test evaluation; lead to audit)

- **Lead audit (2026-10-06):** pre-registration verified: committed in `c5fe68d` before any test evaluation. Locked.
### D-048 Simulation count and what is cached (from the convergence study)
- **Decision:** 20,000 simulations per match and state (`configs/phase5.yaml`). In the convergence study (8 test matches, lineups state, `phase5_convergence.json`) at 20,000 sims the mean Monte Carlo SE is 0.0032 for the home-win probability, 0.0032 for the top scorer's AGS probability, 0.0026 for the 3-leg SGA (T08) and 0.0022 for the 5-leg SGA (T10), i.e. 2.3% relative for the 5-leg probability; the error against a 200,000-sim reference is at the same level. 50,000 sims would cut the SE by a further 37% for 2.5 times the runtime (0.51 s per match at 20,000 sims, 1.28 s at 50,000 in the convergence run; the full-run wall clock varied between 0.7 and 7.7 s per match-state with machine load). The SE falls as 1/sqrt(n), as the plot shows. Rare joint events (below 1%) carry larger relative error; joint estimates are clipped to [1/(2n), 1 - 1/(2n)] before log loss so that a zero count does not give an infinite loss.
- **Caching (D-045):** simulations are regenerated from (seed, match id, state, model version). For all 1,516 match-states, only per-instance summaries are kept in `data/processed/phase5_sga_instances.parquet` (joint, naive, SE, effective sims, realised outcome, template). Full arrays are written for the first five test matches of each state to `data/processed/demo_sims/` (git-ignored). Per-leg indicator matrices for all templates would be about 25 GB at 20,000 sims and are not stored; they are rebuilt by `edgeforge phase5 example`.
- **Status:** Locked (lead, Gate 5 audit, 2026-10-06)

### D-049 Match-level overdispersion experiment (Phase 6 step 0)
- **Why:** Gate 5 shows the simulator beats the naive product but still under-predicts multi-player templates in absolute terms (lineups: T10 0.080 vs 0.102 realised, T11 0.048 vs 0.065, T06 0.153 vs 0.179, T05 0.064 vs 0.082). A plausible cause is missing match-level variance: some matches are "open" for both sides, which raises every scorer leg together. Dixon-Coles with rho only links low scores.
- **Decision:** Test one extension: a shared match-level gamma frailty multiplying both teams' goal (and shot) intensities, mean 1, variance theta. Choose theta on the tuning window only (matchweeks 10 to 19, using the D-047 templates and the team markets; D-040 interior rule, D-043 fit/choose split if needed). Adopt only if it improves the tuning-window joint log loss **and** does not worsen team-market log loss on the tuning window beyond its CI. Then evaluate once on test and report either way. The D-047 Phase 5 results stay as the primary, pre-registered headline; the frailty result is an additional, labelled experiment.
- **Status:** Locked (lead, 2026-10-06)

### D-050 Pillar C pre-registration (must be committed before any Pillar C test result)
- **Decision:** Before evaluating Pillar C on matchweeks 20 to 38, the implementer writes and commits a decision fixing: the markets (the six Phase 4 prop markets and the D-047 SGA templates); player-type bands by opening P(start) (for example < 0.5 rotation risk, 0.5 to 0.85 likely, > 0.85 nailed); the shock metric (change in log-odds and in fair decimal odds, opening to lineups); the informed-bettor rule (bets an opening quoted price when the lineups-state probability exceeds the break-even probability of that quoted price; void if the player does not appear, D-030); the margin grid; and the BH families. Same standard as D-047.
- **Status:** Locked (lead, 2026-10-06)

### D-051 Pillar C specification (the pre-registration required by D-050), committed before any Pillar C result
- **Status of this entry:** written and committed before any Pillar C quantity was computed for matchweeks 20-38 (and before any prop or SGA price pair for the tuning window was computed). The D-049 frailty experiment runs first and touches only team markets, the D-047 templates and the tuning window.
- **Propositions.** (a) *Props:* each of the six Phase 4 markets (shots 1+/2+/3+, SoT 1+/2+, anytime scorer) for every opening-state candidate who is in the lineups-state squad; a player who is not in the squad is void (D-030) and excluded, one who is in the squad but does not appear is void and excluded. (b) *SGAs:* the 13 D-047 templates, with the named players chosen by the **opening-state** ranking (the mechanical D-047 rule applied to the opening state, i.e. what a book could quote before lineups), priced with the same legs in both states; an instance is void if any named player is not in the lineups squad or does not appear. Prices are conditional on appearance and come from the same simulator, seed and model version (D-045, D-048; theta as decided by D-049) in both states.
- **Player-type bands by opening P(start)** (Phase 4 start model): `rotation` < 0.50, `likely` 0.50 to 0.85, `nailed` > 0.85. An SGA takes the lowest band among its named players. Leg-count groups: 2, 3, 4, 5.
- **Shock metric** (labelled UPPER BOUND, D-016 and D-029: lineups are assumed known with certainty at kickoff - 60 minutes and the opening state is the T-48h information): for opening and lineups probabilities p_o and p_l (conditional on appearance), change in log-odds logit(p_l) - logit(p_o) and relative change in fair decimal odds (1/p_l)/(1/p_o) - 1 = p_o/p_l - 1; shares of propositions with |relative odds change| above 10%, 25% and 50%; quantiles 5/25/50/75/95 by market x band, template and leg count. Value of information: log loss of p_o minus log loss of p_l on realised outcomes per market and per template, paired match-cluster bootstrap (1000 draws).
- **Informed-bettor rule (labelled SIMULATION):** the book quotes the opening fair price with margin m. Props are two-way (yes and no) with proportional margin: quoted probabilities q_yes = p_o (1 + m) and q_no = (1 - p_o)(1 + m) (overround m), decimal odds 1/q. An SGA has one price, q = p_o,joint (1 + m). The bettor knows p_l and bets one unit on a side if its lineups probability exceeds the break-even probability of the quoted price (p_l > q_yes, or 1 - p_l > q_no; SGAs yes only); payout (1/q - 1) on a win and -1 on a loss. Void propositions are excluded as above. Probabilities are clipped to [1e-4, 1 - 1e-4].
- **Margin grid:** 0.00 to 0.60 in steps of 0.01; reference margins 5%, 10%, 20%.
- **Reported per cell** (market x band, template, leg count; and pooled) at each margin: propositions offered, bets, bet rate, informed bettor's expected profit per bet and per offered proposition under the lineups probabilities (the book's expected loss is its negative; the book's expected P&L per unit staked is minus the former per bet), realised informed P&L per unit staked and per offered proposition, all with match-cluster bootstrap 95% CIs.
- **Neutralising margin:** the smallest margin on the grid at which the informed bettor's expected profit per offered proposition (lineups probabilities) is at most epsilon = 0.001 (0.1 stake units per 100 propositions offered; `configs/phase6.yaml`). By construction every placed bet has positive expected profit for the bettor under p_l, so the profit reaches zero only as bets disappear, which is why a tolerance is needed.
- **Policy (illustrative).** Fitted on the **tuning window** (matchweeks 10-19; nothing is fitted on the test window): per market x band neutralising margins m*, a flat margin (smallest margin neutralising the pooled props at epsilon, and the worst-cell margin), and a stake-limit rule (maximum stake per bet such that the informed bettor's expected profit per match at margin 5% is at most L = 0.50 stake units, `configs/phase6.yaml`). Evaluated once on the test window: expected and realised informed profit per offered proposition under the differentiated, flat and stake-limit policies, and the margin points a nailed starter is charged under each. Reported as illustrative; no uninformed customer flow exists in the data, so the cost of margin to customers is described in margin points, not revenue.
- **Tests and BH families.** Family V (value of information): 6 markets + 13 templates = 19 tests of mean (log loss p_o - log loss p_l) = 0, BH 10%. Family B (exploratory band split of the same): 18 market x band tests, separate BH. Family P (informed profit at the 5% reference margin): realised informed profit per offered proposition = 0, 6 markets + 13 templates = 19 tests, BH 10%. Only BH survivors are called findings.
- **What would count against the project's hypothesis:** if the lineups probabilities do not predict outcomes better than the opening ones (Family V null), the upper-bound shock is information-free noise and the informed bettor's realised profit is not distinguishable from zero; both are reported as such.
- **Status:** Locked (implementer, pre-registered, lead to audit)

### D-052 Post-hoc additions to Pillar C (labelled; D-051 otherwise followed as written)
- **Why:** the first complete Pillar C run showed that no pooled prop cell reaches the D-051 neutralising threshold inside the pre-registered margin grid (0 to 60%), because lineup shocks for rotation-risk players are far larger than any plausible proportional margin; a "not reached" result is reported as such, but alone it gives no policy comparison.
- **Additions, all labelled post-hoc in the metrics and the gate report:** (1) an extended search grid for the neutralising margin, 0 to 500% in steps of 5% (`configs/phase6.yaml`), used for the illustrative policy; cells unreached even at 500% are capped; the pre-registered grid results are kept next to them; (2) a null control: the lineups probabilities replaced by an independent re-simulation of the opening state, to size the profit that Monte Carlo noise alone creates; (3) SGA propositions need at least 200 effective simulations in both states; (4) the stake-limit rule is evaluated at the 5% reference margin. No result was selected among alternatives.
- **Status:** Locked (implementer, pending lead audit)
