"""Point-in-time feature builder (SQL on DuckDB).

Every feature row carries `asof_ts` and an information-state tag:
  opening  asof = kickoff - 48h, no lineups
  lineups  asof = kickoff - 60 min, announced XI and named bench known (D-029)
  inplay   asof = kickoff + t, events strictly before elapsed second t only

A prior match may feed a feature only if it was *complete* by asof_ts: its latest possible
kickoff plus RESULT_DELAY_H hours must not exceed asof_ts. This is stricter than CLAUDE.md's
"kickoff strictly before asof_ts" and avoids using a match still in play. Where a match has no
kick-off time (football-data before 2019/20) it is treated as kicking off at 00:00 when it is the
target (earliest) and at 23:59:59 when it is history (latest), so uncertainty never leaks forward.

Minutes, substitutions, cards and every other post-kickoff statistic of the target match are
targets; they never appear as features in the opening or lineups state.
"""

from typing import Any

import duckdb
import numpy as np
import pandas as pd

STATES = ("opening", "lineups", "inplay")
OPENING_OFFSET = pd.Timedelta(hours=48)
LINEUPS_OFFSET = pd.Timedelta(minutes=60)
RESULT_DELAY = pd.Timedelta(hours=3)
END_OF_DAY = pd.Timedelta(hours=23, minutes=59, seconds=59)
ROLLING_WINDOWS = (5, 10, 1000)  # 1000 = all prior matches

# Columns that must never be features in the opening/lineups states (post-kickoff information).
FORBIDDEN_FEATURE_COLUMNS = frozenset(
    {
        "minutes",
        "minutes_positions",
        "sub_on_s",
        "sub_off_s",
        "gap_minutes",
        "vacancy_s",
        "exit_kind",
        "exit_s",
        "appeared",
        "yellow",
        "second_yellow",
        "red",
        "shots",
        "shots_on_target",
        "goals",
        "xg",
        "own_goals",
        "penalties_taken",
        "penalty_goals",
        "fthg",
        "ftag",
        "ftr",
        "hthg",
        "htag",
        "hs",
        "as_",
        "hst",
        "ast",
        "hc",
        "ac",
        "hf",
        "af",
        "hy",
        "ay",
        "hr",
        "ar",
    }
)


def monday_floor(ts: pd.Series) -> pd.Series:
    """Most recent Monday 00:00 at or before each timestamp (weekly refit cutoff, D-033)."""
    day = ts.dt.normalize()
    return day - pd.to_timedelta(day.dt.weekday, unit="D")  # type: ignore[no-any-return]


def fd_clock(matches: pd.DataFrame) -> pd.DataFrame:
    """Add kickoff bounds, completion time and as-of times to football-data matches."""
    out = matches.copy()
    date = pd.to_datetime(out["date"])
    ko = pd.to_datetime(out["kickoff_local"])
    out["ko_early"] = ko.fillna(date)
    out["ko_late"] = ko.fillna(date + END_OF_DAY)
    out["done_ts"] = out["ko_late"] + RESULT_DELAY
    out["asof_opening"] = out["ko_early"] - OPENING_OFFSET
    out["asof_lineups"] = out["ko_early"] - LINEUPS_OFFSET
    out["model_cutoff"] = monday_floor(out["asof_opening"])
    return out


def sb_clock(sb_matches: pd.DataFrame) -> pd.DataFrame:
    """Same clocks for StatsBomb matches (match_date + kick_off 'HH:MM:SS.mmm', local time)."""
    out = sb_matches.copy()
    date = pd.to_datetime(out["match_date"])
    t = pd.to_timedelta(out["kick_off"].fillna(""), errors="coerce")
    ko = date + t
    out["kickoff_early"] = ko.fillna(date)
    out["kickoff_late"] = ko.fillna(date + END_OF_DAY)
    out["done_ts"] = out["kickoff_late"] + RESULT_DELAY
    out["asof_opening"] = out["kickoff_early"] - OPENING_OFFSET
    out["asof_lineups"] = out["kickoff_early"] - LINEUPS_OFFSET
    out["match_length_s"] = (
        sb_matches["match_length_s"] if "match_length_s" in sb_matches else np.nan
    )
    keep = [
        "sb_match_id",
        "league",
        "match_length_s",
        "match_week",
        "kickoff_early",
        "kickoff_late",
        "done_ts",
        "asof_opening",
        "asof_lineups",
    ]
    return out[keep]


def eligible_history(df: pd.DataFrame, cutoff: pd.Timestamp, window_days: int) -> pd.DataFrame:
    """Matches usable at `cutoff`: completed by it and no older than the window."""
    mask = (df["done_ts"] <= cutoff) & (df["ko_late"] >= cutoff - pd.Timedelta(days=window_days))
    return df[mask]


def register_clocks(
    con: duckdb.DuckDBPyConnection, fd: pd.DataFrame | None, sb: pd.DataFrame | None
) -> None:
    """Expose clock tables to SQL as views `fd_clock` and `sb_clock`."""
    if fd is not None:
        con.register("fd_clock", fd)
    if sb is not None:
        con.register("sb_clock", sb)


# ----------------------------------------------------------------------------- team / league


def league_frequency_features(
    con: duckdb.DuckDBPyConnection, targets: pd.DataFrame, window_days: int = 1095
) -> pd.DataFrame:
    """League-average outcome frequencies as of each target's `cutoff_ts` (opening state).

    `targets`: match_id, league, cutoff_ts. Uses `fd_clock` rows completed by cutoff_ts.
    """
    con.register("tgt_league", targets)
    sql = f"""
    SELECT t.match_id, t.cutoff_ts AS asof_ts, 'opening' AS state, count(h.match_id) AS n_prior,
           avg((h.fthg > h.ftag)::INT) AS home_win, avg((h.fthg = h.ftag)::INT) AS draw,
           avg((h.fthg < h.ftag)::INT) AS away_win,
           avg((h.fthg + h.ftag > 0)::INT) AS over_0_5, avg((h.fthg + h.ftag > 1)::INT) AS over_1_5,
           avg((h.fthg + h.ftag > 2)::INT) AS over_2_5, avg((h.fthg + h.ftag > 3)::INT) AS over_3_5,
           avg((h.fthg + h.ftag > 4)::INT) AS over_4_5,
           avg((h.fthg > 0 AND h.ftag > 0)::INT) AS btts
    FROM tgt_league t
    LEFT JOIN fd_clock h
      ON h.league = t.league AND h.fthg IS NOT NULL
     AND h.done_ts <= t.cutoff_ts
     AND h.ko_late >= t.cutoff_ts - INTERVAL {int(window_days)} DAY
    GROUP BY t.match_id, t.cutoff_ts
    """
    return con.execute(sql).df()


def team_form_features(
    con: duckdb.DuckDBPyConnection, targets: pd.DataFrame, window_days: int = 365
) -> pd.DataFrame:
    """Prior goals for/against of both teams as of `asof_ts` (opening or lineups state).

    `targets`: match_id, league, home, away, asof_ts, state.
    """
    con.register("tgt_team", targets)
    long = """
      SELECT league, home AS team, fthg AS gf, ftag AS ga, ko_late, done_ts FROM fd_clock
      WHERE fthg IS NOT NULL
      UNION ALL
      SELECT league, away AS team, ftag AS gf, fthg AS ga, ko_late, done_ts FROM fd_clock
      WHERE fthg IS NOT NULL
    """

    def side(col: str, prefix: str) -> str:
        return f"""
        SELECT t.match_id, count(h.team) AS {prefix}_n_prior,
               coalesce(sum(h.gf), 0) AS {prefix}_gf, coalesce(sum(h.ga), 0) AS {prefix}_ga
        FROM tgt_team t
        LEFT JOIN ({long}) h
          ON h.league = t.league AND h.team = t.{col}
         AND h.done_ts <= t.asof_ts AND h.ko_late >= t.asof_ts - INTERVAL {int(window_days)} DAY
        GROUP BY t.match_id
        """

    sql = f"""
    SELECT t.match_id, t.asof_ts, t.state, hs.home_n_prior, hs.home_gf, hs.home_ga,
           aw.away_n_prior, aw.away_gf, aw.away_ga
    FROM tgt_team t JOIN ({side("home", "home")}) hs USING (match_id)
    JOIN ({side("away", "away")}) aw USING (match_id)
    """
    return con.execute(sql).df()


# ----------------------------------------------------------------------------- player


def _agg_columns() -> str:
    cols = []
    for n in ROLLING_WINDOWS:
        f = f"FILTER (WHERE j.rn <= {n})"
        cols += [
            f"count(j.rn) {f} AS n_squad_{n}",
            f"coalesce(sum(j.appeared::INT) {f}, 0) AS n_app_{n}",
            f"coalesce(sum(j.started::INT) {f}, 0) AS n_start_{n}",
            f"coalesce(sum(j.minutes) {f}, 0) AS mins_{n}",
            f"coalesce(sum(j.shots) {f}, 0) AS shots_{n}",
            f"coalesce(sum(j.sot) {f}, 0) AS sot_{n}",
            f"coalesce(sum(j.goals) {f}, 0) AS goals_{n}",
            f"coalesce(sum(j.minutes) FILTER (WHERE j.rn <= {n} AND j.started), 0)"
            f" AS mins_started_{n}",
            f"coalesce(sum(j.minutes)"
            f" FILTER (WHERE j.rn <= {n} AND NOT j.started AND j.appeared), 0)"
            f" AS mins_bench_app_{n}",
            f"count(j.rn) FILTER (WHERE j.rn <= {n} AND NOT j.started AND j.appeared)"
            f" AS n_bench_app_{n}",
            f"coalesce(sum(j.xg) {f}, 0) AS xg_{n}",
            f"coalesce(sum(j.pen) {f}, 0) AS pen_{n}",
            f"coalesce(sum(j.expo) {f}, 0) AS expo_{n}",
            f"count(j.rn) FILTER (WHERE j.rn <= {n} AND j.started AND j.exit_kind = 'substitution')"
            f" AS n_subbed_off_{n}",
        ]
    return ",\n           ".join(cols)


def player_features(
    con: duckdb.DuckDBPyConnection,
    state: str,
    target_match_ids: list[int],
    exclude_match_ids: list[int] | None = None,
) -> pd.DataFrame:
    """Player rolling features as of the state's asof_ts. Needs views player_match and sb_clock.

    lineups: targets are the announced squad (XI + named bench) of each match; adds
             `announced_starter`. opening: targets are players who appeared for the team in any of
             its previous 5 matches; no lineup information. History never includes the target match
             (it completes after asof_ts) nor any `exclude_match_ids` (used to keep test-window
             matches out of tuning).
    """
    if state not in ("opening", "lineups"):
        raise ValueError("player_features supports the opening and lineups states")
    con.register("tgt_ids", pd.DataFrame({"id": target_match_ids}))
    con.register("exc_ids", pd.DataFrame({"id": list(exclude_match_ids or [])}, dtype="int64"))
    asof = "asof_lineups" if state == "lineups" else "asof_opening"
    if state == "lineups":
        tgt = f"""
        SELECT p.sb_match_id, p.player_id, p.team_id, p.started AS announced_starter,
               c.league, c.{asof} AS asof_ts
        FROM player_match p JOIN sb_clock c USING (sb_match_id)
        WHERE p.sb_match_id IN (SELECT id FROM tgt_ids)
        """
        extra = "t.announced_starter,"
    else:
        tgt = f"""
        WITH tm AS (
          SELECT DISTINCT p.team_id, p.sb_match_id, c.kickoff_late, c.done_ts
          FROM player_match p JOIN sb_clock c USING (sb_match_id)
          WHERE p.sb_match_id NOT IN (SELECT id FROM exc_ids)
        ), tt AS (
          SELECT DISTINCT p.team_id, p.sb_match_id, c.league, c.{asof} AS asof_ts
          FROM player_match p JOIN sb_clock c USING (sb_match_id)
          WHERE p.sb_match_id IN (SELECT id FROM tgt_ids)
        ), last5 AS (
          SELECT tt.sb_match_id, tt.team_id, tt.league, tt.asof_ts, tm.sb_match_id AS prev,
                 row_number() OVER (PARTITION BY tt.sb_match_id, tt.team_id
                                    ORDER BY tm.kickoff_late DESC) AS rn
          FROM tt JOIN tm ON tm.team_id = tt.team_id AND tm.done_ts <= tt.asof_ts
        )
        SELECT DISTINCT l.sb_match_id, p.player_id, l.team_id, l.league, l.asof_ts
        FROM last5 l JOIN player_match p
          ON p.sb_match_id = l.prev AND p.team_id = l.team_id AND p.appeared
        WHERE l.rn <= 5
        """
        extra = ""
    sql = f"""
    WITH tgt AS ({tgt}),
    hist AS (
      SELECT p.player_id, p.sb_match_id AS h_match, c.kickoff_late AS h_ko, c.done_ts AS h_done,
             p.started, p.appeared, p.minutes, p.shots, p.shots_on_target AS sot, p.goals,
             p.position_group, p.xg, p.penalties_taken AS pen, p.exit_kind,
             sum(p.shots) OVER (PARTITION BY p.sb_match_id, p.team_id) * p.minutes
               / (11.0 * c.match_length_s / 60.0) AS expo
      FROM player_match p JOIN sb_clock c USING (sb_match_id)
      WHERE p.sb_match_id NOT IN (SELECT id FROM exc_ids)
    ),
    j AS (
      SELECT t.sb_match_id, t.player_id, h.*,
             row_number() OVER (PARTITION BY t.sb_match_id, t.player_id
                                ORDER BY h.h_ko DESC) AS rn
      FROM tgt t JOIN hist h ON h.player_id = t.player_id AND h.h_done <= t.asof_ts
    )
    SELECT t.sb_match_id, t.player_id, t.team_id, t.league, {extra}
           t.asof_ts, '{state}' AS state,
           mode(j.position_group) FILTER (WHERE j.appeared) AS position_group,
           {_agg_columns()}
    FROM tgt t LEFT JOIN j ON j.sb_match_id = t.sb_match_id AND j.player_id = t.player_id
    GROUP BY ALL
    """
    return con.execute(sql).df()


def starter_features(
    con: duckdb.DuckDBPyConnection,
    target_match_ids: list[int],
    exclude_match_ids: list[int] | None = None,
) -> pd.DataFrame:
    """Opening-state recency and congestion features for start/bench/out candidates.

    Candidates are players who appeared in the team's previous five matches. For each of those
    five team matches (t1 = most recent) the frame carries whether the candidate was in the squad,
    started, and his minutes (0 when absent), plus the team's days since its last match and its
    match count over the last 14 and 28 days. Everything is as of `asof_opening`; the target match
    and anything completing after `asof_ts` is never read.
    """
    con.register("tgt_ids", pd.DataFrame({"id": target_match_ids}))
    con.register("exc_ids", pd.DataFrame({"id": list(exclude_match_ids or [])}, dtype="int64"))
    per = []
    for k in range(1, 6):
        per += [
            f"max(CASE WHEN l.rn = {k} THEN pm.player_id IS NOT NULL END)::INT AS squad_t{k}",
            f"coalesce(max(CASE WHEN l.rn = {k} THEN pm.started::INT END), 0) AS started_t{k}",
            f"coalesce(max(CASE WHEN l.rn = {k} THEN pm.minutes END), 0) AS mins_t{k}",
        ]
    sql = f"""
    WITH tm AS (
      SELECT DISTINCT p.team_id, p.sb_match_id, c.kickoff_late, c.done_ts
      FROM player_match p JOIN sb_clock c USING (sb_match_id)
      WHERE p.sb_match_id NOT IN (SELECT id FROM exc_ids)
    ), tt AS (
      SELECT p.team_id, p.sb_match_id, bool_or(p.is_home) AS is_home, c.league,
             c.asof_opening AS asof_ts
      FROM player_match p JOIN sb_clock c USING (sb_match_id)
      WHERE p.sb_match_id IN (SELECT id FROM tgt_ids)
      GROUP BY p.team_id, p.sb_match_id, c.league, c.asof_opening
    ), l AS (
      SELECT tt.sb_match_id, tt.team_id, tt.asof_ts, tm.sb_match_id AS prev,
             tm.kickoff_late AS prev_ko,
             row_number() OVER (PARTITION BY tt.sb_match_id, tt.team_id
                                ORDER BY tm.kickoff_late DESC) AS rn
      FROM tt JOIN tm ON tm.team_id = tt.team_id AND tm.done_ts <= tt.asof_ts
    ), ctx AS (
      SELECT sb_match_id, team_id,
             date_diff('minute', max(prev_ko), any_value(asof_ts)) / 1440.0 AS days_since_team_last,
             count(*) FILTER (WHERE prev_ko >= asof_ts - INTERVAL 14 DAY) AS team_matches_14d,
             count(*) FILTER (WHERE prev_ko >= asof_ts - INTERVAL 28 DAY) AS team_matches_28d,
             least(count(*), 5) AS n_team_prior
      FROM l GROUP BY sb_match_id, team_id
    ), cand AS (
      SELECT DISTINCT l.sb_match_id, l.team_id, p.player_id
      FROM l JOIN player_match p
        ON p.sb_match_id = l.prev AND p.team_id = l.team_id AND p.appeared
      WHERE l.rn <= 5
    )
    SELECT c.sb_match_id, c.player_id, c.team_id, tt.asof_ts, 'opening' AS state, tt.is_home,
           ctx.days_since_team_last, ctx.team_matches_14d, ctx.team_matches_28d, ctx.n_team_prior,
           {", ".join(per)}
    FROM cand c
    JOIN tt ON tt.sb_match_id = c.sb_match_id AND tt.team_id = c.team_id
    JOIN ctx ON ctx.sb_match_id = c.sb_match_id AND ctx.team_id = c.team_id
    JOIN l ON l.sb_match_id = c.sb_match_id AND l.team_id = c.team_id AND l.rn <= 5
    LEFT JOIN player_match pm
      ON pm.sb_match_id = l.prev AND pm.team_id = l.team_id AND pm.player_id = c.player_id
    GROUP BY ALL
    """
    return con.execute(sql).df()


def position_group_rates(
    con: duckdb.DuckDBPyConnection,
    state: str,
    target_match_ids: list[int],
    exclude_match_ids: list[int] | None = None,
) -> pd.DataFrame:
    """Prior league-wide totals by position group (appearances only) as of each match's asof_ts."""
    con.register("tgt_ids", pd.DataFrame({"id": target_match_ids}))
    con.register("exc_ids", pd.DataFrame({"id": list(exclude_match_ids or [])}, dtype="int64"))
    asof = "asof_lineups" if state == "lineups" else "asof_opening"
    sql = f"""
    WITH g AS (
      SELECT p.sb_match_id, c.league, c.done_ts, p.position_group,
             sum(p.minutes) AS minutes, sum(p.shots) AS shots, sum(p.shots_on_target) AS sot,
             sum(p.goals) AS goals,
             sum(p.minutes) FILTER (WHERE p.started) AS mins_started,
             count(*) FILTER (WHERE p.started) AS n_started,
             sum(p.minutes) FILTER (WHERE NOT p.started) AS mins_bench_app,
             count(*) FILTER (WHERE NOT p.started) AS n_bench_app,
             sum(p.xg) AS xg, sum(p.penalties_taken) AS pen,
             sum(tsh.tshots * p.minutes / (11.0 * c.match_length_s / 60.0)) AS expo,
             count(*) FILTER (WHERE p.started AND p.exit_kind = 'substitution') AS n_subbed
      FROM player_match p JOIN sb_clock c USING (sb_match_id)
      JOIN (SELECT sb_match_id, team_id, sum(shots) AS tshots FROM player_match
            GROUP BY sb_match_id, team_id) tsh
        ON tsh.sb_match_id = p.sb_match_id AND tsh.team_id = p.team_id
      WHERE p.appeared AND p.position_group IS NOT NULL
        AND p.sb_match_id NOT IN (SELECT id FROM exc_ids)
      GROUP BY ALL
    ), t AS (
      SELECT sb_match_id, league, {asof} AS asof_ts FROM sb_clock
      WHERE sb_match_id IN (SELECT id FROM tgt_ids)
    )
    SELECT t.sb_match_id, t.asof_ts, '{state}' AS state, g.position_group,
           sum(g.minutes) AS g_minutes, sum(g.shots) AS g_shots, sum(g.sot) AS g_sot,
           sum(g.goals) AS g_goals, coalesce(sum(g.mins_started), 0) AS g_mins_started,
           coalesce(sum(g.n_started), 0) AS g_n_started,
           coalesce(sum(g.mins_bench_app), 0) AS g_mins_bench_app,
           coalesce(sum(g.n_bench_app), 0) AS g_n_bench_app,
           coalesce(sum(g.xg), 0) AS g_xg, coalesce(sum(g.pen), 0) AS g_pen,
           coalesce(sum(g.expo), 0) AS g_expo, coalesce(sum(g.n_subbed), 0) AS g_n_subbed
    FROM t JOIN g ON g.league = t.league AND g.done_ts <= t.asof_ts
    GROUP BY t.sb_match_id, t.asof_ts, g.position_group
    """
    return con.execute(sql).df()


# ----------------------------------------------------------------------------- in-play


def inplay_state(con: duckdb.DuckDBPyConnection, sb_match_id: int, t: int) -> dict[str, Any]:
    """Score and dismissals at elapsed second t, using only events strictly before t.

    Needs the `events_timeline` and `sb_matches` tables.
    """
    row = con.execute(
        """
        SELECT
          coalesce(sum(CASE WHEN e.kind IN ('goal', 'own_goal') AND e.team = m.home THEN 1 END), 0),
          coalesce(sum(CASE WHEN e.kind IN ('goal', 'own_goal') AND e.team = m.away THEN 1 END), 0),
          coalesce(sum(CASE WHEN e.kind IN ('red_card', 'second_yellow') AND e.team = m.home
                            THEN 1 END), 0),
          coalesce(sum(CASE WHEN e.kind IN ('red_card', 'second_yellow') AND e.team = m.away
                            THEN 1 END), 0)
        FROM sb_matches m
        LEFT JOIN events_timeline e ON e.sb_match_id = m.sb_match_id AND e.elapsed_s < ?
        WHERE m.sb_match_id = ?
        """,
        [int(t), int(sb_match_id)],
    ).fetchone()
    assert row is not None
    return {
        "sb_match_id": sb_match_id,
        "elapsed_s": int(t),
        "state": "inplay",
        "home_goals": int(row[0]),
        "away_goals": int(row[1]),
        "home_dismissals": int(row[2]),
        "away_dismissals": int(row[3]),
    }


def feature_columns(df: pd.DataFrame) -> list[str]:
    """Columns of a feature frame that are neither keys nor tags."""
    keys = {"sb_match_id", "match_id", "player_id", "team_id", "league", "asof_ts", "state"}
    return [c for c in df.columns if c not in keys]


def assert_no_forbidden(df: pd.DataFrame) -> None:
    bad = sorted(set(feature_columns(df)) & FORBIDDEN_FEATURE_COLUMNS)
    if bad:
        raise AssertionError(f"post-kickoff columns in a pre-kickoff feature frame: {bad}")


def rolling_names() -> list[str]:
    return [
        f"{s}_{n}" for n in ROLLING_WINDOWS for s in ("n_squad", "mins", "shots", "sot", "goals")
    ]


__all__ = [
    "STATES",
    "FORBIDDEN_FEATURE_COLUMNS",
    "assert_no_forbidden",
    "eligible_history",
    "fd_clock",
    "feature_columns",
    "inplay_state",
    "league_frequency_features",
    "monday_floor",
    "player_features",
    "position_group_rates",
    "register_clocks",
    "sb_clock",
    "team_form_features",
]
