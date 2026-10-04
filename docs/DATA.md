# Data sources: verified against real payloads

Status as of 2026-10-04 (Phase 0). Every statement below is either (a) read from a payload fetched
in this phase, (b) read from `artifacts/metrics/data_audit_footballdata.json` (produced by
`uv run edgeforge data audit-footballdata`), or (c) explicitly marked **NOT VERIFIED**. Nothing here
comes from documentation or memory unless it is quoted as such and labelled.

| Source | Payload received | Status |
|---|---|---|
| football-data.co.uk | Yes: 62 league-season CSVs cached | Verified (section 2) |
| Understat | **No.** Only `/robots.txt` was requested (2 requests) | **BLOCKED, not verified (section 1)** |

---

## 1. Understat: BLOCKED

### What happened
- Requested `https://understat.com/robots.txt` twice (once to read it, once to confirm the status code). Both returned HTTP 200.
- Body, verbatim (26 bytes):
  ```
  User-agent: *
  Disallow: /
  ```
- That disallows every path for every user agent. D-003 records that the owner accepted scraping despite "no official API and unclear redistribution terms", but it did not record an explicit `Disallow: /`. Per the Phase 0 instruction ("if a data source is blocked or its terms look problematic, stop and report"), **no Understat page, JSON endpoint or API call has been requested**. I did not look for a separate terms-of-service page either, because that would also be a crawl of disallowed paths.
- Nothing was worked around: no alternate user agent, no mirror, no third-party scraper package.

### Consequence: every Understat question is unanswered
| Question | Answer |
|---|---|
| Starter vs substitute flag? | NOT VERIFIED (no payload) |
| Minutes played? | NOT VERIFIED |
| Substitution minute? | NOT VERIFIED |
| Shot minutes? | NOT VERIFIED |
| Own goals? | NOT VERIFIED |
| Yellow / red card flags? | NOT VERIFIED |
| Is the red-card minute recoverable (D-017)? | NOT VERIFIED. See D-020: the route is undetermined |
| Seasons and leagues available, request count, scrape time | NOT VERIFIED |

The fields listed for Understat in D-003 are the plan's expectation, not a finding.

### Decision needed from the owner (see D-021)
1. Override: the owner explicitly accepts local, non-commercial caching despite `Disallow: /` (this supersedes the D-003 constraint wording and carries the ToS risk), or
2. Ask Understat for permission first, or
3. Pick a different player-level source. Candidates have **not** been evaluated: I will not name coverage or fields for any alternative until a payload has been inspected.

---

## 2. football-data.co.uk: verified

### Access
- `robots.txt` (fetched 2026-10-04): `User-agent: *` has an empty `Disallow:` (allow all). It separately disallows a list of named AI crawlers, including `GPTBot`, `ChatGPT-User`, `Google-Extended`, `Anthropic-AI`, `Claude-Web`, `ClaudeBot`, `PerplexityBot`, `CCBot`. The project downloader sends its own user agent (`edgeforge-research/0.1 (personal non-commercial research; local cache only)`), so it falls under `*`. The named-bot entries are aimed at AI crawlers; this is a user-run script that caches CSVs locally for statistical analysis. Flagged for the owner in D-021 because the intent of those entries is not stated on the site.
- Files are plain CSV at `https://www.football-data.co.uk/mmz4281/<season>/<league>.csv` (HTTP 200 for every request made). Season code `2425` = 2024/25. Leagues used: `E0` (Premier League), `D1`, `SP1`, `I1`, `F1`.
- No credentials. Files are UTF-8 with a BOM.
- Site notes (`/notes.txt`, quoted): "Betting odds for weekend games are collected Friday afternoons, and on Tuesday afternoons for midweek games." That sentence sits in the acknowledgements and is not tied to a season.

### Requests made in Phase 0 (this is more than "one league-season")
Column coverage by season cannot be answered from one file, so I cached: E0 for every season 1993/94 to 2026/27 (34 files) and D1/SP1/I1/F1 for 2019/20 to 2025/26 (28 files) = **62 CSV requests**, 1 per second, ~7.9 MB total, all under `data/raw/footballdata/` (git-ignored). Plus roughly 10 small requests to non-CSV pages (`robots.txt`, `notes.txt`, `data.php`, `matches.php`, `englandm.php`, including redirects and a few repeats) to read the column key and robots rules; the exact count was not logged.

### Opening vs closing: what the columns are
- Columns without a `C` are described by the site as "pre-closing" odds; the closing version adds a `C` after the bookmaker code (`PSH` -> `PSCH`). Source: `notes.txt`.
- **There is no timestamp column for odds capture in any file.** The "opening" columns are therefore not verified as true market-open prices and not verified as T-48h. They are "an earlier snapshot, capture time unknown". Pillar A (D-016) and the `opening` information state (D-015) must carry this label. The only evidence in the payload is that open and closing differ: for Pinnacle home-win odds the share of matches with `PSH == PSCH` and the mean absolute difference are, per `data_audit_footballdata.json` (`pinnacle_home_open_vs_close`):

  | E0 season | pairs | share identical | mean abs diff |
  |---|---|---|---|
  | 2012/13 | 380 | 0.066 | 0.179 |
  | 2018/19 | 380 | 0.050 | 0.236 |
  | 2023/24 | 380 | 0.034 | 0.220 |
  | 2024/25 | 380 | 0.024 | 0.194 |
  | 2025/26 | 210 | 0.057 | 0.157 |

### Pinnacle columns by market
| Market | Opening (pre-closing snapshot) | Closing | First E0 season with data |
|---|---|---|---|
| 1X2 | `PSH`, `PSD`, `PSA` | `PSCH`, `PSCD`, `PSCA` | 2012/13 (open and close) |
| O/U 2.5 | `P>2.5`, `P<2.5` | `PC>2.5`, `PC<2.5` | 2019/20 |
| Asian handicap | `PAHH`, `PAHA` (line in `AHh`) | `PCAHH`, `PCAHA` (line in `AHCh`) | 2019/20 |

- The notes also list `PH/PD/PA` as aliases for Pinnacle 1X2. No cached file contains a `PH` column, so only `PS*` is used.
- Asian handicap: the line columns `AHh` and `AHCh` are single columns shared by all bookmakers in the file. **NOT VERIFIED** that Pinnacle's `PAHH/PAHA` are quoted at exactly that line; this needs a check in Phase 1 (e.g. implied two-way margin and line-consistency across bookmakers) before AH is used for Pillar A.
- Before 2019/20, O/U and AH exist only as Betbrain aggregates (`BbAv>2.5`, `BbMx>2.5`, `BbAHh`, `BbAvAHH`, ...), maximum/average, with no closing version. They are not usable for opening-vs-closing.
- Other bookmakers with both open and closing 1X2 columns (E0, from the audit's `closing_bookmakers_1x2`): 2012/13 to 2018/19: `PS` only. 2019/20: `Avg, B365, BW, IW, Max, PS, VC, WH`. 2024/25: `1XB, Avg, B365, BF, BFE, BW, Max, PS, WH`. 2025/26: `Avg, B365, BFD, BFE, BMGM, BV, BW, CL, LB, Max, PS`. For O/U closing in 2024/25: `B365, P, Max, Avg, BFE`; for AH closing: `B365, P, Max, Avg, BFE`.
- `Max*` and `Avg*` are market maximum/average across the bookmakers contributing to that file (definitions in `notes.txt`; the number of contributing bookmakers is not in the payload).

### Coverage (matches with Pinnacle data)
Counts of rows with a non-empty value, from `data_audit_footballdata.json`. "n" is parsed match rows.

| League-season | n | Pinnacle 1X2 open | 1X2 close | O/U open | O/U close | AH open | AH close |
|---|---|---|---|---|---|---|---|
| E0 2019/20 to 2024/25 | 380 each | 380 | 380 | 372 to 380 | 373 to 380 | 380 | 380 |
| D1, F1, I1, SP1 2019/20 to 2024/25 | 279 to 380 | at most 2 missing | complete | up to 14 missing (D1 2022/23, 2023/24) | up to 13 missing (D1 2023/24) | up to 2 missing | complete |
| E0 2025/26 | 380 | **210** | **210** | 210 | 210 | 210 | 210 |
| D1 / F1 / I1 / SP1 2025/26 | 306 / 306 / 380 / 380 | 150 / 153 / 200 / 189 | 149 / 153 / 198 / 188 | 142 / 152 / 200 / 188 | 142 / 152 / 198 / 189 | 150 / 153 / 200 / 189 | 149 / 153 / 198 / 189 |

The ranges in the first two rows are summaries of the per-file values in the JSON; read the JSON for exact counts.

**Pinnacle disappears mid-2025/26.** In the E0 2025/26 file the last match with a `PSH` value is 2026-01-08 (`pinnacle_1x2_last_date`); matches from 2026-01-17 onward have none. The 2026/27 E0 file (50 matches, to 2026-09-20) has no Pinnacle columns. Market `Avg*` and `Max*` closing columns remain fully populated in 2025/26 (`AvgCH`: 380/380). The cause is not in the payload and is unknown.

### Match-level fields relevant to other phases
- Present (E0 2024/25): `FTHG, FTAG, FTR, HTHG, HTAG, HTR, Referee, HS, AS, HST, AST, HF, AF, HC, AC, HY, AY, HR, AR`.
- `HR` / `AR` are red-card **counts per team per match**, populated in E0 from 2000/01 onward (first season with non-empty `HR` in the cached E0 files). They contain **no minute**. They are useful only to cross-check a red-card timeline built elsewhere (D-020).
- No goal minutes, no lineups, no player-level data, no kick-off time before 2019/20 (`Time` column first appears in E0 2019/20).

### Parsing hazards found (Phase 1 must handle)
- **Ragged rows**: E0 1993/94 and 1994/95 (79 rows each) and 2003/04 and 2004/05 (45 each) have rows whose field count differs from the header. A naive reader drops or shifts them. These seasons are outside the recommended range.
- Blank separator rows (`,,,,`) in 1993/94-era files.
- Date format changes: `dd/mm/yy` before 2019/20, `dd/mm/yyyy` from 2019/20.
- Column sets change year to year (106 columns in 2019/20, 120 in 2024/25, 132 in 2025/26), so the odds table must be built by column name, not position.
- Team names are free text and differ from other sources; `team_name_map` is required in Phase 1.

---

## 3. Recommended scope (football-data side only)

The final season range also depends on Understat/player-data coverage, which is unverified. See D-019 for the conditional decision.

- **Leagues:** E0, D1, SP1, I1, F1 (the same five as the D-003 "top five"). All five show the same column layout and comparable Pinnacle coverage for 2019/20 to 2024/25.
- **Seasons for Pillar A with opening and closing on all three markets:** 2019/20 to 2024/25 (6 complete seasons x 5 leagues = 30 league-seasons). 1X2 alone has Pinnacle open and close from 2012/13 (E0 verified; other leagues not checked before 2019/20).
- **2025/26:** include, but flag it: Pinnacle is present only until 2026-01-08. Market `Avg/Max` closing columns are complete. It is the natural test season for walk-forward if player data allows, in which case Pinnacle-based comparisons on it use the matches that have it.
- **Request count (football-data):** 35 CSVs for 5 leagues x 2019/20 to 2025/26, at 1 request/second = about 35 seconds. Already cached locally in this phase. Rerun: `uv run edgeforge data fetch-footballdata E0,D1,SP1,I1,F1 1920,2021,2122,2223,2324,2425,2526`.
- **Understat request count and time:** NOT VERIFIED.

## 4. Reproduce
```
uv run edgeforge data fetch-footballdata-proof      # one league-season (E0 2024/25)
uv run edgeforge data fetch-footballdata E0,D1,SP1,I1,F1 1920,2021,2122,2223,2324,2425,2526
uv run edgeforge data audit-footballdata            # writes artifacts/metrics/data_audit_footballdata.json
```
Retrieval date for all football-data payloads: 2026-10-04. Licence/terms: no licence statement was found in the files or notes; redistribution is not assumed (raw data is never committed).
