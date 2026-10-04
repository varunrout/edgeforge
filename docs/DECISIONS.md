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
- **Status:** Proposed (implementing engineer, 2026-10-04). Update 2026-10-04 (Phase 0b): the Understat condition is cleared by D-022 (Pillar A uses football-data only), and test seasons are fixed by D-025. The range stands for Pillar A; awaiting lead confirmation. Not edited otherwise.

### D-020 Red-card minute data route (D-017): StatsBomb event cards
- **Decision:** The dismissal minute is read directly from StatsBomb card events (`foul_committed.card` or `bad_behaviour.card` with name `Red Card` or `Second Yellow`, giving `period, minute, second`), cross-checked against `lineups[].cards[]`, which repeats them. It is not derived from lineup `positions` or `Tactical Shift` lineups. football-data `HR/AR` (red-card counts per team-match) are a sanity check only.
- **Evidence (`docs/DATA.md` section 3; `data_audit_statsbomb.json`):** In match 3754217 a `Red Card` (45:44, period 1) left the player in `positions` until the final whistle (derived minutes 99.3, the full match) and in every later `Tactical Shift` lineup (11 players), while a `Second Yellow` (78:23, period 2) closed `positions` correctly. Hence the rule: cap a dismissed player's minutes at the card timestamp.
- **Limits:** two dismissals in one match out of 7 sampled matches. Phase 1 must count, across all 1,517 matches, how often `positions` fail to close at a dismissal and reconcile card counts per team-match with football-data `HR/AR`.
- **Consequence for D-017:** dismissals stay in the in-play state (route found); the D-018 cut order is unchanged. The in-play clock must use `(period, minute, second)` because `minute` restarts at 45 in period 2 while period-1 stoppage runs past 45.
- **Replaces:** the earlier "undetermined" text of this entry (which was blocked by D-021).
- **Status:** Proposed (implementing engineer, 2026-10-04), based on a 7-match sample; awaiting lead confirmation.

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
- **Status:** Proposed (implementing engineer, 2026-10-04)

### D-027 Team-model history range (scores only)
- **Decision:** The Dixon-Coles team model for E0, SP1, I1 and F1 is trained on football-data results from 2005/06 to 2014/15 (10 seasons, 40 CSV files, 380 matches each), then updated through 2015/16 walk-forward. Requested in Phase 0b: 30 new CSVs (SP1, I1, F1) plus 3 for 2015/16; the 10 E0 files and E0 2015/16 were already cached.
- **Why:** all 40 files have 380 rows, 0 ragged rows and the five fields needed (`Date, HomeTeam, AwayTeam, FTHG, FTAG`), per `data_audit_footballdata.json`. Ten seasons give a burn-in plus a pre-2015/16 tuning window for the time-decay hyperparameter, so tuning never touches the 2015/16 evaluation season. The known ragged-row seasons (1993/94, 1994/95, 2003/04, 2004/05; E0) are avoided.
- **Limits:** the history covers only these four top flights, so relegated-from or promoted-to-lower-division strength is not observable (relevant to the cold-start fix in Phase 3; the D1/second-tier files are not fetched). Bundesliga is not in the player-data scope and is used for Pillar A only.
- **Rejected:** going back to 1993/94 (parsing hazards, four decades of rule and style drift); fewer than 10 seasons (no clean tuning window).
- **Status:** Proposed (implementing engineer, 2026-10-04)
