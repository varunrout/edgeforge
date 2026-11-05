"""EdgeForge trader app: the lifecycle of a football price (opening -> lineups -> in-play).

Run with `uv run edgeforge serve app`. Historical replay of 2015/16 test matches; every number
comes from the serving bundle (derived from the models) or from `artifacts/metrics/` through
`edgeforge.evidence`. Nothing here claims an edge.
"""

import os
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from edgeforge.api import service  # noqa: E402
from edgeforge.api.bundle import DEFAULT_DIR, Bundle, load_bundle  # noqa: E402
from edgeforge.config import PROJECT_ROOT  # noqa: E402
from edgeforge.sga.engine import SGAError  # noqa: E402

st.set_page_config(page_title="EdgeForge trader", layout="wide")


@st.cache_resource
def get_bundle(path: str) -> Bundle:
    return load_bundle(Path(path))


def main() -> None:
    st.title("EdgeForge: the lifecycle of a football price")
    st.warning(service.DISCLAIMER)
    bundle_dir = os.environ.get("EDGEFORGE_BUNDLE", str(DEFAULT_DIR))
    try:
        b = get_bundle(bundle_dir)
    except FileNotFoundError as err:
        st.error(f"{err}")
        st.stop()
    events = service.list_events(b)
    labels = {e["id"]: f"{e['home']} v {e['away']} ({str(e['date'])[:10]})" for e in events}
    with st.sidebar:
        st.header("Match and margin")
        eid = st.selectbox("Match", list(labels), format_func=lambda i: labels[i])
        method = st.selectbox("Margin allocation", ["proportional", "power", "odds_ratio"], index=0)
        overround = st.select_slider(
            "Overround", options=[0.0, 0.05, 0.10, 0.15, 0.20], value=0.10,
            format_func=lambda x: f"{x:.0%}",
        )  # fmt: skip
        settlement = st.radio(
            "Prop settlement", ["start", "appear"],
            help="start: void if the player does not start (the Phase 6b recommendation); "
            "appear: void if he does not appear",
        )  # fmt: skip
        st.caption(b.meta["label"])
    ev = b.events[eid]
    st.subheader(f"{ev['home']} v {ev['away']}")
    state = st.radio(
        "Information state", ["opening", "lineups", "inplay"], horizontal=True,
        help="opening: T-48h, no lineups. lineups: T-60 min, confirmed XI. "
        "inplay: score and dismissals up to the minute.",
    )  # fmt: skip
    minute = 0.0
    if state == "inplay":
        minute = float(
            st.slider("Elapsed minute (play-time clock)", 0, int(ev["match_length_s"] // 60) - 1, 0)
        )
    tabs = st.tabs(["1. Prices", "2. Players and lineup shock", "3. SGA builder", "4. Evidence"])
    with tabs[0]:
        prices_tab(b, eid, state, minute, overround, method)
    with tabs[1]:
        players_tab(b, eid, state, overround, settlement)
    with tabs[2]:
        sga_tab(b, eid, state, overround, settlement)
    with tabs[3]:
        evidence_tab()


def prices_tab(
    b: Bundle, eid: int, state: str, minute: float, overround: float, method: str
) -> None:
    res = service.team_markets(
        b, eid, state, minute if state == "inplay" else None, overround, method
    )
    if state == "inplay":
        st.write(
            f"Minute {minute:.0f}: score {res['score'][0]}-{res['score'][1]}, "
            f"dismissals home {res['dismissals'][0]} / away {res['dismissals'][1]}"
        )
    st.caption(res["source"])
    df = pd.DataFrame(res["lines"]).rename(
        columns={
            "fair_probability": "fair p",
            "fair_odds": "fair odds",
            "quoted_probability": "quoted p",
            "quoted_odds": "quoted odds",
        }
    )
    st.dataframe(
        df.style.format(
            {
                "fair p": "{:.3f}",
                "fair odds": "{:.2f}",
                "quoted p": "{:.3f}",
                "quoted odds": "{:.2f}",
            }
        ),
        hide_index=True,
        width="stretch",
    )
    path = service.team_path(b, eid, 5)
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.4), sharex=True)
    mins = [p["minute"] for p in path["in_play"]]
    for k, lab in enumerate(("home", "draw", "away")):
        axes[0].plot(mins, [p["1x2"][k] for p in path["in_play"]], label=lab)
        axes[0].axhline(path["pre_match"]["1x2"][k], color=f"C{k}", linestyle=":", linewidth=0.8)
    axes[1].plot(mins, [p["over_2.5"] for p in path["in_play"]], color="k")
    axes[1].axhline(path["pre_match"]["over_2.5"], color="k", linestyle=":", linewidth=0.8)
    for e in path["timeline"]:
        colour = "tab:green" if e["kind"] in ("goal", "own_goal") else "tab:red"
        for ax in axes:
            ax.axvline(e["elapsed_s"] / 60, color=colour, linestyle="--", linewidth=0.8, alpha=0.7)
    if state == "inplay":
        for ax in axes:
            ax.axvline(minute, color="grey", linewidth=2, alpha=0.5)
    axes[0].set_title("Final 1X2 (dotted: pre-match)", fontsize=9)
    axes[1].set_title("Over 2.5 goals (green: goal, red: dismissal)", fontsize=9)
    for ax in axes:
        ax.set_xlabel("elapsed minute")
    axes[0].legend(fontsize=7)
    fig.tight_layout()
    st.pyplot(fig)
    plt.close(fig)
    st.caption(
        "Lineups do not change team-market prices in this model; they change player props and SGAs (tab 2)."
    )


def players_tab(b: Bundle, eid: int, state: str, overround: float, settlement: str) -> None:
    if state == "inplay":
        st.info("Player props exist for the opening and lineups states only (D-017).")
        return
    res = service.props(b, eid, state, overround, "odds_ratio", settlement)
    st.caption(res["label"])
    df = pd.DataFrame(res["props"])
    markets = sorted(df["market"].unique())
    market = st.selectbox("Market", markets, index=markets.index("anytime_scorer"))
    sub = df[(df["market"] == market) & df["offered"]].sort_values(
        "fair_probability", ascending=False
    )
    st.dataframe(
        sub[
            [
                "player_id",
                "team",
                "band",
                "p_start_input",
                "fair_probability",
                "fair_odds",
                "quoted_odds_yes",
                "quoted_odds_no",
            ]
        ],
        hide_index=True,
        width="stretch",
    )
    st.markdown(
        "**Lineup shock** (opening to lineups, UPPER BOUND: lineups assumed known with certainty)"
    )
    shock = pd.DataFrame(service.lineup_shock(b, eid, settlement))
    if shock.empty:
        st.write("No comparable props for this match.")
        return
    sm = shock[shock["market"] == market].sort_values(
        "relative_odds_change", key=lambda s: -s.abs()
    )
    st.dataframe(sm.head(15), hide_index=True, width="stretch")
    fig, ax = plt.subplots(figsize=(5, 3.2))
    for band, g in sm.groupby("band"):
        ax.scatter(g["p_opening"], g["p_lineups"], label=band, s=18)
    ax.plot([0, 1], [0, 1], color="grey", linewidth=0.8)
    ax.set_xlabel("opening probability")
    ax.set_ylabel("lineups probability")
    ax.legend(fontsize=7)
    fig.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


def sga_tab(b: Bundle, eid: int, state: str, overround: float, settlement: str) -> None:
    sga_state = "lineups" if state == "inplay" else state
    st.caption(
        f"SGAs are priced in the {sga_state} state by the seeded simulator (naive product vs joint)."
    )
    base = pd.DataFrame(
        service.props(b, eid, sga_state, overround, "odds_ratio", settlement)["props"]
    )
    players = base[(base["market"] == "anytime_scorer") & base["offered"]].sort_values(
        "fair_probability", ascending=False
    )
    names = {
        int(r.player_id): f"#{int(r.player_id)} ({r.team}, P(score)={r.fair_probability:.2f})"
        for r in players.itertuples()
    }
    legs: list[dict[str, Any]] = []
    c1, c2 = st.columns(2)
    with c1:
        res = st.selectbox("Match result", ["(none)", "home", "draw", "away"])
        if res != "(none)":
            legs.append({"kind": "team_result", "result": res})
        tot = st.selectbox(
            "Total goals",
            ["(none)", "over 1.5", "over 2.5", "over 3.5", "under 1.5", "under 2.5", "under 3.5"],
        )
        if tot != "(none)":
            side, line = tot.split()
            legs.append({"kind": "total", "line": float(line), "side": side})
        btts = st.selectbox("Both teams to score", ["(none)", "yes", "no"])
        if btts != "(none)":
            legs.append({"kind": "btts", "side": btts})
    with c2:
        for i in range(3):
            pid = st.selectbox(
                f"Player leg {i + 1}", [None, *names],
                format_func=lambda x: "(none)" if x is None else names[x], key=f"pl{i}",
            )  # fmt: skip
            if pid is not None:
                kind = st.selectbox(
                    f"Leg {i + 1} type",
                    ["anytime scorer", "shots 2+", "shots on target 1+", "shots on target 2+"],
                    key=f"kind{i}",
                )
                legs.append(
                    {
                        "anytime scorer": {"kind": "player_ags", "player_id": pid},
                        "shots 2+": {"kind": "player_shots", "player_id": pid, "k": 2},
                        "shots on target 1+": {"kind": "player_sot", "player_id": pid, "k": 1},
                        "shots on target 2+": {"kind": "player_sot", "player_id": pid, "k": 2},
                    }[kind]
                )
    if len(legs) < 2:
        st.info("Pick at least two legs.")
        return
    try:
        out = service.price_sga(b, eid, sga_state, legs, overround)
    except (SGAError, service.ServiceError, ValueError) as err:
        st.error(f"Rejected: {err}")
        return
    st.dataframe(
        pd.DataFrame(
            {"leg": [str(leg) for leg in legs], "standalone probability": out["leg_probabilities"]}
        ),
        hide_index=True,
    )
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        "Naive product", f"{out['naive_product']:.4f}", f"fair odds {out['fair_odds_naive']:.2f}"
    )
    m2.metric("Joint (simulated)", f"{out['joint']:.4f}", f"+/- {out['joint_mc_se']:.4f} (MC SE)")
    m3.metric("Adjusted fair odds", f"{out['fair_odds_adjusted']:.2f}")
    m4.metric("Joint / naive", f"{out['adjustment_ratio']:.2f}", out["direction"])
    st.caption(
        f"Quoted with {overround:.0%} margin on the joint: odds {out['quoted_odds_joint_with_margin']:.2f}. {out['label']}"
    )


def evidence_tab() -> None:
    h = service.model_health()
    st.subheader("What the evidence says (read from artifacts/metrics/)")
    for f in h["findings"]:
        st.markdown(f"- {f}")
    ci = h["ci_local"]
    status = "all checks passed" if ci["all_passed"] else "FAILED"
    st.write(
        f"Scripted fresh-clone check at commit `{ci['commit_sha'][:7]}`: {status} ({ci['created_at']}). "
        "GitHub Actions is not used (account limitation)."
    )
    rows = [
        {
            "claim": v["label"], "value": v["text"], "source": v["source"],
            "git sha": str(v["provenance"].get("git_sha", v["provenance"].get("commit_sha", "")))[:7],
        }
        for v in h["claims"].values()
    ]  # fmt: skip
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if "shadow" in h:
        st.subheader("Pre-match shadow log")
        st.json(h["shadow"])
    st.subheader("Calibration and efficiency figures")
    figs = PROJECT_ROOT / "artifacts" / "figures"
    for name in (
        "reliability_1x2_pillar_a_2024_25.png",
        "pillar_a_model_vs_market_F1_primary_2024_25.png",
        "pillar_a_flb_F1_primary_2024_25.png",
    ):
        if (figs / name).exists():
            st.image(str(figs / name), caption=name)


main()
