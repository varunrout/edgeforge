"""Evidence registry: every number shown in the API, the app and the rendered documents is read
from a metrics file in `artifacts/metrics/` through this table (CLAUDE.md evidence rule).

A path is a tuple of dict keys, list indices, or `"id=<x>"` (the list element whose `id` is x).
`derive` claims compute from the whole file (counts, sums). Nothing here is typed in by hand.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from edgeforge.config import PROJECT_ROOT

METRICS_DIR = PROJECT_ROOT / "artifacts" / "metrics"


@dataclass(frozen=True)
class Claim:
    id: str
    label: str
    file: str
    path: tuple[str | int, ...] = ()
    fmt: str = ".4f"
    derive: Callable[[Any], Any] | None = None


def _dig(obj: Any, path: tuple[str | int, ...]) -> Any:
    for k in path:
        if isinstance(k, str) and k.startswith("id="):
            obj = next(x for x in obj if x.get("id") == k[3:])
        else:
            obj = obj[k]
    return obj


def _count(path: tuple[str | int, ...]) -> Callable[[Any], int]:
    return lambda j: len(_dig(j, path))


def _sum_values(path: tuple[str | int, ...]) -> Callable[[Any], int]:
    return lambda j: int(sum(_dig(j, path).values()))


PA = ("blocks", "F1_primary_2024_25")
P6 = ("value_of_information", "family_V", "markets")
C: list[Claim] = [
    # ---- Pillar A (pre-match market efficiency and model vs market)
    Claim(
        "a_n_matches", "Pillar A test matches (2024/25)", "pillar_a.json", (*PA, "n_matches"), "d"
    ),
    Claim(
        "a_model_ll",
        "Dixon-Coles 1X2 log loss, 2024/25",
        "team_model.json",
        (
            "blocks",
            "pillar_a",
            "tests",
            "primary_2024_25",
            "per_model",
            "dixon_coles",
            "1x2",
            "log_loss",
        ),
    ),
    Claim(
        "a_market_ll",
        "Pinnacle closing 1X2 log loss (proportional de-vig), 2024/25",
        "baseline_team.json",
        (
            "blocks",
            "pillar_a_primary_2024_25",
            "per_model",
            "market_PS_proportional",
            "1x2",
            "log_loss",
        ),
    ),
    Claim(
        "a_enc_weight",
        "Model weight in the log-linear pool with the market",
        "pillar_a.json",
        (*PA, "encompassing_fit", "model_weight"),
        ".3f",
    ),
    Claim(
        "a_enc_lo",
        "Model weight, 95% CI low",
        "pillar_a.json",
        (*PA, "encompassing_fit", "model_weight_ci95", 0),
        ".3f",
    ),
    Claim(
        "a_enc_hi",
        "Model weight, 95% CI high",
        "pillar_a.json",
        (*PA, "encompassing_fit", "model_weight_ci95", 1),
        ".3f",
    ),
    Claim(
        "a_move_slope",
        "Slope of (close - early) on (model - early)",
        "pillar_a.json",
        (*PA, "claims", "id=mvm.linemove.slope", "estimate"),
        ".4f",
    ),
    Claim(
        "a_move_lo",
        "Line-movement slope, 95% CI low",
        "pillar_a.json",
        (*PA, "claims", "id=mvm.linemove.slope", "ci_low"),
        ".4f",
    ),
    Claim(
        "a_move_hi",
        "Line-movement slope, 95% CI high",
        "pillar_a.json",
        (*PA, "claims", "id=mvm.linemove.slope", "ci_high"),
        ".4f",
    ),
    Claim(
        "a_n_bh",
        "Pre-registered efficiency claims surviving BH in 2024/25",
        "pillar_a.json",
        derive=_count((*PA, "bh_survivors")),
        fmt="d",
    ),
    Claim(
        "a_n_claims",
        "Claims in the 2024/25 BH family",
        "pillar_a.json",
        (*PA, "n_claims_in_bh_family"),
        "d",
    ),
    # ---- Phase 4 participation and props
    Claim(
        "p4_start_ll",
        "P(start) log loss, model",
        "phase4_participation.json",
        ("starter_model", "start_model", "log_loss"),
    ),
    Claim(
        "p4_start_base_ll",
        "P(start) log loss, 'started last match' baseline",
        "phase4_participation.json",
        ("starter_model", "started_last_match_baseline", "log_loss"),
    ),
    Claim(
        "p4_props_better",
        "Prop markets (of six) beating the Phase 2 baseline after BH, lineups state",
        "phase4_markets.json",
        derive=_count(
            (
                "lineups_state",
                "comparison_E_full_distribution_vs_phase2_baseline",
                "bh_family",
                "survivors",
            )
        ),
        fmt="d",
    ),
    Claim(
        "p4_shots1_diff",
        "Shots 1+ log-loss difference vs baseline (null)",
        "phase4_markets.json",
        (
            "lineups_state",
            "comparison_E_full_distribution_vs_phase2_baseline",
            "markets",
            "shots_1plus",
            "log_loss_diff_new_minus_baseline",
            "estimate",
        ),
        "+.4f",
    ),
    Claim(
        "p4_anytime_diff",
        "Anytime scorer log-loss difference vs baseline",
        "phase4_markets.json",
        (
            "lineups_state",
            "comparison_E_full_distribution_vs_phase2_baseline",
            "markets",
            "anytime_scorer",
            "log_loss_diff_new_minus_baseline",
            "estimate",
        ),
        "+.4f",
    ),
    # ---- Phase 5 simulator and SGA
    Claim(
        "s_inv_violations",
        "Simulator invariant violations over all match-states",
        "phase5_simulator.json",
        derive=lambda j: sum(
            sum(s["invariant_violations_matches"].values()) for s in j["states"].values()
        ),
        fmt="d",
    ),
    Claim(
        "s_n_matches",
        "Simulated test matches per state",
        "phase5_simulator.json",
        ("states", "lineups", "n_matches"),
        "d",
    ),
    Claim("s_n_sims", "Simulations per match and state", "phase5_simulator.json", ("n_sims",), "d"),
    Claim(
        "s_compB_survivors",
        "Markets where simulator marginals differ from the standalone models (lineups, BH)",
        "phase5_comparison_b.json",
        derive=_count(("states", "lineups", "bh_family", "survivors")),
        fmt="d",
    ),
    Claim(
        "j_n_tests",
        "SGA template x state tests",
        "phase5_sga.json",
        ("joint_validation", "bh_family", "n_tests"),
        "d",
    ),
    Claim(
        "j_better",
        "Tests where the simulated joint beats the naive product (BH 10%)",
        "phase5_sga.json",
        derive=_count(("joint_validation", "bh_family", "survivors_better")),
        fmt="d",
    ),
    Claim(
        "j_worse",
        "Tests where the simulated joint is worse (BH 10%)",
        "phase5_sga.json",
        derive=_count(("joint_validation", "bh_family", "survivors_worse")),
        fmt="d",
    ),
    Claim(
        "j_sim_ll",
        "Pooled joint log loss, simulated (lineups)",
        "phase5_sga.json",
        ("joint_validation", "pooled", "lineups|all", "simulated", "log_loss"),
    ),
    Claim(
        "j_naive_ll",
        "Pooled joint log loss, naive product (lineups)",
        "phase5_sga.json",
        ("joint_validation", "pooled", "lineups|all", "naive", "log_loss"),
    ),
    Claim(
        "j_sim_ece",
        "Pooled joint ECE, simulated (lineups)",
        "phase5_sga.json",
        ("joint_validation", "pooled", "lineups|all", "simulated", "ece"),
    ),
    Claim(
        "j_naive_ece",
        "Pooled joint ECE, naive product (lineups)",
        "phase5_sga.json",
        ("joint_validation", "pooled", "lineups|all", "naive", "ece"),
    ),
    Claim(
        "j_n_instances",
        "Settled SGA instances (lineups)",
        "phase5_sga.json",
        ("joint_validation", "pooled", "lineups|all", "n"),
        "d",
    ),
    # ---- Pillar C (lineup shock; UPPER BOUND) and Phase 6b levers (SIMULATION)
    Claim(
        "c_shock_rot",
        "Share of rotation-risk prop prices moving more than 25% in odds",
        "phase6_pillar_c.json",
        ("shock", "props", "by_band", "rotation", "share_abs_odds_change_gt_25pct"),
        ".1%",
    ),
    Claim(
        "c_shock_nailed",
        "Share of nailed-starter prop prices moving more than 25% in odds",
        "phase6_pillar_c.json",
        ("shock", "props", "by_band", "nailed", "share_abs_odds_change_gt_25pct"),
        ".1%",
    ),
    Claim(
        "c_voi_shots1",
        "Value of information, shots 1+ (log loss opening minus lineups)",
        "phase6_pillar_c.json",
        (*P6, "shots_1plus", "opening_minus_lineups", "estimate"),
        "+.4f",
    ),
    Claim(
        "c_voi_markets",
        "Prop markets (of six) where the lineups price is better (BH)",
        "phase6_pillar_c.json",
        derive=lambda j: sum(
            1 for k in j["value_of_information"]["family_V"]["survivors"] if k.startswith("props|")
        ),
        fmt="d",
    ),
    Claim(
        "c_voi_sga",
        "SGA templates (of 13) where the lineups price is better (BH)",
        "phase6_pillar_c.json",
        derive=lambda j: sum(
            1 for k in j["value_of_information"]["family_V"]["survivors"] if k.startswith("sga|")
        ),
        fmt="d",
    ),
    Claim(
        "b_base",
        "Informed expected profit per offered prop, base settlement, 10% overround",
        "phase6b_levers.json",
        ("test", "base|0.1", "informed_expected_per_offered"),
    ),
    Claim(
        "b_start",
        "Same, void if not starting",
        "phase6b_levers.json",
        ("test", "start|0.1", "informed_expected_per_offered"),
    ),
    Claim(
        "b_reco",
        "Same, void if not starting + odds-ratio allocation",
        "phase6b_levers.json",
        ("test", "start+odds_ratio|0.1", "informed_expected_per_offered"),
    ),
    # ---- Pillar B (in-play team markets)
    Claim(
        "i_n_tests",
        "Checkpoint x market tests",
        "phase7_inplay.json",
        ("family_I_checkpoint_x_market", "bh", "n_tests"),
        "d",
    ),
    Claim(
        "i_better",
        "Tests where the in-play model beats the naive baseline (BH 10%)",
        "phase7_inplay.json",
        derive=_count(("family_I_checkpoint_x_market", "bh", "model_better")),
        fmt="d",
    ),
    Claim(
        "i_worse",
        "Tests where it is worse (BH 10%)",
        "phase7_inplay.json",
        derive=_count(("family_I_checkpoint_x_market", "bh", "model_worse")),
        fmt="d",
    ),
    Claim(
        "i_dismiss",
        "1X2 log-loss difference after a dismissal, model minus baseline",
        "phase7_inplay.json",
        (
            "family_II_stratum_x_market",
            "tests",
            "after_dismissal|1x2",
            "log_loss_diff_model_minus_baseline",
            "estimate",
        ),
        "+.4f",
    ),
    Claim(
        "i_rem_obs",
        "Observed mean remaining goals (all checkpoints)",
        "phase7_inplay.json",
        ("remaining_goals_calibration", "pooled", "observed"),
        ".3f",
    ),
    Claim(
        "i_rem_model",
        "Model mean predicted remaining goals",
        "phase7_inplay.json",
        ("remaining_goals_calibration", "pooled", "model"),
        ".3f",
    ),
    Claim(
        "i_rem_base",
        "Baseline mean predicted remaining goals",
        "phase7_inplay.json",
        ("remaining_goals_calibration", "pooled", "baseline"),
        ".3f",
    ),
    Claim("i_n_matches", "In-play test matches", "phase7_inplay.json", ("n_test_matches",), "d"),
    # ---- engineering evidence
    Claim(
        "ci_sha",
        "Commit tested by the scripted fresh-clone check",
        "ci_local.json",
        ("commit_sha",),
        "s",
    ),
    Claim(
        "ci_passed",
        "Tests passed in the scripted check",
        "ci_local.json",
        ("steps", 4, "test_counts", "passed"),
        "d",
    ),
]
CLAIMS: dict[str, Claim] = {c.id: c for c in C}


def load_metric(file: str, metrics_dir: Path = METRICS_DIR) -> Any:
    return json.loads((metrics_dir / file).read_text(encoding="utf-8"))


def value(claim: Claim, metrics_dir: Path = METRICS_DIR) -> Any:
    j = load_metric(claim.file, metrics_dir)
    return claim.derive(j) if claim.derive else _dig(j, claim.path)


def fmt(v: Any, spec: str) -> str:
    return str(v) if spec == "s" else format(v, spec)


def render(claim_id: str, metrics_dir: Path = METRICS_DIR) -> str:
    c = CLAIMS[claim_id]
    return fmt(value(c, metrics_dir), c.fmt)


def provenance_of(file: str, metrics_dir: Path = METRICS_DIR) -> dict[str, Any]:
    j = load_metric(file, metrics_dir)
    if "provenance" in j:
        return dict(j["provenance"])
    return {k: j[k] for k in ("commit_sha", "created_at") if k in j}


def resolve_all(metrics_dir: Path = METRICS_DIR) -> dict[str, dict[str, Any]]:
    """All claims with value, rendered text and the provenance of their source file."""
    out: dict[str, dict[str, Any]] = {}
    for c in C:
        v = value(c, metrics_dir)
        out[c.id] = {
            "label": c.label,
            "value": v,
            "text": fmt(v, c.fmt),
            "source": c.file,
            "provenance": provenance_of(c.file, metrics_dir),
        }
    return out


from edgeforge.evidence_extra import EXTRA  # noqa: E402

C.extend(EXTRA)
CLAIMS.update({c.id: c for c in EXTRA})
