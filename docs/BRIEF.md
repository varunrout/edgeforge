You are acting as the lead quantitative researcher, data scientist, ML engineer, and software engineer for this project.

Your job is to build this project end to end, not merely plan it, scaffold it, or give me code snippets.

PROJECT NAME
Multi-Sport Sportsbook Pricing Engine

Optional product/repo name:
EdgeForge

PRIMARY OBJECTIVE

Build a rigorous, portfolio-quality quantitative sportsbook pricing system that demonstrates the capabilities expected from a Senior Sports Quantitative Analyst working on:

- player prop pricing
- non-core / derivative sports markets
- Same-Game Accumulator / Bet Builder pricing
- correlated outcome modelling
- probability calibration
- fair odds generation
- sportsbook margin application
- backtesting
- risk and exposure analysis
- trader-facing tools
- multi-sport modelling
- production-style model and API delivery

This project is specifically intended to prove that I can bridge sports data science and quantitative market modelling into sportsbook pricing.

This must NOT become a generic "predict who wins" sports ML project.

The central research and engineering question is:

"How can uncertain and correlated sporting outcomes be converted into coherent, calibrated and commercially usable sportsbook prices?"

==================================================
1. WORKING STYLE
==================================================

Operate autonomously.

Do not repeatedly ask me what to do next.

When there are multiple reasonable technical choices:
1. investigate them,
2. document the trade-off,
3. choose the strongest defensible option,
4. implement it,
5. continue.

Only stop and ask me when there is a genuinely blocking dependency that cannot reasonably be solved without my input, such as a paid API credential.

If an external data source becomes unavailable, do not abandon the project. Find a reproducible public alternative or redesign that component while preserving the project's analytical purpose.

Do not stop after:
- creating folders
- writing a README
- generating notebooks
- making placeholder classes
- producing toy data
- making a dashboard shell

Continue until the analytical system works end to end and the acceptance criteria in this prompt are satisfied.

Prefer working software, validated models, reproducible experiments and defensible analysis over excessive architecture.

Do not overengineer merely to add technologies to the CV.

==================================================
2. NON-NEGOTIABLE EVIDENCE RULE
==================================================

Never invent results.

Never fabricate:
- model performance
- profitability
- ROI
- calibration improvements
- pricing accuracy
- sample sizes
- market coverage
- latency
- deployment status
- number of tests
- number of markets
- statistical significance

Any numeric claim that appears in the README, dashboard, evidence pack or CV summary must be produced by actual code in this repository.

Keep a machine-readable or auditable record showing where every headline metric came from.

Failed models and negative findings are acceptable and should be documented honestly.

Do not optimise metrics simply to manufacture an impressive portfolio result.

If a model does not beat a baseline, document that result and investigate why.

==================================================
3. PROJECT SCOPE
==================================================

Build the system around two sports:

PRIMARY SPORT:
Football / soccer

SECONDARY SPORT:
NBA basketball

Football should receive the deepest treatment because that is my strongest sports domain.

Basketball should demonstrate that the pricing architecture generalises beyond football.

Target roughly:
70 to 80 percent football
20 to 30 percent basketball

Do not build two entirely separate codebases.

Create shared abstractions wherever mathematically appropriate while allowing sport-specific models.

==================================================
4. REQUIRED FOOTBALL MARKETS
==================================================

At minimum, support a useful subset of:

Match-level:
- 1X2
- Over / Under match goals
- Both Teams To Score

Player props:
- anytime goalscorer
- player 1+ goal
- player shots
- player shots on target
- player 2+ shots
- player 1+ shot on target

Optional where data quality supports it:
- assists
- goalkeeper saves
- cards
- player fouls

Do not force markets where the available data cannot support a defensible model.

Player props are a priority.

==================================================
5. REQUIRED NBA MARKETS
==================================================

Support at minimum several player-level markets such as:

- points
- rebounds
- assists
- 3-pointers made

And at least one team/game-level market such as:

- game winner
- total points

If feasible, support a composite market such as:
- PRA

The objective is not to create an exhaustive NBA betting platform.

The objective is to demonstrate multi-sport pricing methodology and player-prop modelling.

==================================================
6. CORE MODELLING REQUIREMENT
==================================================

Do not simply train independent binary classifiers for every market.

The system should model an underlying sporting process from which multiple related markets can be priced coherently.

For football, investigate appropriate models such as:

- Poisson
- Negative Binomial
- Dixon-Coles style adjustments
- hierarchical player/team models
- zero-inflated models where justified
- gradient boosting for conditional components
- player participation/minutes models
- shot-volume models
- scoring conversion models

For NBA, investigate distributions and conditional models appropriate for count and continuous player statistics.

Use machine learning where it adds genuine value.

Use classical statistical models where they provide better interpretability, calibration or structural coherence.

Model selection must be evidence-driven rather than technology-driven.

==================================================
7. JOINT EVENT / MATCH SIMULATION
==================================================

A major deliverable is a coherent simulation engine.

For football, simulated matches should generate internally consistent outcomes from which multiple markets can be derived.

Where practical, simulations should include relevant quantities such as:

- team goals
- player minutes / participation
- player shots
- shots on target
- player goals
- other available player events

The precise structure is your research decision.

The simulation engine must support Monte Carlo estimation of arbitrary market probabilities.

Use enough simulations to produce stable probability estimates, with convergence checks where appropriate.

Do not hard-code fake correlations.

Dependencies must arise from:
- the generative model,
- conditional relationships,
- empirical dependence structures,
- copulas or similar techniques if justified,
- or another defensible statistical framework.

==================================================
8. SAME-GAME ACCUMULATOR / BET BUILDER ENGINE
==================================================

This is one of the most important parts of the project.

Build an engine capable of taking several legs from the SAME match/game and calculating a dependency-aware joint probability.

Example football SGA:

Manchester City win
+
Haaland anytime scorer
+
Haaland 2+ shots on target
+
Over 2.5 match goals

The system must calculate or estimate:

P(A ∩ B ∩ C ∩ D)

Do NOT simply multiply:

P(A) * P(B) * P(C) * P(D)

unless demonstrating the naive independence baseline.

For each SGA, expose:

- standalone probability of each leg
- naive independence probability
- dependency-aware joint probability
- naive fair odds
- dependency-adjusted fair odds
- size and direction of dependency adjustment

Investigate whether Monte Carlo joint simulation is sufficient or whether additional contingency / dependence modelling materially improves results.

Include positive, negative and near-independent examples.

==================================================
9. RESEARCH QUESTION FOR SGA
==================================================

Treat this as a formal research workstream:

"How materially does dependency-aware pricing change Same-Game Accumulator probabilities relative to an independence baseline?"

Evaluate across many combinations.

Analyse dependency by:
- number of legs
- type of legs
- player/team relationship
- match state relationship
- sport

Produce plots and tables showing where naive independence fails most severely.

This should become one of the strongest technical findings in the project.

==================================================
10. FAIR ODDS AND SPORTSBOOK PRICING LAYER
==================================================

Convert estimated probabilities into fair decimal odds.

Implement:

fair_odds = 1 / probability

Then build a pricing layer that can apply configurable bookmaker margin.

Support at least one defensible overround / margin allocation method.

Investigate approaches such as:
- proportional margin
- odds-ratio methods
- power method
- another recognised approach

Document assumptions.

Expose both:
- fair price
- quoted sportsbook-style price

Example conceptual output:

Fair probability: 0.187
Fair odds: 5.35
Target margin: 6%
Quoted odds: 4.XX

All numbers must come from actual implementation.

==================================================
11. MARKET MARGIN REMOVAL
==================================================

Where public bookmaker odds are available and legally/reproducibly accessible, implement de-vig / margin-removal functionality.

Support comparison between:
- bookmaker implied probabilities
- de-vigged market probabilities
- model probabilities

Do not make the core project dependent on a paid betting API.

Optional live integrations should remain optional.

==================================================
12. MODEL EVALUATION
==================================================

Use metrics appropriate for probabilistic forecasting.

At minimum consider:

- log loss
- Brier score
- calibration curves
- calibration error
- reliability diagrams
- sharpness / discrimination where useful
- ROC-AUC only where appropriate, never as the sole headline metric

For count models also evaluate:
- MAE / RMSE where relevant
- distributional fit
- residual diagnostics
- coverage / PIT-type diagnostics where appropriate

Use chronological or otherwise leakage-safe validation.

Never randomly split time-dependent sports observations without justification.

==================================================
13. BASELINES
==================================================

Define baselines BEFORE celebrating model results.

Depending on market, baselines may include:

- historical frequency
- league average
- rolling average
- simple Poisson
- bookmaker implied probability
- independent-leg SGA pricing
- simple player-rate model

Every complex model should have an explicit benchmark.

Document where complex models fail to improve on simpler models.

==================================================
14. BACKTESTING
==================================================

Build a leakage-safe historical backtesting framework.

At minimum support evaluation of:

- fair probability quality
- fair odds
- quoted odds
- hypothetical model-market disagreement
- closing line value where suitable odds data exists
- realised return where data permits
- drawdown
- bankroll exposure
- hit rate, but not as the primary metric
- probability calibration through time

If real historical odds coverage is insufficient, keep probability-model evaluation rigorous and clearly label any simulated pricing experiments as simulations.

Do not blur simulated sportsbook economics with realised historical betting performance.

==================================================
15. RISK / TRADING LAYER
==================================================

Add a lightweight but meaningful trading/risk component.

Do not attempt to recreate an entire sportsbook risk platform.

Demonstrate concepts such as:

- market liability
- aggregate exposure
- concentration by correlated outcome
- sensitivity of price to model uncertainty
- configurable maximum exposure
- margin adjustment under uncertainty
- SGA concentration risk

Where assumptions are illustrative, label them clearly as illustrative.

==================================================
16. TRADER-FACING APPLICATION
==================================================

Build a polished interactive application, preferably Streamlit unless another choice is clearly superior.

The interface should allow a user to:

1. choose sport
2. choose match/game
3. inspect standalone markets
4. view fair probability
5. view fair odds
6. view quoted odds
7. create a Same-Game Accumulator
8. see naive independence pricing
9. see dependency-adjusted pricing
10. see dependency adjustment
11. inspect model/calibration information
12. adjust pricing margin
13. inspect basic risk/exposure outputs

The app should feel like a small quantitative trading/pricing tool, not a generic sports dashboard.

Design for analytical credibility, not flashy decoration.

==================================================
17. API
==================================================

Create a documented pricing API.

Python/FastAPI is acceptable for the primary modelling API.

Suggested endpoints may include:

GET /markets/{event_id}
GET /price/{market_id}
POST /sga/price
GET /model-health
GET /event/{event_id}

Design the final interface based on the actual architecture.

Include schema validation and useful error handling.

==================================================
18. GOLANG COMPONENT
==================================================

A Golang component is desirable because the target role values Go, but do not distort the architecture just to claim Go.

Implement one small genuine Go component if it adds value.

Preferred option:

A lightweight Go pricing gateway/service that:
- accepts a pricing request
- validates payloads
- calls or consumes model outputs from the Python pricing engine
- returns market or SGA pricing responses

Keep statistical modelling in Python.

The Go component should demonstrate practical interoperability rather than duplicating models.

If the Go component adds unreasonable complexity relative to benefit, document that decision before omitting it.

==================================================
19. DATA SOURCES
==================================================

Prioritise real, public, reproducible data.

Potential football sources include:
- StatsBomb Open Data
- other reputable public football datasets

Potential NBA sources include:
- NBA public statistics interfaces
- stable publicly downloadable datasets

Research the best available source before committing.

Store raw data responsibly.

Respect licences and terms.

Document:
- source
- retrieval method
- licence / usage limitations where known
- date obtained
- transformations

Do not make paid credentials mandatory for running the core project.

If an API is unstable, create a reproducible cached-data workflow where permissible.

==================================================
20. PLAYER PARTICIPATION / MINUTES
==================================================

Player props are highly dependent on whether and how long a player plays.

Do not ignore this.

Where data permits, explicitly handle:
- starter probability
- expected minutes
- substitution patterns
- historical playing time
- missing / unavailable players

At minimum, document and model playing-time uncertainty sufficiently to prevent obviously unrealistic prop prices.

==================================================
21. TEMPORAL INTEGRITY
==================================================

Treat sports pricing as a forecasting problem.

Features must be available at the assumed pricing cutoff.

Create clear feature timestamps.

Prevent:
- future leakage
- post-match information leakage
- season summary leakage
- closing data used in opening-price models
- target leakage through player outcome statistics

Include automated tests for important leakage risks where practical.

==================================================
22. MODEL CALIBRATION
==================================================

Calibration is central.

Investigate:
- raw calibration
- Platt scaling
- isotonic regression
- beta calibration
- or suitable alternatives

Only retain post-hoc calibration if it genuinely improves the appropriate held-out metrics.

Do not assume calibrated models are automatically better.

Produce reliability diagrams.

==================================================
23. UNCERTAINTY
==================================================

Do not treat every estimated probability as perfectly known.

Where practical, quantify uncertainty using methods such as:
- bootstrap confidence intervals
- posterior intervals
- parameter uncertainty
- Monte Carlo uncertainty
- ensemble variation

Use uncertainty in at least one useful pricing or risk analysis.

==================================================
24. ENGINEERING QUALITY
==================================================

Structure this as a real repository.

Prefer something along the lines of:

src/
  data/
  features/
  models/
  simulation/
  pricing/
  sga/
  risk/
  api/
  evaluation/

apps/
  trader_dashboard/

go/
  pricing_gateway/

tests/

configs/

data/
  raw/
  interim/
  processed/

artifacts/

reports/

docs/

Exact structure is your decision.

Requirements:
- configuration-driven where sensible
- clear modules
- type hints
- linting
- formatting
- meaningful unit tests
- integration tests for the pricing flow
- deterministic seeds where relevant
- environment specification
- reproducible commands
- no hard-coded local Windows paths
- sensible logging
- no secrets committed
- .env.example where required

==================================================
25. TESTING
==================================================

Testing must cover more than trivial utility functions.

Include tests for critical quantitative logic such as:

- probability-to-odds conversion
- margin application
- margin removal
- SGA independence baseline
- joint probability bounds
- simulation reproducibility
- impossible combinations where relevant
- API schemas
- leakage-related feature behaviour
- model artifact loading
- pricing invariants

Examples of invariants:

0 <= probability <= 1

odds > 1 for non-certain positive-probability outcomes

P(A ∩ B) <= min(P(A), P(B))

Quoted prices should behave consistently when target margin changes.

==================================================
26. EXPERIMENT TRACKING
==================================================

Maintain a proper experiment log.

This can use MLflow or a well-designed lightweight alternative.

Record:
- dataset version
- feature set
- model
- hyperparameters
- validation window
- metrics
- calibration
- notes
- status: promoted / rejected

Do not keep only the winning model.

Preserve evidence of rejected approaches and why they were rejected.

==================================================
27. REPRODUCIBILITY
==================================================

A clean checkout should have a documented path to:

1. install dependencies
2. retrieve or prepare permitted public data
3. build features
4. train models
5. run evaluation
6. generate simulations
7. launch API
8. launch trader app
9. run tests

Create convenient scripts or Makefile/task commands.

If full retraining is computationally expensive, provide both:
- full pipeline
- fast/demo pipeline

But do not replace the real pipeline with a demo-only toy system.

==================================================
28. DOCUMENTATION
==================================================

Create an unusually strong README.

It should explain:

- business problem
- sportsbook pricing context
- architecture
- data
- modelling strategy
- markets supported
- simulation design
- SGA dependency problem
- pricing/margin methodology
- validation design
- calibration
- backtesting
- key findings
- limitations
- how to run
- screenshots
- project structure

Write for a technically sophisticated sports quant hiring manager.

Avoid marketing fluff.

==================================================
29. REQUIRED TECHNICAL REPORT
==================================================

Create:

reports/technical_report.md

This should read like a concise quantitative research report.

Include:

- hypotheses
- methodology
- baselines
- experiments
- results
- calibration
- SGA dependency analysis
- error analysis
- rejected models
- limitations
- future work

It must distinguish clearly between:
- measured results
- simulations
- assumptions
- illustrative examples

==================================================
30. EVIDENCE PACK FOR JOB APPLICATIONS
==================================================

Create:

reports/evidence_pack.md

This file is extremely important.

For every CV-safe claim, include:

CLAIM:
EVIDENCE:
SOURCE FILE / COMMAND:
STATUS:
INTERVIEW DEFENCE:
LIMITATIONS:

Example:

CLAIM:
Built a dependency-aware Same-Game Accumulator pricing engine.

EVIDENCE:
Implemented joint probability estimation from coherent match simulations and benchmarked against independent-leg pricing.

SOURCE:
src/sga/...
reports/...
tests/...

INTERVIEW DEFENCE:
Explain how correlated outcomes invalidate naive multiplication and how the simulation captures the dependence.

LIMITATIONS:
Independent project, not deployed at a commercial bookmaker.

Never put an unsupported claim in this file.

==================================================
31. CV OUTPUT
==================================================

At completion, create:

reports/cv_project_summary.md

Provide:

PROJECT TITLE

ONE-LINE DESCRIPTION

TECH STACK

3-BULLET VERSION

5-BULLET VERSION

TECHNICAL INTERVIEW VERSION

RECRUITER VERSION

Do not write CV claims until the underlying evidence exists.

Do not describe this as professional sportsbook employment.

The wording may say:
- built
- developed
- designed
- evaluated
- implemented

It may NOT falsely imply:
- commercial bookmaker deployment
- real customer usage
- proprietary bookmaker data
- sportsbook employment
- real-money production operation

==================================================
32. TARGET CAPABILITIES
==================================================

By the end, this repository should provide defensible evidence for:

- player prop pricing
- football quantitative modelling
- basketball quantitative modelling
- derivative market pricing
- Same-Game Accumulator pricing
- correlation / contingency modelling
- probabilistic forecasting
- probability calibration
- fair odds estimation
- bookmaker margin modelling
- sports market backtesting
- simulation
- risk and exposure analysis
- trader-facing tooling
- Python
- SQL
- API development
- software engineering
- optionally Golang
- multi-sport modelling

==================================================
33. SPECIFIC COMPARISONS I WANT
==================================================

Where the data permits, investigate these comparisons:

A.
Simple Poisson vs richer football scoring model

B.
Independent player-prop models vs coherent joint simulation

C.
Naive SGA multiplication vs dependency-aware SGA pricing

D.
Raw probability model vs calibrated probability model

E.
Simple rolling player-rate baseline vs richer player-prop model

F.
Football-specific architecture vs reusable multi-sport components

Do not force a preferred winner.

Let the evidence decide.

==================================================
34. IMPORTANT ANALYTICAL QUESTIONS
==================================================

Try to answer questions like:

- Which same-game market combinations show the strongest dependence?
- How much pricing error does naive independence introduce?
- Does dependence become more material as more SGA legs are added?
- Are player-level props harder to calibrate than team-level markets?
- How much does playing-time uncertainty influence player-prop prices?
- When does a richer model outperform a simple statistical baseline?
- How stable are generated prices across time?
- How sensitive are prices to parameter uncertainty?
- Does adding complexity improve log loss / Brier score enough to justify it?
- Can the same pricing framework generalise from football to basketball?

==================================================
35. VISUALISATIONS
==================================================

Create useful analytical graphics, for example:

- reliability diagrams
- model vs baseline calibration
- predicted vs observed frequencies
- SGA naive vs dependency-aware prices
- dependency adjustment distribution
- player prop calibration
- simulation convergence
- price sensitivity to margin
- exposure concentration
- rolling backtest performance
- model residual diagnostics

Avoid decorative visualisations without analytical purpose.

==================================================
36. PRODUCT EXPERIENCE
==================================================

The final trader interface should make it obvious within 30 seconds that this is:

"a sportsbook quantitative pricing and Bet Builder tool"

rather than:

"a football analytics portfolio dashboard".

The first screen should therefore foreground:
- markets
- probabilities
- fair prices
- quoted prices
- SGA builder
- dependency adjustment

not generic charts.

==================================================
37. DEPLOYMENT
==================================================

If feasible within reasonable free-tier constraints, deploy:

- trader dashboard
- pricing API

Use a reproducible cloud deployment method.

Containerise appropriately.

Do not call something "production" merely because it is deployed publicly.

Use wording such as:
"deployed portfolio application"
or
"deployed review environment"

unless there is genuine evidence supporting stronger wording.

==================================================
38. GITHUB QUALITY
==================================================

The repository should look credible to a senior quantitative hiring manager.

Include:

- strong README
- clean commit history where feasible
- issue-free basic setup
- CI
- automated tests
- clear architecture
- no abandoned junk files
- no obviously AI-generated filler documentation
- no dozens of meaningless markdown files
- no fake enterprise architecture

Maintain a CHANGELOG or development log only if useful.

==================================================
39. SECURITY / DATA HYGIENE
==================================================

Do not commit:
- API secrets
- private credentials
- copyrighted datasets that cannot legally be redistributed
- large unnecessary raw artifacts

Use environment variables for credentials.

Document any user-side setup required.

==================================================
40. DO NOT BUILD FAKE COMPLEXITY
==================================================

Avoid:
- Kafka merely for CV keywords
- Kubernetes for a small portfolio tool
- microservices with no real need
- deep learning where a count model works better
- blockchain
- LLM features unrelated to pricing
- fake "real-time streaming"
- fake trading execution
- fake customers
- fake live betting

This project should impress because the quantitative reasoning is strong.

==================================================
41. IMPLEMENTATION PHASES
==================================================

Use this approximate sequence internally:

PHASE 0
Repository audit, research plan, data-source decision, architecture decision.

PHASE 1
Data ingestion, validation, temporal schemas and exploratory analysis.

PHASE 2
Football baseline models and standalone market pricing.

PHASE 3
Football player-prop models.

PHASE 4
Coherent simulation engine.

PHASE 5
SGA / Bet Builder dependency-aware pricing.

PHASE 6
Calibration and historical evaluation.

PHASE 7
NBA second-sport implementation.

PHASE 8
Pricing margin and basic risk layer.

PHASE 9
FastAPI and optional Go pricing gateway.

PHASE 10
Trader dashboard.

PHASE 11
Tests, CI, reproducibility and cleanup.

PHASE 12
Technical report, evidence pack, CV-safe claims and final audit.

You may change the sequence if the dependency graph demands it.

Do not interpret phases as permission to stop after each one waiting for me.

Continue automatically.

==================================================
42. DEFINITION OF DONE
==================================================

Do NOT declare the project complete until:

- at least one football team-level market is genuinely priced
- football player props are genuinely priced
- probabilities are evaluated out of sample
- calibration is assessed
- a coherent simulation engine works
- same-game multi-leg combinations can be priced
- dependency-aware pricing is compared against naive independence
- at least one NBA player-prop family is implemented
- fair odds are generated
- sportsbook margin can be applied
- backtesting/evaluation runs reproducibly
- trader interface works
- pricing API works
- critical mathematical logic is tested
- CI passes
- README reflects actual functionality
- technical report contains real experimental results
- evidence_pack contains only verified claims
- cv_project_summary contains only evidence-backed claims

If any item cannot be completed, clearly state:
1. what is missing,
2. why,
3. what evidence exists,
4. what remains required.

==================================================
43. FINAL AUDIT
==================================================

Before considering the project finished, perform a hostile hiring-manager review.

Assume you are a Senior Sports Quantitative Analyst interviewing me.

Try to break the project.

Ask internally:

- Is the pricing mathematically coherent?
- Is there leakage?
- Is SGA dependence genuinely modelled?
- Are player props actually player-level models?
- Are probabilities calibrated?
- Are baselines meaningful?
- Are claims reproducible?
- Are the NBA claims substantial enough to justify "multi-sport"?
- Is the Go component real or decorative?
- Does the dashboard behave like a trader tool?
- Are any README claims stronger than the evidence?
- Could I defend every architecture decision in an interview?
- Could I reproduce every headline number from the repository?

Fix material issues you identify.

==================================================
44. FIRST ACTION
==================================================

Start now.

First:

1. inspect the current working directory and existing files
2. determine whether this is a new repo or an existing project
3. create a concise implementation plan and decision log inside the repository
4. research/select the best reproducible data sources
5. define the initial data contracts and project structure
6. begin implementation immediately

Do not stop after returning the plan.

Execute the project.