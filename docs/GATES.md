# Gate Reports

Appended by Claude Code at the end of each phase.

---

## Gate 0: Setup and data verification

**Report date:** 2026-10-04. **Verdict by implementer: NOT MET. Blocked on Understat (D-021). Stopped for owner and lead.**

### What was built
- Repo skeleton per PLAN Phase 0, `pyproject.toml` (uv, ruff, mypy strict, pytest), typer CLI (`edgeforge version`, `data fetch-footballdata-proof`, `data fetch-footballdata`, `data audit-footballdata`), `.gitignore`, `.env.example`, `configs/data.yaml`.
- `edgeforge.data.http.CachedFetcher`: 1 req/s throttle, every response cached on disk, cache hits cost no request.
- `edgeforge.data.footballdata` downloader and `edgeforge.data.audit` (reads only cached payloads; writes `artifacts/metrics/data_audit_footballdata.json` with `git_sha`, `data_version`, `command`, `config_hash`, `created_at`, `seed`).
- GitHub Actions `ci.yml` (`uv sync --locked`, ruff check, ruff format --check, mypy, pytest); tests use synthetic data only.
- `docs/DATA.md` (football-data verified; Understat explicitly marked not verified), D-019, D-020, D-021 in `docs/DECISIONS.md`.
- No Understat scraper was written: it would be unusable without resolving D-021.

### Gate criteria
| # | Criterion | Result | Proof command |
|---|---|---|---|
| 1 | CLI runs | **PASS** | `uv run edgeforge --help` and `uv run edgeforge version` (prints `0.1.0`) |
| 2 | CI runs | **PARTIAL** | The workflow file exists and every command it runs passes in a fresh `git clone` of the repo: `uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q` (7 tests pass, mypy clean on 19 source files). **It has not run on GitHub**: there is no remote and pushing needs Varun's say-so. |
| 3a | One real league-season from football-data cached | **PASS** | `uv run edgeforge data fetch-footballdata-proof` (E0 2024/25, 197,110 bytes, 380 matches, HTTP 200); `git check-ignore -v data/raw/footballdata/E0_2425.*.body` confirms it is ignored |
| 3b | One real league-season from Understat cached | **FAIL (blocked)** | `curl -s https://understat.com/robots.txt` returns `User-agent: *` / `Disallow: /`. No data request was made. |
| 4a | DATA.md verified against real payloads: football-data | **PASS** | `uv run edgeforge data audit-footballdata`; every number in DATA.md section 2 traces to `artifacts/metrics/data_audit_footballdata.json` |
| 4b | DATA.md verified: Understat (starter flag, minutes, sub minute, shot minutes, own goals, card flags, red-card minute) | **FAIL (not verifiable)** | All marked NOT VERIFIED; no payload exists |
| 5 | Season range logged as a decision | **PARTIAL** | D-019, status Proposed: football-data side verified; final range conditional on player data |
| 6 | Red-card data route logged as a decision | **PARTIAL** | D-020, status Open: football-data has red-card counts only (`HR`/`AR`), no minutes; Understat route undetermined |

### Findings the lead should know
1. **Understat `robots.txt` disallows everything** (D-021). Owner decision required before any player-level data work.
2. **football-data `robots.txt`** allows `*` but names `Anthropic-AI`, `Claude-Web`, `ClaudeBot` and other AI crawlers as disallowed. The project downloader uses its own user agent. This reading is flagged in D-021 for owner veto.
3. **"Opening" odds are not verified as opening.** The non-`C` columns are a pre-closing snapshot with no capture timestamp. Pillar A and the `opening` state need this label (D-019).
4. **Pinnacle vanishes mid-2025/26** (last Pinnacle match 2026-01-08 in E0; none in 2026/27). Cause unknown.
5. Pinnacle O/U 2.5 and AH open+close exist only from 2019/20 (E0). 1X2 open+close from 2012/13.
6. Asian-handicap line columns (`AHh`, `AHCh`) are single columns shared across bookmakers; that Pinnacle's prices are at that line is not verified.
7. Ragged rows in E0 1993/94, 1994/95, 2003/04, 2004/05 (outside the recommended range).

### Deviations from plan
- Fetched 62 football-data CSVs rather than one league-season, because the opening/closing and season-start questions cannot be answered from one file (DATA.md section 2). About 7.9 MB, throttled at 1 req/s, cached and git-ignored.
- Process bugs found and fixed during the phase: an unanchored `.gitignore` rule `data/` excluded the `src/edgeforge/data` package from the first commit, and `ruff` skipped that package while it was ignored. Fixed in commits `cb6f25a` and `ab0dcd5`; the fresh-clone check above ran after both fixes.

### Open issues
- D-021 (owner): how to proceed on player-level data.
- CI has not run on GitHub (no remote).
- Phase 1 must not start until the lead passes this gate.
