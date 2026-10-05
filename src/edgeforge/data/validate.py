"""Gate 1 validation: every check reads the DuckDB warehouse and is written to one metrics JSON.

Thresholds marked 'implementer-chosen' were not fixed by PLAN/DECISIONS and are flagged for the
lead. A check with pass=None is informational.
"""

import json
import logging
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from edgeforge.config import load_config, resolve_path
from edgeforge.data.team_season import in_scope
from edgeforge.provenance import dir_digest, provenance

log = logging.getLogger(__name__)

LIST_LIMIT = 100


def _records(df: pd.DataFrame, limit: int = LIST_LIMIT) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = json.loads(
        df.head(limit).to_json(orient="records", date_format="iso")
    )
    return out


def _check(
    cid: str,
    description: str,
    passed: bool | None,
    threshold: str,
    observed: dict[str, Any],
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": cid,
        "description": description,
        "pass": passed,
        "threshold": threshold,
        "observed": observed,
        "details": details or {},
    }


def check_row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    per_fd = con.execute(
        "SELECT league, season, count(*) AS matches FROM matches GROUP BY ALL ORDER BY ALL"
    ).df()
    odds = con.execute(
        "SELECT m.league, m.season, count(*) AS odds_rows FROM odds o JOIN matches m USING (match_id)"
        " GROUP BY ALL ORDER BY ALL"
    ).df()
    per_fd = per_fd.merge(odds, on=["league", "season"], how="left")
    sb = con.execute(
        "SELECT competition, season, count(*) AS matches FROM sb_matches GROUP BY ALL ORDER BY ALL"
    ).df()
    totals = {
        t: con.execute(f"SELECT count(*) FROM {t}").df().iloc[0].tolist()[0]
        for t in (
            "matches",
            "odds",
            "team_name_map",
            "sb_matches",
            "sb_match_link",
            "player_match",
            "shots",
            "events_timeline",
            "team_season",
        )
    }
    return _check(
        "row_counts",
        "Row counts per league-season and per source",
        None,
        "informational",
        {"table_totals": totals, "statsbomb_matches": _records(sb)},
        {"football_data_per_league_season": _records(per_fd, 10_000)},
    )


def check_odds_join(con: duckdb.DuckDBPyConnection, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    orphan = con.execute(
        "SELECT o.match_id, count(*) AS rows FROM odds o LEFT JOIN matches m USING (match_id)"
        " WHERE m.match_id IS NULL GROUP BY ALL"
    ).df()
    n_odds = con.execute("SELECT count(*) FROM odds").df().iloc[0].tolist()[0]
    orphan_rows = int(orphan["rows"].sum()) if len(orphan) else 0
    c1 = _check(
        "odds_rows_join_to_match",
        "Share of odds rows whose match_id exists in matches",
        (n_odds - orphan_rows) / n_odds >= 0.99,
        ">= 99%",
        {
            "odds_rows": n_odds,
            "unmatched_rows": orphan_rows,
            "coverage": (n_odds - orphan_rows) / n_odds,
        },
        {"unmatched_listed": _records(orphan)},
    )
    flags = con.execute(
        """
        SELECT m.match_id, m.league, m.season, m.season_start_year, m.date, m.home, m.away,
               m.ragged_row,
               coalesce(bool_or(o.snapshot = 'early'), false) AS has_early,
               coalesce(bool_or(o.snapshot = 'close'), false) AS has_close,
               count(o.match_id) > 0 AS has_any
        FROM matches m LEFT JOIN odds o
          ON o.match_id = m.match_id AND o.market = '1X2'
        GROUP BY ALL
        """
    ).df()
    flags["in_scope"] = [
        in_scope(cfg, lg, int(y))
        for lg, y in zip(flags["league"], flags["season_start_year"], strict=True)
    ]
    sc = flags[flags["in_scope"]]
    cov = float(sc["has_any"].mean())
    by_ls = (
        sc.groupby(["league", "season"])
        .agg(
            matches=("match_id", "size"),
            with_1x2=("has_any", "sum"),
            with_early=("has_early", "sum"),
            with_close=("has_close", "sum"),
        )
        .reset_index()
    )
    by_ls["coverage_any_1x2"] = by_ls["with_1x2"] / by_ls["matches"]
    missing = sc[~sc["has_any"]][["match_id", "league", "season", "date", "home", "away"]]
    c2 = _check(
        "matches_have_1x2_odds_in_scope",
        "Share of in-scope football-data matches with at least one 1X2 odds row",
        cov >= 0.99,
        ">= 99% over in-scope league-seasons (configs/data.yaml scope)",
        {
            "in_scope_matches": len(sc),
            "with_any_1x2": int(sc["has_any"].sum()),
            "coverage": cov,
            "with_early_snapshot": float(sc["has_early"].mean()),
            "with_close_snapshot": float(sc["has_close"].mean()),
            "n_league_seasons_below_99pct": int((by_ls["coverage_any_1x2"] < 0.99).sum()),
            "out_of_scope_matches": int((~flags["in_scope"]).sum()),
        },
        {
            "matches_without_1x2_listed": _records(missing.astype({"date": str})),
            "by_league_season": _records(by_ls, 10_000),
        },
    )
    return [c1, c2]


def check_link(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    n_sb = con.execute("SELECT count(*) FROM sb_matches").df().iloc[0].tolist()[0]
    n_linked = (
        con.execute("SELECT count(DISTINCT sb_match_id) FROM sb_match_link")
        .df()
        .iloc[0]
        .tolist()[0]
    )
    fd_un = con.execute(
        """
        SELECT m.match_id, m.league, m.date, m.home, m.away, m.fthg, m.ftag
        FROM matches m
        WHERE m.season = '1516' AND m.league IN (SELECT DISTINCT league FROM sb_matches)
          AND m.match_id NOT IN (SELECT fd_match_id FROM sb_match_link)
        ORDER BY m.league, m.date
        """
    ).df()
    n_fd = (
        con.execute(
            "SELECT count(*) FROM matches WHERE season = '1516' AND league IN"
            " (SELECT DISTINCT league FROM sb_matches)"
        )
        .df()
        .iloc[0]
        .tolist()[0]
    )
    ok = n_linked == n_sb and n_fd - n_linked == len(fd_un)
    return _check(
        "statsbomb_football_data_link",
        "Every StatsBomb match links to one football-data match; unlinked football-data matches named",
        bool(ok and n_linked == 1517),
        "1,517 StatsBomb matches linked",
        {
            "statsbomb_matches": n_sb,
            "linked": n_linked,
            "football_data_2015_16_matches_in_linked_leagues": n_fd,
            "football_data_unlinked": len(fd_un),
            "unlinked_by_league": fd_un.groupby("league").size().to_dict(),
        },
        {"football_data_unlinked_listed": _records(fd_un.astype({"date": str}))},
    )


def check_goals(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    df = con.execute(
        """
        WITH g AS (
          SELECT t.sb_match_id,
                 sum(CASE WHEN t.team = s.home THEN 1 ELSE 0 END) AS sb_home_goals,
                 sum(CASE WHEN t.team = s.away THEN 1 ELSE 0 END) AS sb_away_goals
          FROM events_timeline t JOIN sb_matches s USING (sb_match_id)
          WHERE t.kind IN ('goal', 'own_goal') GROUP BY t.sb_match_id
        )
        SELECT l.sb_match_id, l.fd_match_id, s.home, s.away, s.home_score, s.away_score,
               coalesce(g.sb_home_goals, 0) AS ev_home, coalesce(g.sb_away_goals, 0) AS ev_away,
               m.fthg, m.ftag
        FROM sb_match_link l
        JOIN sb_matches s USING (sb_match_id)
        JOIN matches m ON m.match_id = l.fd_match_id
        LEFT JOIN g USING (sb_match_id)
        """
    ).df()
    ok = (
        (df["ev_home"] == df["home_score"])
        & (df["ev_away"] == df["away_score"])
        & (df["fthg"] == df["home_score"])
        & (df["ftag"] == df["away_score"])
    )
    fail = df[~ok]
    return _check(
        "goal_reconciliation",
        "StatsBomb goals (shots + own goals) == StatsBomb score == football-data score",
        float(ok.mean()) >= 0.99,
        ">= 99% of linked matches",
        {
            "linked_matches": len(df),
            "reconciled": int(ok.sum()),
            "share": float(ok.mean()),
            "events_vs_sb_score_mismatch": int(
                ((df["ev_home"] != df["home_score"]) | (df["ev_away"] != df["away_score"])).sum()
            ),
            "sb_vs_football_data_score_mismatch": int(
                ((df["fthg"] != df["home_score"]) | (df["ftag"] != df["away_score"])).sum()
            ),
        },
        {"failures_listed": _records(fail)},
    )


def check_player_invariants(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    q = {
        "sot_gt_shots": "shots_on_target > shots",
        "goals_gt_sot": "goals > shots_on_target",
        "penalty_goals_gt_penalties": "penalty_goals > penalties_taken",
        "penalties_gt_shots": "penalties_taken > shots",
        "negative_minutes": "minutes < 0",
        "events_without_appearance": "NOT appeared AND (shots > 0 OR minutes > 0)",
    }
    counts = {
        k: con.execute(f"SELECT count(*) FROM player_match WHERE {w}").df().iloc[0].tolist()[0]
        for k, w in q.items()
    }
    team_shots = (
        con.execute(
            """
        SELECT count(*) FROM (
          SELECT sb_match_id, sum(shots) AS p FROM player_match GROUP BY ALL
        ) a JOIN (SELECT sb_match_id, count(*) AS s FROM shots GROUP BY ALL) b USING (sb_match_id)
        WHERE a.p <> b.s
        """
        )
        .df()
        .iloc[0]
        .tolist()[0]
    )
    counts["matches_where_player_shots_sum_ne_shot_events"] = team_shots
    non_lineup = (
        con.execute("SELECT coalesce(sum(shots_by_non_lineup_players), 0) FROM sb_match_checks")
        .df()
        .iloc[0]
        .tolist()[0]
    )
    counts["shots_by_players_not_in_lineup"] = int(non_lineup)
    bench_cards = con.execute(
        "SELECT count(*) AS players, sum(red) AS red, sum(second_yellow) AS second_yellow,"
        " sum(yellow) AS yellow FROM player_match WHERE NOT appeared AND yellow + red + second_yellow > 0"
    ).df()
    return _check(
        "player_match_invariants",
        "goals <= shots_on_target <= shots, penalties consistent, off-pitch players record nothing",
        all(v == 0 for v in counts.values()),
        "all violation counts == 0",
        counts,
        {"unused_bench_players_with_cards_not_a_violation": _records(bench_cards)},
    )


def check_minutes(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    pm = con.execute(
        """
        SELECT p.*, s.match_length_s / 60.0 AS length_min
        FROM player_match p JOIN sb_matches s USING (sb_match_id)
        """
    ).df()
    over = pm[pm["minutes"] > pm["length_min"] + 1 / 3600]
    full = pm[pm["started"] & pm["exit_kind"].isna()].copy()
    full_bad = full[(full["minutes"] - full["length_min"]).abs() > 1 / 60 + full["gap_minutes"]]
    # Team identity: person-minutes on pitch = 11 x length - vacancy (temporary absences, late or
    # missing replacements, dismissals), where vacancy comes from the event stream.
    pm["vacancy_min"] = pm["vacancy_s"] / 60.0
    team = (
        pm.groupby(["sb_match_id", "team"])
        .agg(
            total=("minutes", "sum"),
            vacancy=("vacancy_min", "sum"),
            length=("length_min", "first"),
            starters=("started", "sum"),
        )
        .reset_index()
    )
    team["expected"] = 11 * team["length"] - team["vacancy"]
    team["diff_s"] = (team["total"] - team["expected"]) * 60
    team_bad = team[team["diff_s"].abs() > 2]
    starters_bad = team[team["starters"] != 11]
    disagree = pm[(pm["minutes"] - pm["minutes_positions"]).abs() > 1 / 60]
    started_mismatch = pm[pm["started"] != pm["started_lineup"]]
    c_over = _check(
        "minutes_not_above_match_length",
        "No player has minutes greater than the match length",
        len(over) == 0,
        "0 player-matches",
        {"player_matches": len(pm), "violations": len(over)},
        {
            "violations_listed": _records(
                over[["sb_match_id", "team", "player_id", "minutes", "length_min"]]
            )
        },
    )
    c_team = _check(
        "minutes_team_consistency",
        "Per team: sum of minutes == 11 x match length - vacancy from events (+-2 s);"
        " every team has 11 starters (Starting XI event); starters who never exit play the full match (less temporary absences)",
        len(team_bad) == 0 and len(starters_bad) == 0 and len(full_bad) == 0,
        "0 violations (tolerance 2 s per team; implementer-chosen)",
        {
            "team_matches": len(team),
            "team_total_violations": len(team_bad),
            "teams_without_11_starters": len(starters_bad),
            "full_match_starters_checked": len(full),
            "full_match_starter_violations": len(full_bad),
        },
        {
            "team_violations_listed": _records(team_bad),
            "starter_count_violations_listed": _records(starters_bad),
            "full_match_starter_violations_listed": _records(
                full_bad[
                    ["sb_match_id", "team", "player_id", "minutes", "length_min", "gap_minutes"]
                ]
            ),
        },
    )
    c_src = _check(
        "minutes_events_vs_positions",
        "Event-stream minutes (D-031) vs lineup-positions minutes (D-030 as written), and starter flags",
        None,
        "informational",
        {
            "player_matches": len(pm),
            "disagree_over_1s": len(disagree),
            "positions_higher_than_events": int(
                (disagree["minutes_positions"] > disagree["minutes"]).sum()
            ),
            "positions_over_match_length": int(
                (pm["minutes_positions"] > pm["length_min"] + 1 / 3600).sum()
            ),
            "started_by_event_vs_lineup_positions_mismatch": len(started_mismatch),
        },
        {
            "disagreements_listed": _records(
                disagree[["sb_match_id", "team", "player_id", "minutes", "minutes_positions"]]
            ),
            "started_mismatch_listed": _records(
                started_mismatch[["sb_match_id", "team", "player_id", "started", "started_lineup"]]
            ),
        },
    )
    return [c_over, c_team, c_src]


def check_dismissals(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    df = con.execute(
        """
        WITH d AS (
          SELECT t.sb_match_id,
                 sum(CASE WHEN t.team = s.home THEN 1 ELSE 0 END) AS sb_home,
                 sum(CASE WHEN t.team = s.away THEN 1 ELSE 0 END) AS sb_away
          FROM events_timeline t JOIN sb_matches s USING (sb_match_id)
          WHERE t.kind IN ('red_card', 'second_yellow') GROUP BY t.sb_match_id
        )
        SELECT l.sb_match_id, l.fd_match_id, m.home, m.away, m.date,
               coalesce(d.sb_home, 0) AS sb_home, coalesce(d.sb_away, 0) AS sb_away,
               m.hr, m.ar
        FROM sb_match_link l JOIN matches m ON m.match_id = l.fd_match_id
        LEFT JOIN d USING (sb_match_id)
        """
    ).df()
    ok = (df["sb_home"] == df["hr"]) & (df["sb_away"] == df["ar"])
    bad = df[~ok]
    chk = con.execute("SELECT * FROM sb_match_checks").df()
    card_bad = chk[
        (chk["event_cards_yellow"] != chk["lineup_cards_yellow"])
        | (chk["event_cards_second_yellow"] != chk["lineup_cards_second_yellow"])
        | (chk["event_cards_red"] != chk["lineup_cards_red"])
    ]
    c1 = _check(
        "dismissals_vs_football_data",
        "StatsBomb dismissal timeline (Red Card + Second Yellow) per team == football-data HR/AR",
        None,
        "informational; mismatches listed (no threshold fixed by PLAN)",
        {
            "linked_matches": len(df),
            "agree": int(ok.sum()),
            "share": float(ok.mean()),
            "sb_more_than_fd": int(((df["sb_home"] + df["sb_away"]) > (df["hr"] + df["ar"])).sum()),
            "sb_fewer_than_fd": int(
                ((df["sb_home"] + df["sb_away"]) < (df["hr"] + df["ar"])).sum()
            ),
            "total_sb_dismissals": int(df["sb_home"].sum() + df["sb_away"].sum()),
            "total_fd_red_cards": int(df["hr"].sum() + df["ar"].sum()),
        },
        {"mismatches_listed": _records(bad.astype({"date": str}))},
    )
    c2 = _check(
        "event_cards_vs_lineup_cards",
        "Per match, card counts in the event stream equal card counts in lineups cards[]",
        len(card_bad) == 0,
        "0 mismatching matches",
        {"matches": len(chk), "mismatching": len(card_bad)},
        {"mismatches_listed": _records(card_bad)},
    )
    c3 = _check(
        "statsbomb_structure",
        "Only periods 1 and 2; Half End present in both periods; own-goal event pairs balanced",
        bool(
            (chk["max_period"] <= 2).all()
            and (chk["half_end_events_p1"] > 0).all()
            and (chk["half_end_events_p2"] > 0).all()
            and (chk["own_goal_for_events"] == chk["own_goal_against_events"]).all()
        ),
        "0 violations",
        {
            "matches_with_period_gt_2": int((chk["max_period"] > 2).sum()),
            "matches_missing_half_end": int(
                ((chk["half_end_events_p1"] == 0) | (chk["half_end_events_p2"] == 0)).sum()
            ),
            "matches_own_goal_pair_unbalanced": int(
                (chk["own_goal_for_events"] != chk["own_goal_against_events"]).sum()
            ),
            "matches_where_event_team_names_differ_from_match_record": int(
                (chk["event_team_names_differ_from_match_record"] > 0).sum()
            ),
        },
    )
    return [c1, c2, c3]


def check_overround(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    df = con.execute(
        """
        SELECT match_id, bookmaker, snapshot, sum(1.0 / price) AS implied, count(*) AS n
        FROM odds WHERE market = '1X2' AND bookmaker NOT IN ('Max', 'BbMx')
        GROUP BY ALL HAVING count(*) = 3
        """
    ).df()
    out = df[(df["implied"] < 1.0) | (df["implied"] > 1.20)]
    mx = (
        con.execute(
            """
        SELECT count(*) AS n, sum(CASE WHEN implied < 1.0 THEN 1 ELSE 0 END) AS below_one FROM (
          SELECT sum(1.0 / price) AS implied FROM odds
          WHERE market = '1X2' AND bookmaker IN ('Max', 'BbMx') GROUP BY match_id, bookmaker, snapshot
          HAVING count(*) = 3)
        """
        )
        .df()
        .iloc[0]
        .tolist()
    )
    share = len(out) / len(df)
    by_book = (
        out.groupby(["bookmaker", "snapshot"], observed=True)
        .size()
        .rename("outliers")
        .reset_index()
    )
    return _check(
        "overround_1x2",
        "1X2 implied-probability sum between 1.00 and 1.20 for every bookmaker snapshot "
        "(maximum-of-bookmakers aggregates excluded; reported separately)",
        share < 0.01,
        "< 1% outside [1.00, 1.20] (implementer-chosen); outliers listed",
        {
            "bookmaker_snapshots": len(df),
            "outliers": len(out),
            "outlier_share": share,
            "implied_min": float(df["implied"].min()),
            "implied_median": float(df["implied"].median()),
            "implied_max": float(df["implied"].max()),
            "max_aggregate_snapshots": int(mx[0]),
            "max_aggregate_below_one": int(mx[1]),
        },
        {
            "outliers_by_bookmaker": _records(by_book, 1000),
            "outliers_listed": _records(out.sort_values("implied")),
        },
    )


def check_ah_lines(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Is Pinnacle's AH price pair consistent with the shared line (DATA.md open item)?

    Test: de-vigged home-cover probability of Pinnacle vs Bet365 and vs market average at the same
    shared line. A quarter-line mismatch would move this probability by far more than 0.10.
    Second test: sign of the line against the Pinnacle 1X2 favourite.
    """
    pq = """
      WITH ah AS (
        SELECT match_id, bookmaker, snapshot, line,
               max(CASE WHEN selection = 'home' THEN price END) AS h,
               max(CASE WHEN selection = 'away' THEN price END) AS a
        FROM odds WHERE market = 'AH' AND line_source = 'shared' GROUP BY ALL
      ), p AS (
        SELECT match_id, snapshot, line, (1/h)/(1/h + 1/a) AS pp FROM ah
        WHERE bookmaker = 'P' AND h IS NOT NULL AND a IS NOT NULL
      ), o AS (
        SELECT match_id, snapshot, bookmaker, (1/h)/(1/h + 1/a) AS po FROM ah
        WHERE bookmaker IN ('B365', 'Avg') AND h IS NOT NULL AND a IS NOT NULL
      )
      SELECT p.snapshot, o.bookmaker, abs(p.pp - o.po) AS d FROM p JOIN o USING (match_id, snapshot)
    """
    d = con.execute(pq).df()
    rows: list[dict[str, Any]] = []
    for (snap, book), g in d.groupby(["snapshot", "bookmaker"], observed=True):
        rows.append(
            {
                "snapshot": snap,
                "versus": book,
                "n": len(g),
                "mean_abs_diff": float(g["d"].mean()),
                "p95_abs_diff": float(g["d"].quantile(0.95)),
                "max_abs_diff": float(g["d"].max()),
                "share_over_0_10": float((g["d"] > 0.10).mean()),
            }
        )
    s = con.execute(
        """
        WITH p AS (
          SELECT match_id, snapshot, line,
                 max(CASE WHEN selection = 'home' THEN price END) AS h,
                 max(CASE WHEN selection = 'away' THEN price END) AS a
          FROM odds WHERE market = 'AH' AND bookmaker = 'P' AND line_source = 'shared' GROUP BY ALL
        ), x AS (
          SELECT match_id, snapshot,
                 max(CASE WHEN selection = 'home' THEN 1/price END) AS ph,
                 max(CASE WHEN selection = 'away' THEN 1/price END) AS pa
          FROM odds WHERE market = '1X2' AND bookmaker = 'PS' GROUP BY ALL
        )
        SELECT p.snapshot, count(*) AS n,
               avg(CASE WHEN (p.line < 0) = (x.ph > x.pa) THEN 1.0 ELSE 0.0 END) AS sign_agree
        FROM p JOIN x USING (match_id, snapshot) WHERE p.line <> 0 GROUP BY p.snapshot
        """
    ).df()
    worst = max(r["share_over_0_10"] for r in rows) if rows else 1.0
    sign_min = float(s["sign_agree"].min()) if len(s) else 0.0
    return _check(
        "ah_line_consistency",
        "Pinnacle AH prices are consistent with the shared line AHh/AHCh (2019/20 onward)",
        bool(rows) and worst < 0.01 and sign_min >= 0.95,
        "share |P - other| > 0.10 below 1% and line sign agrees with 1X2 favourite in >= 95% (implementer-chosen)",
        {"pinnacle_vs_other_bookmakers": rows, "line_sign_vs_1x2_favourite": _records(s)},
    )


def check_duplicates(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    keys = {
        "matches": "match_id",
        "odds": "match_id, bookmaker, market, selection, snapshot",
        "team_name_map": "league, fd_name",
        "sb_matches": "sb_match_id",
        "sb_match_link": "sb_match_id",
        "player_match": "sb_match_id, player_id",
        "shots": "sb_match_id, event_id",
        "events_timeline": "sb_match_id, event_index, kind",
        "sb_match_checks": "sb_match_id",
        "team_season": "league, season, team",
    }
    out: dict[str, int] = {}
    for t, k in keys.items():
        out[t] = (
            con.execute(
                f"SELECT count(*) FROM (SELECT {k} FROM {t} GROUP BY ALL HAVING count(*) > 1)"
            )
            .df()
            .iloc[0]
            .tolist()[0]
        )
    out["sb_match_link_fd_match_id"] = (
        con.execute(
            "SELECT count(*) FROM (SELECT fd_match_id FROM sb_match_link GROUP BY ALL HAVING count(*) > 1)"
        )
        .df()
        .iloc[0]
        .tolist()[0]
    )
    out["team_name_map_sb_name"] = (
        con.execute(
            "SELECT count(*) FROM (SELECT league, sb_name FROM team_name_map GROUP BY ALL HAVING count(*) > 1)"
        )
        .df()
        .iloc[0]
        .tolist()[0]
    )
    return _check(
        "no_duplicate_keys",
        "No duplicate primary keys in any table",
        all(v == 0 for v in out.values()),
        "0 duplicate keys per table",
        out,
        {"keys": keys},
    )


def check_team_tables(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    prom = con.execute(
        """
        SELECT league, season, count(*) AS teams,
               sum(CASE WHEN promoted THEN 1 ELSE 0 END) AS promoted,
               sum(CASE WHEN promoted IS NULL THEN 1 ELSE 0 END) AS promoted_unknown
        FROM team_season GROUP BY ALL ORDER BY ALL
        """
    ).df()
    mgr = con.execute(
        "SELECT count(*) AS teams, sum(CASE WHEN manager_change_flag THEN 1 ELSE 0 END) AS with_change,"
        " avg(manager_coverage) AS mean_coverage FROM team_season WHERE manager_change_flag IS NOT NULL"
    ).df()
    unmapped = con.execute(
        """
        SELECT DISTINCT m.league, m.home AS team FROM matches m
        WHERE m.season = '1516' AND m.league IN (SELECT DISTINCT league FROM sb_matches)
          AND (m.league, m.home) NOT IN (SELECT league, fd_name FROM team_name_map)
        """
    ).df()
    c1 = _check(
        "team_name_map_complete",
        "Every 2015/16 football-data team in the four StatsBomb leagues is in the reviewed map",
        len(unmapped) == 0,
        "0 unmapped teams",
        {
            "unmapped": len(unmapped),
            "map_rows": con.execute("SELECT count(*) FROM team_name_map").df().iloc[0].tolist()[0],
        },
    )
    c2 = _check(
        "team_season_summary",
        "Promoted counts per league-season (null where the previous season is not cached) and "
        "manager-change availability",
        None,
        "informational",
        {"manager_flag_rows": _records(mgr)},
        {"promoted_by_league_season": _records(prom, 10_000)},
    )
    return [c1, c2]


def run_validate(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config("data")
    db = resolve_path(cfg, "warehouse")
    con = duckdb.connect(str(db), read_only=True)
    checks: list[dict[str, Any]] = [check_row_counts(con)]
    checks += check_odds_join(con, cfg)
    checks.append(check_link(con))
    checks.append(check_goals(con))
    checks.append(check_player_invariants(con))
    checks += check_minutes(con)
    checks += check_dismissals(con)
    checks.append(check_overround(con))
    checks.append(check_ah_lines(con))
    checks.append(check_duplicates(con))
    checks += check_team_tables(con)
    con.close()
    failed = [c["id"] for c in checks if c["pass"] is False]
    out = {
        "provenance": provenance(
            "edgeforge validate",
            cfg,
            dir_digest(sorted(resolve_path(cfg, "processed_dir").glob("*.parquet"))),
        ),
        "gate1_pass": not failed,
        "failed_checks": failed,
        "summary": [
            {"id": c["id"], "pass": c["pass"], "threshold": c["threshold"]} for c in checks
        ],
        "checks": checks,
    }
    path = resolve_path(cfg, "metrics_dir") / "gate1_validation.json"
    path.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    log.info("wrote %s; failed checks: %s", path, failed or "none")
    return path
