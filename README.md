# EdgeForge

> **Work in progress. All numbers in this repository are interim until the final audit by the lead.**
> Independent, non-commercial portfolio project. Nothing here is a claim of commercial use, of a betting edge, or of a deployed product.

A football sportsbook pricing engine built as the lifecycle of a price across three information states: **opening** (about two days before kick-off, no lineups), **lineups** (confirmed XI and bench, one hour before) and **in-play** (score and dismissals up to the minute). Football only.

Every number on this page is rendered from `artifacts/metrics/` by `uv run edgeforge docs render`; `uv run edgeforge docs check` and the test suite fail if a document disagrees with its metrics file. Each metrics file carries its git sha, data version, command, config hash and seed.

## What was found (the negative results come first)

- **The pre-match model does not beat the market.** On 1752 Premier League, Bundesliga, La Liga, Serie A and Ligue 1 matches (2024/25) the Dixon-Coles model has a 1X2 log loss of 0.9853 against 0.9606 for the Pinnacle closing line (de-vigged proportionally). Its weight in a log-linear pool with the market is -0.072 (95% CI -0.172 to 0.046): no detectable information beyond the market (the interval reaches 0.046).
- **The closing line does not move toward the model** (comparison with an early snapshot taken roughly one to three days before kick-off, not timestamped; D-024): slope -0.0035 (95% CI -0.0259 to 0.0184).
- **No market inefficiency replicates.** Of 12 pre-registered findings that survived the multiple-testing control in the confirmatory season, 5 reproduce in the replication season (same sign, p below one in twenty, uncorrected; the replication season is measured against the market-average close because Pinnacle prices stop in early 2026), and those are model-versus-market gaps (the market is better); the Bundesliga home-win bias does not replicate (p = 0.81). The favourite-longshot slope at the close is 1.069 (95% CI 0.965 to 1.180), compatible with a calibrated market.
- **Player props:** 5 of six markets beat a rolling per-90 baseline after multiple-testing control (anytime scorer: log-loss difference -0.0090, negative is better); shots 1+ is a null (+0.0000). No historical prop odds exist, so props are evaluated on probability quality only.
- **Same-game accumulators:** with templates fixed before any test result, the simulated joint beats the naive product of the legs in 21 of 26 template-by-state tests and loses in 0; pooled log loss 0.3301 against 0.3572 on 17434 settled instances, and calibration error 0.0152 against 0.0364. Both prices use the simulator's own marginals, so the comparison isolates dependence, and the named players are each team's mechanically chosen top scorers. The simulator still under-predicts some multi-player templates (the five-leg template T10: 0.080 predicted, 0.102 realised). A match-level frailty meant to fix this was tested and not adopted.
- **Lineup information (Pillar C, an upper bound):** 81.7% of rotation-risk players' prop prices move by more than a quarter once the lineups are known, against 16.3% for nailed starters. In a simulation, a bettor who knows the lineups earns an expected 0.151 units per settled proposition at a five per cent margin (noise floor 0.019). On the stricter per-offered-proposition basis of Phase 6b, at a ten per cent overround, settling props void-if-not-starting with odds-ratio margin allocation cuts the residual to 0.0007 from 0.1015 (at five per cent: 0.0036 from 0.1229); the two bases are not comparable. These are simulations, not observed betting.
- **In-play:** the model beats the naive in-play baseline in 14 of 42 checkpoint-by-market tests and loses in 0 (the final-1X2 log-loss difference after a dismissal is -0.0792, 95% CI -0.1420 to -0.0244, on 340 propositions; negative means the model is better; the baseline is naive). Both still under-predict remaining goals (1.238 model, 1.194 baseline, 1.347 observed). **No in-play market prices exist in the data, so the in-play model cannot be compared with a market.**
- **Forward shadow log (pre-match only, no betting):** the first batch (the Premier League weekend of 6 to 8 November 2026) was committed and pushed before kick-off: 10 logged, 10 settled, against the market-average close because the current football-data files carry no Pinnacle prices (D-059), a weaker benchmark than the Pinnacle close used above; a record is only evidence of timing as far as the push timestamp on the remote. Model 1X2 log loss 0.9630 against 0.9364 for the market-average closing line (model minus market +0.0265, bootstrap 95% CI -0.0481 to +0.1114); 2 of the settled fixtures involve a promoted team with little history and are reported as a separate segment. 10 matches show the process working, not an edge: nothing can be concluded. Verdict: inconclusive: 10 settled fixtures, fewer than the 200 fixed in D-058; nothing can be concluded about the model.

## What is not claimed

No betting edge, no profit, no deployed or commercial use, no live feed, no in-play player props. Props and SGAs are validated on realised outcomes, never against prop odds. All backtests are on the 2015/16 StatsBomb season (props, SGAs, lineup shock, in-play) or the 2024/25 football-data season (pre-match market comparison).

## Known process violations (disclosed)

The cold-start prior grid and the player rolling-baseline grid were widened after their test numbers had been seen, and calibrators were first adopted on the test window; the rules D-040 and D-043 fix this from Phase 4 on and the affected results are labelled in `docs/GATES.md` and `docs/DECISIONS.md` (D-039, D-040, D-043). The rolling baseline is the comparison for the player-prop results above.

## Quick start

```
uv sync
uv run edgeforge --help
uv run edgeforge serve build     # derived serving bundle from the local warehouse
uv run edgeforge serve api       # FastAPI on http://127.0.0.1:8000/docs
uv run edgeforge serve app       # Streamlit trader app on http://localhost:8501
uv run edgeforge shadow predict --fixtures fixtures.csv --commit   # pre-match shadow log
uv run edgeforge shadow settle
uv run edgeforge docs render && uv run edgeforge docs check
uv run edgeforge ci-local        # fresh-clone lint, types and tests
```

Tests and lint run via a scripted fresh-clone check (`uv run edgeforge ci-local`, evidence in `artifacts/metrics/ci_local.json`; last run at commit `f50f8b22dc3376bbeec2e0d3f63460903582a42f`, 211 tests passed); the GitHub Actions workflow is included but not executed (account limitation). Container files and Cloud Run scripts are prepared in `Dockerfile` and `deploy/`; the image was not built here (the Docker engine was unavailable; a fresh-clone run with production-only dependencies was checked instead) and nothing is deployed.

## Where things are

`docs/DECISIONS.md` (every locked decision and pre-registration), `docs/GATES.md` (audited phase reports), `reports/technical_report.md`, `reports/evidence_pack.md` (what a sceptic can verify, and how), `experiments/registry.jsonl` (every experiment including the rejected ones), `artifacts/metrics/` (all numbers, with provenance).

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
