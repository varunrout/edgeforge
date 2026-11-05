# EdgeForge evidence pack

For a sceptical betting-analytics reader. Every claim below is read from a metrics file produced by code and can be re-derived from a command; none is typed by hand (this file is rendered by `uv run edgeforge docs render` and `uv run edgeforge docs check` fails if it drifts). Interim until the final audit.

## 1. How to verify

```
git clone <repository> && cd <repository>
uv sync --locked
uv run edgeforge ci-local            # fresh clone: ruff, ruff format, mypy, pytest -> artifacts/metrics/ci_local.json
uv run edgeforge docs check          # documents equal a fresh render of artifacts/metrics
```

The metrics files are committed. Re-running a phase needs the local data warehouse (raw data are not redistributed): `uv run edgeforge data fetch-footballdata`, `... fetch-statsbomb-all`, `build-warehouse`, then the phase command shown in the table below. Each metrics file records the git sha, data version, command, config hash, seed and creation time of the run that produced it.

## 2. What the evidence says

| Question | Answer (from the metrics) | Command that regenerates it |
|---|---|---|
| Does the pre-match model beat the Pinnacle closing market? | **No.** 1X2 log loss 0.9853 against 0.9606 on 1752 matches (2024/25). | `uv run edgeforge team-model run`, `uv run edgeforge baselines team` |
| Does the model add information beyond the market? | **No detectable information.** Pool weight -0.072 (95% CI -0.172 to 0.046; the interval reaches 0.046). | `uv run edgeforge pillar-a run` |
| Does the closing line move toward the model? | **No.** Slope -0.0035 (95% CI -0.0259 to 0.0184), measured against an early snapshot roughly one to three days before kick-off, not timestamped (D-024). | `uv run edgeforge pillar-a run` |
| Is there a replicated market inefficiency? | **No.** 5 of 12 confirmatory survivors replicate (same sign, p below one in twenty, uncorrected; the replication season uses the market-average close, Pinnacle prices stop in early 2026), all model-versus-market gaps; the Bundesliga home bias does not (p = 0.81). | `uv run edgeforge pillar-a run` |
| Are player props predictable beyond a rolling baseline? | **Partly.** 5 of six markets improve after multiple-testing control; shots 1+ does not (+0.0000). No prop odds exist to compare with. | `uv run edgeforge phase4 run` |
| Is the joint probability of a same-game accumulator better than the product of its legs? | **Yes, on realised outcomes.** Better in 21 of 26 pre-registered tests, worse in 0; pooled log loss 0.3301 against 0.3572. Both use the same marginals, so this isolates dependence; the named players are mechanically chosen top scorers; multi-player templates are still under-predicted (T10 0.080 predicted, 0.102 realised). | `uv run edgeforge phase5 run` |
| Does knowing the lineups change prop prices? | **Yes, a lot for rotation-risk players** (an upper bound): 81.7% move by more than a quarter, against 16.3% for nailed starters; the lineups price is the better predictor in 6 of six prop markets. | `uv run edgeforge phase6 run` |
| What can a book do about informed lineup knowledge? | **SIMULATION, UPPER BOUND.** On the Phase 6b basis (per offered proposition, void ones count zero) the informed expected profit 0.1015 (void on appearance) falls to 0.0147 (void if not starting) and 0.0007 with odds-ratio margin allocation, at a ten per cent overround. (The Pillar C figure of 0.151 at a five per cent margin is per settled proposition and not comparable.) | `uv run edgeforge phase6b` |
| Does an in-play model beat a naive in-play baseline? | **Yes in 14 of 42 tests, never worse** (largest after a dismissal: log-loss difference -0.0792, 95% CI -0.1420 to -0.0244, 340 propositions, negative is better; the baseline is naive). Both under-predict remaining goals. **No in-play market prices exist, so no comparison with a market is possible.** | `uv run edgeforge phase7` |
| What does the forward shadow log say? | 10 fixtures logged, 0 settled; the first batch (Premier League, 6 to 8 November 2026) was committed and pushed before kick-off, and the push timestamp on the remote is the timing evidence; settlement will use the market-average close (D-059). inconclusive: no settled fixtures with closing prices yet. | `uv run edgeforge shadow predict`, `shadow settle` |

## 3. What is not claimed

- No betting edge, no profit and no expected return: the shadow log and every backtest stop at probabilities; the informed-bettor numbers are a simulation of an adversary, not a strategy.
- No deployment, no commercial use, no customers, no live feed. Prop and SGA results are validated on realised outcomes only.
- No claim that the in-play model is better than a market. The only in-play comparison is with a naive baseline.
- Single-season player data (2015/16); results may not transfer to other seasons or leagues.

## 4. Controls against fooling ourselves

- Splits by date. **From Phase 4 on** test windows are never used for tuning, every tuning grid is checked to end interior before any test metric exists (D-040) and calibration adoption is decided inside the tuning window (D-043). **Known violations before that, disclosed in D-039 and D-040:** the promoted-team prior grid and the player rolling-baseline grid were widened after their test numbers were seen and the Pillar A calibrators were first adopted on the test window; the rolling baseline is the comparison behind the prop results.
- Templates, bands, margins and test families were committed before the corresponding results (D-037, D-047, D-051, D-055, D-056, D-058); `git log` shows the order.
- Multiple-testing control (Benjamini-Hochberg, ten per cent false discovery) within each family; paired match-cluster bootstrap confidence intervals everywhere.
- Null controls on the real data: an independent re-simulation stands in for the "informed" probabilities to size Monte Carlo noise. Null controls in unit tests on synthetic data: a calibrated market yields no survivors in the efficiency analysis; the SGA validation detects a planted dependence and finds none when prices coincide.
- Errors found and disclosed in the gate reports, for example a prediction cache that was indexed by row position and attached minutes to the wrong players until a determinism check caught it; the affected runs were discarded.

## 5. Engineering evidence

A scripted fresh-clone check last passed at commit `fe53bcd626328b53813118fdcd996d8fb173123d` with 210 tests; it covers the code at that commit and later commits change documents only. GitHub Actions is not used (account limitation, D-046). The container image was not built (no Docker engine); a fresh-clone run with production-only dependencies serving the API was checked instead.

## 6. Provenance of every metrics file used

| metrics file | produced by | git sha | created (UTC) | data version |
|---|---|---|---|---|
| `baseline_team.json` | `edgeforge baselines team` | `0e54107` | 2026-10-04T11:22:49Z | 4ff27cd91bb05d85 |
| `ci_local.json` | `edgeforge ci-local` | `fe53bcd` | 2026-11-05T11:37:43Z |  |
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
| `shadow_report.json` | `edgeforge shadow settle` | `c14d2da` | 2026-11-05T16:29:54Z | dc_opening_v1 |
| `team_model.json` | `edgeforge team-model run` | `9de3d25` | 2026-10-05T09:40:49Z | 137781cbbd858114 |

## 7. Every claim and where it comes from

| claim | value | source file | path in the file |
|---|---|---|---|
| Pillar A test matches (2024/25) | 1752 | `pillar_a.json` | `blocks / F1_primary_2024_25 / n_matches` |
| Dixon-Coles 1X2 log loss, 2024/25 | 0.9853 | `team_model.json` | `blocks / pillar_a / tests / primary_2024_25 / per_model / dixon_coles / 1x2 / log_loss` |
| Pinnacle closing 1X2 log loss (proportional de-vig), 2024/25 | 0.9606 | `baseline_team.json` | `blocks / pillar_a_primary_2024_25 / per_model / market_PS_proportional / 1x2 / log_loss` |
| Model weight in the log-linear pool with the market | -0.072 | `pillar_a.json` | `blocks / F1_primary_2024_25 / encompassing_fit / model_weight` |
| Model weight, 95% CI low | -0.172 | `pillar_a.json` | `blocks / F1_primary_2024_25 / encompassing_fit / model_weight_ci95 / 0` |
| Model weight, 95% CI high | 0.046 | `pillar_a.json` | `blocks / F1_primary_2024_25 / encompassing_fit / model_weight_ci95 / 1` |
| Slope of (close - early) on (model - early) | -0.0035 | `pillar_a.json` | `blocks / F1_primary_2024_25 / claims / id=mvm.linemove.slope / estimate` |
| Line-movement slope, 95% CI low | -0.0259 | `pillar_a.json` | `blocks / F1_primary_2024_25 / claims / id=mvm.linemove.slope / ci_low` |
| Line-movement slope, 95% CI high | 0.0184 | `pillar_a.json` | `blocks / F1_primary_2024_25 / claims / id=mvm.linemove.slope / ci_high` |
| Pre-registered efficiency claims surviving BH in 2024/25 | 12 | `pillar_a.json` | `(derived: count of blocks / F1_primary_2024_25 / bh_survivors)` |
| Claims in the 2024/25 BH family | 47 | `pillar_a.json` | `blocks / F1_primary_2024_25 / n_claims_in_bh_family` |
| P(start) log loss, model | 0.5072 | `phase4_participation.json` | `starter_model / start_model / log_loss` |
| P(start) log loss, 'started last match' baseline | 0.5840 | `phase4_participation.json` | `starter_model / started_last_match_baseline / log_loss` |
| Prop markets (of six) beating the Phase 2 baseline after BH, lineups state | 5 | `phase4_markets.json` | `(derived: count of lineups_state / comparison_E_full_distribution_vs_phase2_baseline / bh_family / survivors)` |
| Shots 1+ log-loss difference vs baseline (null) | +0.0000 | `phase4_markets.json` | `lineups_state / comparison_E_full_distribution_vs_phase2_baseline / markets / shots_1plus / log_loss_diff_new_minus_baseline / estimate` |
| Anytime scorer log-loss difference vs baseline | -0.0090 | `phase4_markets.json` | `lineups_state / comparison_E_full_distribution_vs_phase2_baseline / markets / anytime_scorer / log_loss_diff_new_minus_baseline / estimate` |
| Simulator invariant violations over all match-states | 0 | `phase5_simulator.json` | `(derived: computed of values in the file)` |
| Simulated test matches per state | 758 | `phase5_simulator.json` | `states / lineups / n_matches` |
| Simulations per match and state | 20000 | `phase5_simulator.json` | `n_sims` |
| Markets where simulator marginals differ from the standalone models (lineups, BH) | 0 | `phase5_comparison_b.json` | `(derived: count of states / lineups / bh_family / survivors)` |
| SGA template x state tests | 26 | `phase5_sga.json` | `joint_validation / bh_family / n_tests` |
| Tests where the simulated joint beats the naive product (BH 10%) | 21 | `phase5_sga.json` | `(derived: count of joint_validation / bh_family / survivors_better)` |
| Tests where the simulated joint is worse (BH 10%) | 0 | `phase5_sga.json` | `(derived: count of joint_validation / bh_family / survivors_worse)` |
| Pooled joint log loss, simulated (lineups) | 0.3301 | `phase5_sga.json` | `joint_validation / pooled / lineups|all / simulated / log_loss` |
| Pooled joint log loss, naive product (lineups) | 0.3572 | `phase5_sga.json` | `joint_validation / pooled / lineups|all / naive / log_loss` |
| Pooled joint ECE, simulated (lineups) | 0.0152 | `phase5_sga.json` | `joint_validation / pooled / lineups|all / simulated / ece` |
| Pooled joint ECE, naive product (lineups) | 0.0364 | `phase5_sga.json` | `joint_validation / pooled / lineups|all / naive / ece` |
| Settled SGA instances (lineups) | 17434 | `phase5_sga.json` | `joint_validation / pooled / lineups|all / n` |
| Share of rotation-risk prop prices moving more than 25% in odds | 81.7% | `phase6_pillar_c.json` | `shock / props / by_band / rotation / share_abs_odds_change_gt_25pct` |
| Share of nailed-starter prop prices moving more than 25% in odds | 16.3% | `phase6_pillar_c.json` | `shock / props / by_band / nailed / share_abs_odds_change_gt_25pct` |
| Value of information, shots 1+ (log loss opening minus lineups) | +0.0242 | `phase6_pillar_c.json` | `value_of_information / family_V / markets / shots_1plus / opening_minus_lineups / estimate` |
| Prop markets (of six) where the lineups price is better (BH) | 6 | `phase6_pillar_c.json` | `(derived: computed of values in the file)` |
| SGA templates (of 13) where the lineups price is better (BH) | 3 | `phase6_pillar_c.json` | `(derived: computed of values in the file)` |
| Informed expected profit per offered prop, base settlement, 10% overround | 0.1015 | `phase6b_levers.json` | `test / base|0.1 / informed_expected_per_offered` |
| Same, void if not starting | 0.0147 | `phase6b_levers.json` | `test / start|0.1 / informed_expected_per_offered` |
| Same, void if not starting + odds-ratio allocation | 0.0007 | `phase6b_levers.json` | `test / start+odds_ratio|0.1 / informed_expected_per_offered` |
| Checkpoint x market tests | 42 | `phase7_inplay.json` | `family_I_checkpoint_x_market / bh / n_tests` |
| Tests where the in-play model beats the naive baseline (BH 10%) | 14 | `phase7_inplay.json` | `(derived: count of family_I_checkpoint_x_market / bh / model_better)` |
| Tests where it is worse (BH 10%) | 0 | `phase7_inplay.json` | `(derived: count of family_I_checkpoint_x_market / bh / model_worse)` |
| 1X2 log-loss difference after a dismissal, model minus baseline | -0.0792 | `phase7_inplay.json` | `family_II_stratum_x_market / tests / after_dismissal|1x2 / log_loss_diff_model_minus_baseline / estimate` |
| Observed mean remaining goals (all checkpoints) | 1.347 | `phase7_inplay.json` | `remaining_goals_calibration / pooled / observed` |
| Model mean predicted remaining goals | 1.238 | `phase7_inplay.json` | `remaining_goals_calibration / pooled / model` |
| Baseline mean predicted remaining goals | 1.194 | `phase7_inplay.json` | `remaining_goals_calibration / pooled / baseline` |
| In-play test matches | 758 | `phase7_inplay.json` | `n_test_matches` |
| Commit tested by the scripted fresh-clone check | fe53bcd626328b53813118fdcd996d8fb173123d | `ci_local.json` | `commit_sha` |
| Tests passed in the scripted check | 210 | `ci_local.json` | `steps / 4 / test_counts / passed` |
| Dixon-Coles minus static Poisson 1X2 log loss, 2024/25 | -0.0046 | `team_model.json` | `blocks / pillar_a / tests / primary_2024_25 / comparison_A / dc_vs_static_poisson / 0 / mean_diff` |
| Same, 95% CI low | -0.0084 | `team_model.json` | `blocks / pillar_a / tests / primary_2024_25 / comparison_A / dc_vs_static_poisson / 0 / ci_low` |
| Same, 95% CI high | -0.0004 | `team_model.json` | `blocks / pillar_a / tests / primary_2024_25 / comparison_A / dc_vs_static_poisson / 0 / ci_high` |
| Favourite-longshot logit slope at the close (1 = calibrated) | 1.069 | `pillar_a.json` | `blocks / F1_primary_2024_25 / claims / id=eff.flb.close.slope / estimate` |
| Favourite-longshot slope, 95% CI low | 0.965 | `pillar_a.json` | `blocks / F1_primary_2024_25 / claims / id=eff.flb.close.slope / ci_low` |
| Favourite-longshot slope, 95% CI high | 1.180 | `pillar_a.json` | `blocks / F1_primary_2024_25 / claims / id=eff.flb.close.slope / ci_high` |
| Cold-start fix, promoted teams' first 10 matches, log-loss difference | -0.0245 | `pillar_a.json` | `blocks / F1_primary_2024_25 / claims / id=coldstart.fix_vs_none.first10 / estimate` |
| Cold-start fix, 95% CI high (the interval reaches zero) | +0.0003 | `pillar_a.json` | `blocks / F1_primary_2024_25 / claims / id=coldstart.fix_vs_none.first10 / ci_high` |
| Primary-block survivors that replicate in 2025/26 (same sign and p < 0.05) | 5 | `pillar_a.json` | `(derived: computed of values in the file)` |
| Primary-block survivors tested for replication | 12 | `pillar_a.json` | `(derived: count of replication_of_primary_survivors)` |
| Bundesliga home-bias replication p-value (2025/26) | 0.81 | `pillar_a.json` | `(derived: computed of values in the file)` |
| P(start) paired log-loss difference vs baseline | -0.0768 | `phase4_participation.json` | `starter_model / paired_vs_baseline_log_loss / estimate` |
| Bench appearance ECE before D-044 recalibration | 0.0383 | `phase4_participation.json` | `recalibration_d044 / bench_appearance_level / test_raw / ece` |
| Bench appearance ECE after D-044 recalibration | 0.0223 | `phase4_participation.json` | `recalibration_d044 / bench_appearance_level / test_recalibrated / ece` |
| 5-leg template T10: mean simulated joint probability (lineups) | 0.080 | `phase5_sga.json` | `joint_validation / by_template_state / T10|lineups / mean_p_sim` |
| T10: realised rate | 0.102 | `phase5_sga.json` | `joint_validation / by_template_state / T10|lineups / event_rate` |
| T10: mean naive-product probability | 0.028 | `phase5_sga.json` | `joint_validation / by_template_state / T10|lineups / mean_p_naive` |
| Pooled log-loss difference simulated minus naive, 3+ legs (lineups) | -0.0468 | `phase5_sga.json` | `joint_validation / pooled / lineups|3plus_legs / log_loss_diff_sim_minus_naive / estimate` |
| Pooled log-loss difference simulated minus naive, 2 legs (lineups) | -0.0167 | `phase5_sga.json` | `joint_validation / pooled / lineups|2_legs / log_loss_diff_sim_minus_naive / estimate` |
| Match-level frailty (D-049) adopted | False | `phase6_frailty.json` | `decision / adopted` |
| Share of rotation-risk prop prices moving more than 50% in odds | 59.1% | `phase6_pillar_c.json` | `shock / props / by_band / rotation / share_abs_odds_change_gt_50pct` |
| Informed expected profit per SETTLED prop at a 5% margin (Pillar C basis; not comparable with the Phase 6b per-offered figures) | 0.151 | `phase6_pillar_c.json` | `informed_bettor / cells / props|all / reference / 0.05 / informed_expected_per_offered` |
| Same basis with the lineups probabilities replaced by an independent re-simulation (noise floor) | 0.019 | `phase6_pillar_c.json` | `null_control / cells / props|all / curve / 5 / informed_expected_per_offered` |
| Settlement rule chosen on the tuning window (Phase 6b) | start | `phase6b_levers.json` | `tuning_window / chosen_settlement_rule` |
| Recommended combination on the tuning window | start+odds_ratio | `phase6b_levers.json` | `tuning_window / recommended_combination` |
| Odds cap, informed expected profit per offered prop (10% overround) | 0.0913 | `phase6b_levers.json` | `test / cap|0.1 / informed_expected_per_offered` |
| Offer rule, informed expected profit per offered prop (10% overround) | 0.0512 | `phase6b_levers.json` | `test / offer|0.1 / informed_expected_per_offered` |
| Offer rule, share of the offer retained | 60.8% | `phase6b_levers.json` | `test / offer|0.1 / offer_retained` |
| In-play final 1X2 pooled ECE, model | 0.0120 | `phase7_inplay.json` | `ece_pooled / 1x2 / model` |
| In-play final 1X2 pooled ECE, naive baseline | 0.0174 | `phase7_inplay.json` | `ece_pooled / 1x2 / baseline` |
| In-play 1X2 log-loss difference at 60 minutes, model minus baseline | -0.0154 | `phase7_inplay.json` | `family_I_checkpoint_x_market / tests / 60|1x2 / log_loss_diff_model_minus_baseline / estimate` |
| Remaining goals after a dismissal at 75 minutes: model | 0.73 | `phase7_inplay.json` | `remaining_goals_calibration / by_checkpoint_stratum / 75|after_dismissal / model_predicted` |
| Remaining goals after a dismissal at 75 minutes: baseline | 0.54 | `phase7_inplay.json` | `remaining_goals_calibration / by_checkpoint_stratum / 75|after_dismissal / baseline_predicted` |
| Remaining goals after a dismissal at 75 minutes: observed | 0.75 | `phase7_inplay.json` | `remaining_goals_calibration / by_checkpoint_stratum / 75|after_dismissal / observed_remaining_goals` |
| Remaining goals at 45 minutes with a two-goal lead: model | 1.48 | `phase7_inplay.json` | `remaining_goals_calibration / by_checkpoint_stratum / 45|two_plus / model_predicted` |
| Remaining goals at 45 minutes with a two-goal lead: observed | 1.83 | `phase7_inplay.json` | `remaining_goals_calibration / by_checkpoint_stratum / 45|two_plus / observed_remaining_goals` |
| Propositions in the after-dismissal 1X2 comparison | 340 | `phase7_inplay.json` | `family_II_stratum_x_market / tests / after_dismissal|1x2 / n` |
| After-dismissal 1X2 difference, 95% CI low | -0.1420 | `phase7_inplay.json` | `family_II_stratum_x_market / tests / after_dismissal|1x2 / log_loss_diff_model_minus_baseline / ci_low` |
| After-dismissal 1X2 difference, 95% CI high | -0.0244 | `phase7_inplay.json` | `family_II_stratum_x_market / tests / after_dismissal|1x2 / log_loss_diff_model_minus_baseline / ci_high` |
| Phase 6b base informed expected profit per offered prop, 5% overround | 0.1229 | `phase6b_levers.json` | `test / base|0.05 / informed_expected_per_offered` |
| Phase 6b recommended combination, 5% overround | 0.0036 | `phase6b_levers.json` | `test / start+odds_ratio|0.05 / informed_expected_per_offered` |
| Shadow log: fixtures logged | 10 | `shadow_report.json` | `n_logged` |
| Shadow log: fixtures settled | 0 | `shadow_report.json` | `n_settled` |
| Shadow log verdict | inconclusive: no settled fixtures with closing prices yet | `shadow_report.json` | `verdict` |
