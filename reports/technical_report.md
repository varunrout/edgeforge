# EdgeForge technical report

> Audited research prototype, not a product. Interim until the final audit. Rendered from `artifacts/metrics/` by `uv run edgeforge docs render`; every number below comes from a metrics file listed in the provenance table at the end. Each result carries one of three labels: **Measured** (realised outcomes of a held-out window), **Simulated** (a model-generated quantity, or a simulation of a bettor), **Assumed** (an assumption stated in a decision record).

## 0. Scope, data and discipline

- **Football only.** Two data sources: football-data.co.uk (results and bookmaker odds, five leagues; the pre-match market comparison uses the 2024/25 season, replication 2025/26) and StatsBomb Open Data for one season, 2015/16 (lineups, minutes, shots, goals, events; players, props, simulator, lineup shock, in-play).
- **Temporal integrity.** Every feature row carries an as-of time; a prior match may feed a feature only if it was complete (latest kick-off plus three hours) by then (D-035). Validation is walk-forward by date. The 2015/16 player block is split by matchweek: burn-in 1 to 9, tuning 10 to 19, test 20 to 38 (D-033). From Phase 4 on, every tuning grid must end interior or be widened before any test metric is computed (D-040) and calibrator adoption is decided inside the tuning window (D-043). **Known violations before that:** the promoted-team prior grid and the player rolling-baseline grid were widened after their test numbers were seen, and the Pillar A calibrators were first adopted on the test window (D-039, D-040); the rolling baseline is the comparison behind the prop results below, and the calibration adoptions were re-decided under D-043.
- **Pre-registration.** The analysis designs were committed before the corresponding test results: the efficiency segments (D-037), the SGA templates (D-047), the Pillar C specification (D-051), the Phase 6b levers (D-055), the in-play model (D-056) and the shadow log (D-058). Every experiment, including rejected ones, is in `experiments/registry.jsonl`.
- **Multiple testing.** Benjamini-Hochberg at ten per cent false discovery within each pre-registered family; only survivors are called findings.

## 1. Pillar A: pre-match market efficiency and model versus market (Measured)

- **Model.** Dixon-Coles per league with exponential time decay (half-life tuned on earlier seasons), weekly refits under the completion rule. Against a static Poisson model the 1X2 log loss changes by -0.0046 (95% CI -0.0084 to -0.0004) on 1752 matches.
- **Against the market.** The Dixon-Coles 1X2 log loss is 0.9853; the de-vigged Pinnacle closing line scores 0.9606. The market is better.
- **Encompassing.** In a log-linear pool of model and market, the model's weight is -0.072 (95% CI -0.172 to 0.046). The pool is compared with the recalibrated market alone so that the market's own sharpening is not credited to the model.
- **Does the line move toward the model?** The slope of the move (close minus an early snapshot taken roughly one to three days before kick-off and not timestamped, D-024) on the disagreement (model minus early) is -0.0035 (95% CI -0.0259 to 0.0184). No.
- **Efficiency map.** 47 pre-registered claims were tested in the confirmatory season. Of the 12 that survived the multiple-testing control, 5 reproduce in the replication season (same sign, p below one in twenty, uncorrected; the replication season is measured against the market-average close because Pinnacle prices stop in early 2026); they are model-versus-market gaps. The Bundesliga home-win bias does not replicate (p = 0.81). The favourite-longshot slope at the close is 1.069 (95% CI 0.965 to 1.180).
- **Cold start.** A promoted-team prior lowers log loss on promoted teams' first matches by -0.0245 (95% CI upper bound +0.0003); the interval reaches zero, so it is not adopted as a default.
- **Honest summary.** The model is a structural source of probabilities for markets without prices (totals, BTTS, props); it is not an edge against the 1X2 close. Of the 12 confirmatory findings, 5 replicated and 7 did not. These statements concern a results-only Dixon-Coles model compared with the Pinnacle closing line; they are not a proof that the market is efficient. Power, taken only from the intervals already reported: the 95% interval of the pool weight has a half-width of 0.109, that of the line-movement slope 0.0222 and that of the favourite-longshot slope 0.107, so effects of about that size or smaller cannot be excluded on this sample.

## 2. Participation and player props, 2015/16 (Measured)

- **Starter probability** (opening state): log loss 0.5072 against 0.5840 for "started last match" (paired difference -0.0768). Bench appearance was under-predicted (calibration error 0.0383); a level recalibration chosen inside the tuning window lowers it to 0.0223 (D-044).
- **Props** (shots 1+, 2+, 3+, shots on target 1+, 2+, anytime scorer), lineups state, against the Phase 2 rolling baseline: 5 of six markets improve after multiple-testing control (anytime scorer -0.0090 log loss); shots 1+ is a null (+0.0000). Settlement follows D-030 (void if the player does not appear). No prop odds exist, so only probability quality is assessed.

## 3. Simulator and same-game accumulators (Simulated, validated on realised outcomes)

- **Design.** Per simulation: scoreline from Dixon-Coles, team shots conditional on goals, minutes from the participation model (confirmed lineups, or sampled starters in the opening state), shots allocated to on-pitch players, goals assigned to shots, goals on target, other shots on target at the player's rate. 20000 seeded simulations per match and state; 758 test matches in each state; invariants (player goals plus own goals equal team goals; goals at most shots on target at most shots; off-pitch players record nothing) are violated 0 times. Dismissals are not simulated as events (assumed).
- **Marginals.** Simulator player marginals tie with the standalone models: 0 markets differ after multiple-testing control.
- **Joint validation (the headline).** Thirteen SGA templates, fixed before the test window, with the named players chosen by a mechanical rule, were priced as the product of the legs and as the simulated joint, and scored on realised outcomes. The simulated joint is better in 21 of 26 tests and worse in 0. The named players are each team's mechanically chosen top scorers. Pooled (lineups) log loss 0.3301 against 0.3572; calibration error 0.0152 against 0.0364; the gain is -0.0167 for two-leg and -0.0468 for three-or-more-leg combinations.
- **What this does and does not show.** Both prices use the simulator's own marginals, so the comparison isolates the dependence structure. The simulated joint still under-predicts several multi-player templates in absolute terms (T10: 0.080 against a realised 0.102; the naive product gives 0.028). A shared match-level frailty was tested (adopted: False).

## 4. Pillar C: lineup shock and adverse selection (upper bound; Simulated bettor)

- **Shock (UPPER BOUND: lineups assumed known with certainty one hour before kick-off).** The fair odds of 81.7% of rotation-risk players' prop prices move by more than a quarter between the opening and lineups states (59.1% by more than a half), against 16.3% for nailed starters (more than a quarter).
- **Value of information (Measured).** The lineups price has the lower log loss in 6 of six prop markets (shots 1+: +0.0242) and in 3 of thirteen SGA templates after multiple-testing control.
- **Informed bettor (SIMULATION).** A bettor who knows the lineups bets the opening price wherever his probability exceeds the quoted break-even. At a five per cent margin the expected profit per settled proposition (the Pillar C basis, not comparable with Phase 6b) is 0.151 units; replacing the lineups probabilities by an independent re-simulation (pure Monte Carlo noise) gives 0.019.
- **Levers a book actually has (Phase 6b, SIMULATION).** On the Phase 6b basis (every in-squad proposition offered; void ones count zero), at a ten per cent overround, the informed expected profit per offered proposition is 0.1015 under void-on-appearance settlement; settlement start (void if the player does not start) alone gives 0.0147, the combination start+odds_ratio gives 0.0007, an odds cap gives 0.0913, and withdrawing rotation-risk props gives 0.0512 while keeping 60.8% of the offer. A proportional-margin "neutralising" policy was rejected as not economic (D-053). No customer demand model exists.

## 5. Pillar B: in-play team markets (Measured against a naive baseline; no market comparison)

- **Model.** Dixon-Coles intensities times a time-of-match profile on the elapsed-seconds clock, a score-state multiplier and own and opponent dismissal multipliers, fitted by penalised Poisson regression on matchweeks 1 to 19, priced by a Markov evolution of the remaining goals with a fitted match-length distribution. Future dismissals are not modelled (assumed).
- **Against the Phase 2 naive baseline** on 758 test matches: better in 14 of 42 checkpoint-by-market tests after multiple-testing control, worse in 0. The final-1X2 log-loss difference (model minus baseline; negative is better) is -0.0154 at 60 minutes and -0.0792 after a dismissal (95% CI -0.1420 to -0.0244, 340 propositions; the baseline is naive). Pooled 1X2 calibration error 0.0120 against 0.0174.
- **Remaining goals.** Observed 1.347, model 1.238, baseline 1.194: both under-predict. After a dismissal at 75 minutes: observed 0.75, model 0.73, baseline 0.54. With a two-goal lead at 45 minutes the model still under-predicts: observed 1.83, model 1.48.
- **No market.** The data contain no in-play market prices (D-017); nothing here compares the in-play model with a market.

## 6. Prototype, shadow log and engineering evidence

- **API and app.** `uv run edgeforge serve api` and `serve app` replay 16 test matches through the lifecycle with margins (proportional, power, odds ratio) and SGA pricing; the evidence tab and `/model-health` show the findings above with their source files.
- **Shadow log (D-058, pre-match only, no betting).** Frozen Dixon-Coles predictions are appended to `artifacts/shadow/log.jsonl` before kick-off and committed; `shadow settle` compares them with the closing market: Pinnacle where the file has it, otherwise the market average, which is what the current football-data files give (D-059), so the settled fixtures are measured against the market-average close, a weaker benchmark than the Pinnacle close of Pillar A. The first batch (the Premier League weekend of 6 to 8 November 2026) was committed and pushed before kick-off; the commit and push timestamps are the timing evidence. Status: 10 logged, 10 settled. Model 1X2 log loss 0.9630 against 0.9364 for the market-average closing line (model minus market +0.0265, bootstrap 95% CI -0.0481 to +0.1114); 2 of the settled fixtures involve a promoted team with little history and are reported as a separate segment. 10 matches show the process working, not an edge: nothing can be concluded. inconclusive: 10 settled fixtures, fewer than the 200 fixed in D-058; nothing can be concluded about the model. The expectation recorded in advance is that the market is better.
- **Engineering.** A scripted fresh-clone check (ruff, mypy, pytest) last passed at commit `c0feeb291841205d2d6fcd0fd9eb6091c07cb48f` with 212 tests (GitHub Actions is unavailable; D-046). A Dockerfile and Cloud Run scripts exist; the image was not built here (no Docker engine) and nothing is deployed.

## 7. Limitations

- One season of player data; matchweeks 20 to 38 only for tests; simulations are conditional on the model being right about minutes and shot shares.
- The shock and informed-bettor results assume lineups are known with certainty and that bets are accepted at the opening price without limits.
- No prop or in-play prices exist, so no market comparison is possible for props, SGAs or in-play markets.
- Player names and raw event data are not stored in the repository (data licence, D-028, D-041).

## Provenance of the numbers

| metrics file | produced by | git sha | created (UTC) | data version |
|---|---|---|---|---|
| `baseline_team.json` | `edgeforge baselines team` | `0e54107` | 2026-10-04T11:22:49Z | 4ff27cd91bb05d85 |
| `ci_local.json` | `edgeforge ci-local` | `c0feeb2` | 2026-11-09T14:31:07Z |  |
| `phase4_markets.json` | `edgeforge phase4 run` | `a75bef4` | 2026-10-07T06:17:14Z | 588d4619e62984b7 |
| `phase4_participation.json` | `edgeforge phase4 run` | `a75bef4` | 2026-10-07T06:17:14Z | 588d4619e62984b7 |
| `phase5_comparison_b.json` | `edgeforge phase5 run` | `811070b` | 2026-10-05T12:10:35Z | ab7850c097da4492 |
| `phase5_sga.json` | `edgeforge phase5 run` | `811070b` | 2026-10-05T12:10:35Z | ab7850c097da4492 |
| `phase5_simulator.json` | `edgeforge phase5 run` | `811070b` | 2026-10-05T12:10:35Z | ab7850c097da4492 |
| `phase6_frailty.json` | `edgeforge phase6 frailty` | `7e13ae7` | 2026-10-07T05:26:31Z | ab7850c097da4492 |
| `phase6_pillar_c.json` | `edgeforge phase6 run` | `ec1d218` | 2026-10-07T19:00:22Z | 588d4619e62984b7 |
| `phase6b_levers.json` | `edgeforge phase6b run` | `7d80c2b` | 2026-10-07T23:46:23Z | 588d4619e62984b7 |
| `phase7_inplay.json` | `edgeforge phase7 run` | `def7c9c` | 2026-10-08T02:55:05Z | 588d4619e62984b7 |
| `pillar_a.json` | `edgeforge pillar-a run` | `42bf956` | 2026-10-04T20:41:08Z | 137781cbbd858114 |
| `shadow_report.json` | `edgeforge shadow settle` | `7394965` | 2026-11-09T14:23:50Z | dc_opening_v1 |
| `team_model.json` | `edgeforge team-model run` | `9de3d25` | 2026-10-05T09:40:49Z | 137781cbbd858114 |
