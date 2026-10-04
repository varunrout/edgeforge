# Data sources: verified against real payloads

Status as of 2026-10-04 (Phase 0b). Every statement is either (a) read from a payload fetched in this
phase, (b) read from `artifacts/metrics/data_audit_footballdata.json` or
`artifacts/metrics/data_audit_statsbomb.json` (produced by `uv run edgeforge data audit-footballdata`
and `uv run edgeforge data audit-statsbomb`), (c) a quote or paraphrase of a document I read, labelled
as such, or (d) explicitly marked **NOT VERIFIED**. Nothing comes from memory.

| Source | Role (D-022) | Status |
|---|---|---|
| football-data.co.uk | Pillar A odds and outcomes; team-model results history | Verified (section 2) |
| StatsBomb Open Data | Lineups, events, shots, cards: pillars B and C, props, SGA | Verified on 7 matches; licence read (section 3) |
| Understat | Dropped | See D-022. No data was ever requested; nothing further here |

---

## 1. Understat: dropped

`understat.com/robots.txt` disallows all crawling (`User-agent: *`, `Disallow: /`). The source is not used
and no further request will be made. Record: D-021, D-022. Nothing in this repo depends on it.

---

## 2. football-data.co.uk

### Access (D-023, locked)
- Plain CSV at `https://www.football-data.co.uk/mmz4281/<season>/<league>.csv`. Season `2425` = 2024/25. Leagues: `E0` Premier League, `D1`, `SP1`, `I1`, `F1`. No credentials. UTF-8 with BOM.
- `robots.txt` (read 2026-10-04): `User-agent: *` has an empty `Disallow:`; separately it disallows named AI crawlers (`GPTBot`, `ChatGPT-User`, `Google-Extended`, `Anthropic-AI`, `Claude-Web`, `ClaudeBot`, `PerplexityBot`, `CCBot` and others). D-023 records the owner-approved reading: the project downloader is a user-run script with its own user agent fetching a small number of published files, throttled at 1 request/second and cached.
- Raw files are never committed or redistributed. Credit the source in the README.
- Retrieval date for all payloads: 2026-10-04. No licence statement was found in the files or notes.

### Cached so far (95 CSVs, 11.2 MB, git-ignored)
E0: every season 1993/94 to 2026/27 (34). SP1, I1, F1: 2005/06 to 2015/16 and 2019/20 to 2025/26 (18 each). D1: 2019/20 to 2025/26 (7). Non-CSV pages (`robots.txt`, `notes.txt`, `data.php`, `matches.php`, `englandm.php`) were also read a few times to get the column key; that count was not logged.

### "Opening" versus closing (D-024, locked)
- Columns without a `C` are "pre-closing" odds; the closing version inserts `C` after the bookmaker code (`PSH` -> `PSCH`). Source: `notes.txt`.
- `notes.txt` (quoted): "Betting odds for weekend games are collected Friday afternoons, and on Tuesday afternoons for midweek games." No odds-capture timestamp exists in any file. Label: **early-market snapshot (approx. 1 to 3 days pre-kickoff, not timestamped)**, never "opening price".
- Evidence that the two snapshots differ (E0, Pinnacle home win, `pinnacle_home_open_vs_close` in the audit JSON):

  | E0 season | pairs | share identical | mean abs diff |
  |---|---|---|---|
  | 2012/13 | 380 | 0.066 | 0.179 |
  | 2018/19 | 380 | 0.050 | 0.236 |
  | 2023/24 | 380 | 0.034 | 0.220 |
  | 2024/25 | 380 | 0.024 | 0.194 |
  | 2025/26 | 210 | 0.057 | 0.157 |

### Pinnacle columns by market
| Market | Early snapshot | Closing | First E0 season with data |
|---|---|---|---|
| 1X2 | `PSH`, `PSD`, `PSA` | `PSCH`, `PSCD`, `PSCA` | 2012/13 (both) |
| O/U 2.5 | `P>2.5`, `P<2.5` | `PC>2.5`, `PC<2.5` | 2019/20 |
| Asian handicap | `PAHH`, `PAHA` (line `AHh`) | `PCAHH`, `PCAHA` (line `AHCh`) | 2019/20 |

- The notes list `PH/PD/PA` as Pinnacle aliases. No cached file has a `PH` column, so only `PS*` is used.
- Asian handicap lines `AHh` and `AHCh` are single columns shared across bookmakers. **NOT VERIFIED** that Pinnacle's `PAHH/PAHA` are quoted at that line. Check in Phase 1 before AH is used.
- Before 2019/20, O/U and AH exist only as Betbrain aggregates (`BbAv>2.5`, `BbMx>2.5`, `BbAHh`, `BbAvAHH`, ...), average/maximum, with no closing version.
- Bookmakers with both early and closing 1X2 columns (E0, audit `closing_bookmakers_1x2`): 2012/13 to 2018/19 `PS` only. 2019/20: `Avg, B365, BW, IW, Max, PS, VC, WH`. 2024/25: `1XB, Avg, B365, BF, BFE, BW, Max, PS, WH`. 2025/26: `Avg, B365, BFD, BFE, BMGM, BV, BW, CL, LB, Max, PS`. O/U closing in 2024/25: `B365, P, Max, Avg, BFE`; AH closing: `B365, P, Max, Avg, BFE`.
- `Max*` / `Avg*` are market maximum/average per `notes.txt`; the number of contributing bookmakers is not in the payload.

### Coverage: 2019/20 onward (Pillar A, D-019, D-025)
Counts of non-empty values from the audit JSON; "n" is parsed match rows.

| League-season | n | Pinnacle 1X2 early | 1X2 close | O/U early | O/U close | AH early | AH close |
|---|---|---|---|---|---|---|---|
| E0 2019/20 to 2024/25 | 380 each | 380 | 380 | 372 to 380 | 373 to 380 | 380 | 380 |
| D1, F1, I1, SP1 2019/20 to 2024/25 | 279 to 380 | at most 2 missing | complete | up to 14 missing (D1 2022/23, 2023/24) | up to 13 missing (D1 2023/24) | up to 2 missing | complete |
| E0 2025/26 | 380 | **210** | **210** | 210 | 210 | 210 | 210 |
| D1 / F1 / I1 / SP1 2025/26 | 306 / 306 / 380 / 380 | 150 / 153 / 200 / 189 | 149 / 153 / 198 / 188 | 142 / 152 / 200 / 188 | 142 / 152 / 198 / 189 | 150 / 153 / 200 / 189 | 149 / 153 / 198 / 189 |

The range rows summarise per-file values in the JSON.

**Pinnacle disappears mid-2025/26.** In the E0 2025/26 file the last match with a `PSH` value is 2026-01-08 (`pinnacle_1x2_last_date`); none from 2026-01-17. The 2026/27 file (50 matches, to 2026-09-20) has no Pinnacle columns. Market `Avg*`/`Max*` closing columns stay complete in 2025/26. Cause not in the payload.

### Coverage: 2015/16 (the StatsBomb season; Phase 0b check)
From the audit JSON, files `E0/SP1/I1/F1` season `1516`, 380 parsed rows each, 0 ragged rows.

| League | Pinnacle 1X2 early | 1X2 close | Pinnacle O/U | Pinnacle AH | Other O/U and AH columns | Red-card counts `HR` |
|---|---|---|---|---|---|---|
| E0 | 380 | 380 | none (no column) | none | Betbrain aggregates only | 380 |
| SP1 | 379 | 380 | none | none | Betbrain aggregates only | 380 |
| I1 | 380 | 380 | none | none | Betbrain aggregates only | 380 |
| F1 | 380 | 380 | none | none | Betbrain aggregates only | 380 |

- Answer: Pinnacle 1X2 early and closing **are present** for 2015/16 in all four leagues. Pinnacle O/U and AH are **absent**; the O/U and AH columns that exist are Betbrain average/maximum with no closing version (`BbAv>2.5`, `BbMx>2.5`, `BbAHh`, `BbAvAHH`, ...). The market anchor for 2015/16 team markets is therefore Pinnacle 1X2 (early and close); 2.5-goal totals have only an average-of-bookmakers early snapshot, and AH has no usable anchor.
- Date coverage per league matches the season (E0 2015-08-08 to 2016-05-17, F1 2015-08-07 to 2016-05-14, I1 2015-08-22 to 2016-05-15, SP1 2015-08-21 to 2016-05-15).
- Ligue 1: football-data has 380 matches for 2015/16, StatsBomb has 377.

### Match-level fields
- Present: `FTHG, FTAG, FTR, HTHG, HTAG, HTR, Referee, HS, AS, HST, AST, HF, AF, HC, AC, HY, AY, HR, AR`.
- `HR/AR` are red-card **counts** per team-match (non-empty in E0 from 2000/01). No minutes. They can cross-check a timeline built from StatsBomb events (section 3).
- No goal minutes, lineups or player data. `Time` (kick-off) first appears in E0 2019/20.

### Parsing hazards (Phase 1 must handle)
- **Ragged rows** (field count differs from header): E0 1993/94 and 1994/95 (79 rows each), 2003/04 and 2004/05 (45 each). All outside the ranges used.
- Blank separator rows in 1993/94-era files.
- Date format changes: `dd/mm/yy` before 2019/20, `dd/mm/yyyy` from 2019/20.
- Column sets vary by season (106 columns in 2019/20, 120 in 2024/25, 132 in 2025/26); build the odds table by column name.
- Team names are free text (see section 4).

### Team-model history (scores only): D-027
2005/06 to 2014/15 for E0, SP1, I1, F1: 40 CSVs, all with 380 rows and 0 ragged rows, all containing `Date, HomeTeam, AwayTeam, FTHG, FTAG` (audit JSON). 30 were requested in Phase 0b; the 10 E0 files were already cached. Plus 2015/16: 4 files (3 requested in Phase 0b).

### Reproduce
```
uv run edgeforge data fetch-footballdata E0,D1,SP1,I1,F1 1920,2021,2122,2223,2324,2425,2526
uv run edgeforge data fetch-footballdata E0,SP1,I1,F1 0506,0607,0708,0809,0910,1011,1112,1213,1314,1415,1516
uv run edgeforge data audit-footballdata
```

---

## 3. StatsBomb Open Data

### Source and licence
- Repository `github.com/statsbomb/open-data`, files fetched from `raw.githubusercontent.com/statsbomb/open-data/master/data/...` (JSON). The repository README and `LICENSE.pdf` ("StatsBomb Public Data User Agreement", standard terms last updated 8 September 2023) were read in full on 2026-10-04. `LICENSE.pdf` is cached at `data/raw/statsbomb/LICENSE.pdf` (165,130 bytes; git-ignored).
- Terms, paraphrased with clause numbers:
  - **Permitted:** use for analysis and research (1.1); analysis built on the data may be shared publicly (preamble).
  - **Not permitted:** editing, distorting, distributing, reproducing, selling or providing the data to any external party (1.2.1); commercially exploiting the data or any analysis derived from it (1.2.2); illegal use, defamatory material, reverse engineering (1.2.3 to 1.2.5); modifying, transferring, distributing, licensing or otherwise exploiting the data without prior written consent, since StatsBomb owns it (7).
  - **Attribution:** any publication of analysis formed from the data must carry the StatsBomb brand logo (1.4). The README adds: state the source as StatsBomb and use the logo from their Media Pack.
  - **Registration:** StatsBomb asks users to register name and email at `www.statsbomb.com/resource-centre` before access (2.2). Not done by me; owner action.
  - Service is as-is and may be withheld at any time without notice (2.1, 3.2, 3.3). English law (9).
- **My reading for this project (not legal advice):** private local analysis, public sharing of results, and a portfolio repository that contains code and aggregate metrics but no data are within the terms. Nothing forbids the use in Phase 0b, so I did not stop. Residual risks for the lead and owner:
  1. **Public deployment (Phase 9, D-008).** A hosted app that shows per-match, per-player prices computed from this data could be read as providing the data, or analysis derived from it, to external parties (1.2.1), and "commercially exploit ... any analysis" (1.2.2) is broad. A job-application portfolio is not a sale, but the wording is not narrow. Suggest: no raw or event-level data and no per-event tables in the image or repo; show model outputs only; or ask StatsBomb in writing.
  2. **Repository hygiene.** Raw JSON is git-ignored and must stay so. Derived player-match tables should not be committed either; commit code and aggregate metrics only.
  3. **Logo.** The StatsBomb logo is required wherever analysis is published (README, reports, app). The Media Pack is not downloaded; owner action.

### Scope verified (D-022)
From `matches/<comp>/<season>.json` and `competitions.json` (80 competition-seasons in the catalogue), season 2015/2016 (`season_id` 27 in every competition):

| Competition | `competition_id` | Matches | `match_status` | Dates | Home manager listed |
|---|---|---|---|---|---|
| Premier League | 2 | 380 | all `available` | 2015-08-08 to 2016-05-17 | 380 |
| La Liga | 11 | 380 | all `available` | 2015-08-21 to 2016-05-15 | 377 |
| Serie A | 12 | 380 | all `available` | 2015-08-22 to 2016-05-15 | 379 |
| Ligue 1 | 7 | 377 | all `available` | 2015-08-07 to 2016-05-14 | 351 |
| **Total** | | **1,517** | | | |

Bundesliga 2015/2016 (`competition_id` 9): 34 matches, excluded. Match record keys include `match_id, match_date, kick_off, match_week, home_score, away_score, home_team{...managers}, away_team, referee, stadium, match_status`. `match_week` and `kick_off` are present for every match in scope. Kick-off has no time zone in the payload.

### Payload facts (7 matches: 5 Premier League, 1 La Liga, 1 Ligue 1)
Sample: 3754097, 3754138, 3754217, 3754237, 3754300 (PL), 3825652 (La Liga), 3829513 (Ligue 1). Match 3754217 (Chelsea 2-0 Arsenal, 2015-09-19) was the first in the PL list and happened to contain a dismissal of each kind, an own goal and substitutions; 3754237 contains a penalty and a post. The sample is small and not random. Facts below hold for these 7 matches only and must be re-verified across all 1,517 in Phase 1. All figures: `data_audit_statsbomb.json`, `sample_matches`.

| Question | Answer from the payload |
|---|---|
| Starter flag | **Yes.** `lineups/<id>.json`: per player `positions[0].start_reason == "Starting XI"` for starters; the `Starting XI` event has `tactics.lineup` (11 players per team in all 7). 22 starters per match. |
| Bench | **Yes, named bench included.** Each team's lineup lists 18 players (11 + 7 bench) in all 7 matches; unused bench players have `positions == []` (8 or 9 unused per match). Used substitutes have `start_reason` such as `Substitution - On (Tactical)`. Whether the file reflects the lineup known at T-60min is NOT VERIFIED; it is the post-match record (see temporal-integrity note below). |
| Substitution minute (in and out) | **Yes.** `Substitution` events: `period, minute, second`, player off, `substitution.replacement` (player on), `substitution.outcome` (observed `Tactical` 39, `Injury` 2). Lineups `positions[].from/to` carry the same times with `start_reason` / `end_reason` (e.g. `Substitution - Off (Tactical)`). |
| Minutes played derivable | **Yes, with caveats.** Sum the `positions` spans using a period-aware clock. `Player Off` / `Player On` events (observed in 4 of 7 matches) split a player's presence into several spans, so spans must be summed. Time is real elapsed time including stoppage (not capped at 90): e.g. match 3754217 lasted 5,956 s on the period clocks (99.3 min). **Positions do not close at a straight red (next row).** |
| Yellow, second yellow, straight red with minute | **Yes, directly.** Event `foul_committed.card` or `bad_behaviour.card` with `card.name` in `Yellow Card`, `Second Yellow`, `Red Card`, plus `period, minute, second`. The same cards appear in `lineups[].cards[]` as `card_type, time, period, reason`. Observed in match 3754217: `Red Card` (Gabriel, `Bad Behaviour`, period 1, 45:44, after a yellow at 44:54) and `Second Yellow` (Cazorla, `Foul Committed`, period 2, 78:23). Whether the first is a straight red in real life is not stated in the payload; it is labelled `Red Card`. |
| Shot outcome | `shot.outcome.name` observed: `Goal` 19, `Saved` 46, `Off T` 53, `Blocked` 47, `Wayward` 9, `Post` 1, `Saved to Post` 1 (176 shots in 7 matches). A `saved_to_post` flag appears on the `Saved to Post` shot. No on-target field exists: shots on target must be derived from `outcome` (Goal and Saved clearly; `Saved to Post` and `Post` need a convention fixed in Phase 1). |
| Shot xG | **Yes.** `shot.statsbomb_xg` present on all 176 shots (including the penalty: 0.78, and the post shot: 0.054). |
| Penalties | `shot.type.name == "Penalty"` (1 observed: Mahrez, period 1, 24:29, outcome `Goal`, xG 0.7835). Other shot types observed: `Open Play` 169, `Free Kick` 6. |
| Own goals | Two events at the same timestamp: `Own Goal Against` (the player who put it in and his team) and `Own Goal For` (the benefiting team, no player). Observed in 2 of 7 matches. They are not `Shot` events and carry no xG. |
| Goal minute | **Yes.** Shot with outcome `Goal`: `period, minute, second`; own goals as above. Goals from `Goal` shots plus `Own Goal For` events equal the final score in 7 of 7 matches. |
| Shot-level fields | `shot` keys observed: `statsbomb_xg, outcome, type, body_part, technique, end_location, first_time, freeze_frame, key_pass_id, one_on_one, aerial_won, deflected, saved_to_post`. |

**Red-card route (settles D-017 / D-020).** The dismissal minute is recoverable **directly** from the card events (and the duplicate in lineups `cards[]`), as `(period, minute, second)`. Do not derive dismissals from `positions` or `Tactical Shift` lineups:
- Gabriel's `Red Card` (45:44, period 1): his last event in the stream is that card, but his `positions` run to `Final Whistle` (`to: null`; `positions_closed_at_card: false`), derived minutes from positions are 99.3 (the full match), and every later `Tactical Shift` event still lists 11 Arsenal players including him.
- Cazorla's `Second Yellow` (78:23): `positions` close at 78:23 with `end_reason` `Foul Committed (Second Yellow)` (82.0 minutes).
So position data is correct for one dismissal and wrong for the other. **Phase 1 correction (full corpus):** across all 1,517 matches `positions` close at the card for only 2 of 413 dismissals, so the Phase 0b impression that `Second Yellow` closes correctly came from a single match; see D-031. Rule for Phase 1: cap each player's minutes at the card timestamp when a `Red Card` or `Second Yellow` exists. Evidence is two dismissals in one match; Phase 1 must count, over all 1,517 matches, how often positions fail to close at a dismissal.

**Clock hazard for in-play (Pillar B).** The `minute` field restarts at 45 in period 2, while period 1 stoppage runs past it (period 1 ended at 45:xx to 48:38 across the 7 matches; period 2 started at 45:00). So `minute` alone is not monotonic: the same minute value occurs in both halves (e.g. a 45:44 first-half card and a 45:12 second-half event). The in-play state must use `(period, minute, second)` or `timestamp`. Observed period lengths: period 1 ended at 2,759 to 2,918 s and period 2 at 5,584 to 5,738 s on the period clocks.

**Temporal-integrity note.** Everything in these files is a post-match record. The lineup in `lineups/<id>.json` is the realised squad (starters, named bench, who came on). Treating it as "confirmed T-60min lineups" assumes the named 18 equal what was announced; that is plausible but NOT VERIFIED. Minutes, subs and cards are targets, never features, in `opening` and `lineups` states (CLAUDE.md).

### Download size, time and method (D-026)
From the GitHub tree listing (one API request, not truncated, 0 of 3,034 in-scope files missing):
- Events: 4,568,675,058 bytes (4.57 GB) over 1,517 files (mean 3.01 MB). Lineups: 29,133,963 bytes (29.1 MB). Whole repository: 16.13 GB of blobs, of which 1,517 matches' events and lineups are the part needed.
- Requests: 3,034 (events + lineups), plus the five `matches` files and `competitions.json` already cached.
- Time: the 1 request/second throttle alone gives 3,034 s (50.6 min). Measured transfer on 3 timed samples was 2.10 MB/s, giving 2,185 s (36.4 min) of transfer; with each events request taking about 1.4 s and each lineups request throttle-bound at 1 s, the sequential estimate is about 2.4 s per match, roughly 1 hour (range 51 to 62 min). The rate is from 3 samples only and is uncertain.
- Disk: 4.6 GB raw, on a drive with 24 GB free (96% used). Ample but tight; convert to Parquet and keep raw out of any image.
- **Method chosen:** per-file raw fetch through the existing throttled, cached, resumable `CachedFetcher`, in-scope files only. A sparse partial `git clone` was **not tested** (its transfer volume is unmeasured); rejected for consistency with the existing cache, per-file resumability and request-rate control. Open risk: GitHub raw rate limiting over 3,000 requests is unknown; the resumable cache makes a retry cheap. Do not run it before Phase 1.

### Reproduce
```
uv run edgeforge data fetch-statsbomb-scope          # competitions + 4 matches files
uv run edgeforge data fetch-statsbomb-match 3754217  # events + lineups for one match
uv run edgeforge data audit-statsbomb
```

---

## 4. Team-name mapping across sources: flagged for Phase 1

Names differ between football-data and StatsBomb for 2015/16 (`team_names_2015_16` in the StatsBomb audit):

| League | Teams | Identical names | Join on (date, home goals, away goals): unique / ambiguous / none | Teams mapped consistently |
|---|---|---|---|---|
| Premier League | 20 vs 20 | 9 | 298 / 82 / 0 | 20 of 20, 0 conflicts |
| La Liga | 20 vs 20 | 9 | 321 / 59 / 0 | 20 of 20, 0 conflicts |
| Serie A | 20 vs 20 | 16 | 291 / 89 / 0 | 20 of 20, 0 conflicts |
| Ligue 1 | 20 vs 20 | 12 | 310 / 67 / 3 | 20 of 20, 0 conflicts |

Examples: `Man United` vs `Manchester United`, `Ath Madrid` vs `Atlético Madrid`, `Paris SG` vs `Paris Saint-Germain`, `Inter` vs `Inter Milan`. Accents differ (`Málaga`, `Saint-Étienne`). Exact-name joins fail for 4 to 11 teams per league. A date-and-score join is unique for 76 to 85% of matches and recovers a complete, conflict-free name map for every league, so Phase 1 can bootstrap `team_name_map` from unique joins and then join all matches on (date, mapped home, mapped away). Three Ligue 1 football-data matches have no StatsBomb counterpart (377 vs 380). Pre-2015/16 and 2019/20+ football-data seasons will need names for teams not in the 2015/16 StatsBomb set (relegated and promoted clubs), so the map needs a manual extension for those. A manual review of the generated map is required.

---

## 5. Phase 1: full StatsBomb corpus and the warehouse

All numbers below are in `artifacts/metrics/` (`statsbomb_download.json`, `statsbomb_profile.json`, `warehouse_build.json`, `gate1_validation.json`), produced by the commands in section 5.5.

### 5.1 Download (D-026)
3,034 files (1,517 events + 1,517 lineups): 3,020 fetched in this run and 14 already cached. 4,577,103,127 bytes fetched plus 20,705,894 already cached (4.60 GB). 3,024 HTTP requests, 4 retries, 0 failures. The run's wall-clock figure (19,922 s) includes a period when the machine was suspended and the network was down; it is not a download time. The sum of per-request durations was 1,135 s over 3,024 timed requests, and before the suspension the log shows 1,250 matches in 2,494 s (2.0 s per match), so the active time was roughly 50 minutes (derived from that rate, not separately measured after the resume). No rate limiting was observed.

### 5.2 Distinct values in the full corpus (`statsbomb_profile.json`)
- **Shot outcome** (37,888 shots): `Off T` 12,637; `Blocked` 9,472; `Saved` 8,783; `Goal` 3,869; `Wayward` 2,154; `Post` 710; `Saved Off Target` 145; `Saved to Post` 118. All are classified by D-030 (`Saved Off Target` is not on target); none unclassified.
- **Shot type:** `Open Play` 35,732; `Free Kick` 1,749; `Penalty` 400; `Corner` 7.
- **Card name** (events and lineups agree, 0 mismatching matches): `Yellow Card` 6,682; `Second Yellow` 217; `Red Card` 196. Sources: `Foul Committed` and `Bad Behaviour` (a red or second yellow can come from either).
- **Substitution outcome:** `Tactical` 8,041; `Injury` 681. **Event period:** 1 and 2 only (no extra time); `Half End` is present for both periods in every match.
- **Lineup `start_reason`:** `Starting XI`, `Tactical Shift`, `Substitution - On (Tactical|Injury|Off Camera)`, `Player On`, `Player On (Off Camera)`. **`end_reason`** includes `Final Whistle`, `Tactical Shift`, `Substitution - Off (...)`, `Player Off`, `Player Off (Off Camera)`, `Player Off (Permanent)` (15), `Foul Committed (Red Card)` (10), `Foul Committed (Second Yellow)` (5), plus odd values (`Substitution - On (...)` used as an end reason, `Starting XI` used as an end reason 9 times).
- **Positions:** 24 distinct names, all mapped to GK / DEF / MID / FWD by `position_group`.
- **Dismissals:** 413 (196 `Red Card`, 217 `Second Yellow`). Lineup `positions` closed at the card for 2 of them (D-031).

### 5.3 Quirks found
- Within StatsBomb, event team names differ from the match record's names in 20 matches (for example `Marseille` versus `Olympique de Marseille`). All tables key on `team_id` and take names from the match record.
- `positions` spans can be out of period order and can restart a substituted player (D-031).
- Unused bench players can be carded (10 player-matches: 4 red, 6 yellow).
- Players can leave without replacement (`Player Off (Permanent)`) and replacements can arrive late, so a team can play short without a dismissal.
- Own goals are paired events (`Own Goal For` / `Own Goal Against`), balanced in all 1,517 matches.

### 5.4 Tables (DuckDB `data/warehouse.duckdb`, Parquet in `data/processed/`; git-ignored, D-028)
| Table | Rows | Key | Notes |
|---|---|---|---|
| `matches` | 36,539 | `match_id` (`<league>_<season>_<yyyymmdd>_<home>_<away>`) | 99 football-data files, all cached league-seasons; scores, match stats, `kickoff_time` where present (2019/20 onward), `ragged_row` flag; 0 rows dropped for bad dates |
| `odds` | 1,879,580 | `match_id, bookmaker, market, selection, snapshot` | Long format built by column name (`1X2`, `OU` at 2.5, `AH` home line); snapshot `early` / `close`; `line_source` shared / book / bb. 16 price cells invalid (non-numeric or <= 1) dropped; 39 AH price rows without a line dropped; 248 odds rows of the ragged-row seasons skipped |
| `team_name_map` | 80 | `league, fd_name` | Committed as `configs/team_name_map.csv` (names only); bootstrapped from unique (date, score) joins, then reviewed |
| `sb_matches` | 1,517 | `sb_match_id` | Manager ids, period lengths, `match_length_s` |
| `sb_match_link` | 1,517 | `sb_match_id` | Date + mapped names; 3 football-data Ligue 1 matches have no StatsBomb counterpart (named in `gate1_validation.json`) |
| `player_match` | 57,665 | `sb_match_id, player_id` | Fields per D-030/D-031; `minutes_positions` and `started_lineup` kept as cross-checks |
| `shots` | 37,888 | `sb_match_id, event_id` | `elapsed_s` and `(period, minute, second)`, outcome, type, xG, `on_target` |
| `events_timeline` | 4,410 | `sb_match_id, event_index, kind` | goals, own goals (credited team), `red_card`, `second_yellow`; running score |
| `sb_match_checks` | 1,517 | `sb_match_id` | Per-match raw-event reconciliation counts |
| `team_season` | 1,962 | `league, season, team` | `promoted` where the previous season of that league is cached, else null; `manager_change_flag` for the 80 StatsBomb-covered teams only |

Elapsed time is real match seconds on the period-aware clock (period 2 starts where period 1 ended, stoppage included).

### 5.5 Reproduce
```
uv run edgeforge data fetch-statsbomb-all      # about 1 hour, resumable
uv run edgeforge data profile-statsbomb        # stops if a shot outcome is unclassified
uv run edgeforge build-warehouse
uv run edgeforge validate                      # exit 1 if a Gate 1 check fails
```

### 5.6 Not derivable / limits
- **Manager changes** exist only for 2015/16 in the four StatsBomb leagues (80 teams, 38 with more than one manager id, mean per-team share of matches with manager data 0.98). For every other league-season they are unavailable.
- **Promoted flag** is null where the previous season of the same league is not cached.
- **Closing odds** exist for 63.5% of in-scope matches (none before 2012/13); early snapshots cover 99.997% (`matches_have_1x2_odds_in_scope`).
- Single-season player data (2015/16) is a stated limitation everywhere (D-022).
