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
