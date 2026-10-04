# Implementation Plan (14 working days, zero slack)

**Framing (D-015):** the lifecycle of a football price. One engine prices every market in three information states (opening, lineups, in-play), and each state answers one research question. Each phase ends at a gate: Claude Code appends a report to `docs/GATES.md`, commits and stops for lead audit. If behind, apply the cut order in D-018.

| State | When | Pillar | Research question |
|---|---|---|---|
| `opening` | T-48h, no lineups | **A. Market efficiency and cold start** | Where, when and why is the football betting market systematically wrong, and does a model beat it there? |
| `lineups` | T-60min | **C. Lineup shock** | How much do player props and SGAs move on team news, who is most exposed, and how should a book protect itself before lineups? |
| `inplay` | minute m | **B. In-play pricing** | How should 1X2 and totals evolve with time, score and dismissals, and are those prices calibrated? |

## Markets in scope
- Team: 1X2, Over/Under (0.5 to 4.5), BTTS, Asian handicap (Pillar A comparison only). In-play: 1X2, remaining-goals totals, BTTS.
- Player (opening and lineups states): anytime goalscorer, 1+ / 2+ shots, 1+ / 2+ shots on target.
- SGA: team and player legs from the same match, in opening and lineups states.

---

## Phase 0: Setup and data verification (Day 1)
- Repo skeleton (`src/edgeforge/{data,features,models,simulation,pricing,sga,inplay,efficiency,lineup,evaluation,api}`, `apps/trader/`, `configs/`, `tests/`, `experiments/`, `artifacts/`, `reports/`), pyproject with uv/ruff/mypy/pytest, typer CLI stub, `.gitignore`, `.env.example`, GitHub Actions on synthetic fixtures.
- Proof of access: StatsBomb Open Data (one match, licence terms) and football-data.co.uk (one league-season). Understat dropped (D-022).
- `docs/DATA.md`, verified against real payloads:
  - StatsBomb Open Data (replaces Understat, D-022): starter flag, minutes, substitution timing, shot outcome and xG, own goals, **card flags and whether red-card minutes are recoverable** (D-017), from a real event payload.
  - football-data: which seasons and leagues have **opening and closing** odds for 1X2, O/U 2.5 and AH, and which bookmakers (Pinnacle especially).
  - Season range and leagues chosen, request count, expected scrape time.

**Gate 0:** CLI and CI run; one real season per source cached; DATA.md verified; season range and red-card data route logged as decisions.

## Phase 1: Ingestion and warehouse (Days 1 to 2)
- Resumable, cached StatsBomb Open Data fetcher (method per Phase 0b decision); football-data downloader.
- DuckDB tables: `matches`, `team_match`, `player_match`, `shots`, `events_timeline` (goals, dismissals with minute), `odds` (opening and closing, long format by bookmaker and market), `team_name_map`, `team_season` (promoted flag, manager-change flag if derivable; otherwise document as unavailable).
- `edgeforge validate` writes checks to metrics JSON.

**Gate 1:** row counts per league-season; odds-to-match join coverage at least 99% with unmatched rows listed; goal reconciliation (player goals + own goals = team goals) at least 99%; goal-minute timeline consistent with final score; no duplicate keys.

## Phase 2: Evaluation harness, baselines, leakage tests (Day 3)
- Walk-forward splitter by date; final season fully held out as test; earlier seasons for tuning.
- Point-in-time feature builder with `asof_ts` and information-state tag.
- Metrics: log loss, Brier, RPS, ECE, reliability diagrams, PIT and interval coverage for counts, bootstrap CIs on every comparison.
- Baselines (status `baseline` in the registry): league-average frequencies; static Poisson; de-vigged closing market (team markets); player rolling per-90 × expected minutes (props); in-play naive baseline = pre-match intensities scaled linearly by time remaining, ignoring score state.
- Leakage tests: data at or after `asof_ts` cannot change features; actual minutes/subs never features in `opening` or `lineups`; in-play state at minute m uses only events before m; test season untouched during tuning.

**Gate 2:** all baselines evaluated walk-forward with provenance; leakage tests pass.

## Phase 3: Team model and Pillar A, market efficiency and cold start (Days 4 to 5)
- Dixon-Coles with time decay vs static Poisson (comparison A). Post-hoc calibration kept only if it improves held-out log loss.
- De-vig methods: proportional, power, Shin; choose by calibration on validation seasons.
- **Efficiency map** on real odds vs real outcomes: closing-line calibration and log loss by league, season phase (first N matchweeks vs rest), favourite/longshot band, draw, promoted teams, post-manager-change matches if available. Opening vs closing: does the line move toward the truth, and where least?
- **Cold start:** does the model's error concentrate in the same segments? Test one fix (e.g. promoted-team prior from previous-league strength or a shrinkage toward a promoted-team average). Report model vs market head to head by segment with CIs.

**Gate 3:** efficiency map tables and plots on real data; comparison A logged; honest model-vs-market verdict by segment (expect the market to win overall; segment-level findings are the point).

## Phase 4: Participation and player models (Days 6 to 7)
- **Starter probability model** (`opening` state): P(start) per player from recent starts, minutes trend, rotation pattern and fixture congestion. Calibrated.
- **Minutes model:** starters' sub-off hazard; bench P(appears) and minutes.
- Shot rate with empirical-Bayes shrinkage by position, scaled by team-model context; SoT beta-binomial; conversion via shrunk xG per shot.
- Comparison E: rolling per-90 baseline vs richer model per prop market. Plug-in expected minutes vs full minutes distribution (price error quantified).

**Gate 4:** starter-probability calibration; prop calibration per market; comparison E logged.

## Phase 5: Simulator and SGA in both pre-match states (Day 8)
- D-014 generative design, vectorised and seeded. `opening` mode samples lineups from starter probabilities; `lineups` mode conditions on confirmed XI and bench.
- Invariants tested (player goals + own goals = team goals, SoT ≤ shots, goals ≤ SoT, off-pitch players record nothing); team marginals match team model within MC SE; convergence plot.
- SGA engine: leg DSL, standalone legs, naive product, joint with MC SE, adjustment ratio. Joint calibration on held-out matches vs naive (D-012).

**Gate 5:** invariants pass; convergence; SGA joint calibration evidence; comparison B logged.

## Phase 6: Pillar C, lineup shock and adverse selection (Day 9)
- For each test-season match: price every prop and a sampled set of SGAs in `opening` and `lineups` states.
- **Information shock:** distribution of price changes by market, player type (nailed starter vs rotation risk), and SGA leg count. Which markets carry most lineup risk. Labelled as an upper bound (D-016).
- **Informed bettor simulation:** a bettor who knows lineups bets pre-lineup prices where lineups-state probability exceeds the quoted price. Book's expected loss per unit staked as a function of pre-lineup margin. Labelled as simulation.
- **Policy output:** pre-lineup margin or stake-limit per market/player-type that brings informed-bettor EV to zero; compare with a flat-margin policy.

**Gate 6:** shock tables and plots; informed-bettor results; policy table; all labels correct.

## Phase 7: Pillar B, in-play team markets (Days 10 to 11)
- State: minute, score, dismissals (per D-017 data route). Time-inhomogeneous goal intensities from the pre-match model with estimated score-state and red-card multipliers.
- Price 1X2, remaining-goals totals and BTTS on a minute grid for test-season matches by replaying each match's timeline.
- Evaluate calibration at checkpoints (e.g. 15, 30, 45, 60, 75 minutes) and by state (level, leading by one, after dismissal) vs naive in-play baseline.
- Showcase: price path through a real match with goal and card events marked.

**Gate 7:** in-play calibration vs baseline by checkpoint and state; dismissal handling documented or cut per D-018.

## Phase 8: Pricing layer, API and trader app (Day 12)
- Fair odds; margin application (proportional, power, odds-ratio) with invariant tests (monotone in margin, overround equals target). Uncertainty-driven margin from Pillar C policy.
- FastAPI: `GET /events`, `GET /events/{id}/markets?state=opening|lineups|inplay&minute=`, `POST /sga/price`, `GET /model-health`.
- Streamlit app organised by lifecycle: pick a match, slide through opening → lineups → in-play minute, see fair vs quoted prices move, build an SGA, see lineup-shock and naive vs adjusted pricing. Secondary tab: efficiency map and calibration.
- Integration tests through the API.

**Gate 8:** app and API run from documented commands; tests pass.

## Phase 9: Containerise, deploy, CI (Day 13)
- Dockerfile, Cloud Run deploy with budget alert (needs Varun's GCP project). Derived artefacts only.
- First item in D-018 cut order: fall back to documented local Docker if behind.

**Gate 9:** public URL health check or documented fallback; CI green.

## Phase 10: Documentation and hostile audit (Day 14)
- `edgeforge docs render`: README, `reports/technical_report.md` (one section per pillar, measured vs simulated vs assumed clearly separated), `reports/evidence_pack.md`, `reports/cv_project_summary.md`.
- Hostile audit by lead plus independent reviewer; fixes applied.

**Gate 10:** brief section 42 minus NBA and Go items, plus each pillar's research question answered with evidence or an honest negative result.
