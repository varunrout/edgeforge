# EdgeForge: project summary for a CV

Independent, non-commercial portfolio project: a football sportsbook pricing engine built and audited phase by phase. It is not a betting system, has no customers and is not deployed.

- Built a pricing stack for football markets across three information states (pre-lineup, confirmed lineups, in-play): a Dixon-Coles team model, starter and minutes models, a player shot / shot-on-target / goal model, a seeded Monte Carlo simulator for same-game accumulators, an in-play intensity model, a margin and fair-odds layer, a FastAPI service and a Streamlit trader app.
- Evaluated everything out of sample with pre-registered designs, walk-forward splits, paired bootstrap confidence intervals and Benjamini-Hochberg control; every number in the reports is rendered from metrics files with provenance (git sha, data version, command, seed).
- Reported the negative results prominently: the pre-match model does not beat the Pinnacle closing line (1X2 log loss 0.9853 against 0.9606 on 1752 matches) and adds no detectable information beyond it; no market inefficiency replicated.
- Showed on held-out matches that simulated same-game-accumulator joint probabilities are better calibrated than the product of the legs (better in 21 of 26 pre-registered tests, worse in 0; calibration error 0.0152 against 0.0364; both prices share the simulator's marginals, so the test isolates dependence), while documenting where the simulator still under-predicts.
- Measured lineup-information risk for player props (an upper bound) and compared the levers a book can pull in simulation: settlement rule, margin allocation, offer rule, odds cap.
- Built an in-play model that beats a naive baseline in 14 of 42 checkpoint tests (none worse) and states plainly that no in-play market data exist to compare against.
- Engineering: Python, DuckDB, pandas, scikit-learn, FastAPI, Streamlit; typed (mypy), linted (ruff), tested (210 tests in a scripted fresh-clone check), container and Cloud Run scripts prepared; a pre-match shadow log built to the same standard (one weekend of Premier League fixtures logged before kick-off so far; no result yet).

Data: football-data.co.uk and StatsBomb Open Data (used under their terms; raw data not redistributed).
