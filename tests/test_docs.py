"""Documents are rendered from metrics: they cannot drift, hide a null or carry typed numbers."""

from pathlib import Path

import pytest

from edgeforge import evidence
from edgeforge.docs_render import (
    TARGETS,
    TEMPLATE_DIR,
    check_all,
    lint_template,
    render_text,
)

ROOT = Path(__file__).resolve().parents[1]


def test_every_claim_resolves_to_a_finite_value_with_provenance() -> None:
    resolved = evidence.resolve_all()
    assert len(resolved) == len(evidence.CLAIMS) > 80
    for cid, r in resolved.items():
        assert r["text"] != "" and r["provenance"], cid
        if isinstance(r["value"], float):
            assert r["value"] == r["value"], cid  # not NaN


def test_templates_contain_no_numbers_outside_placeholders() -> None:
    for t in TARGETS:
        assert lint_template((TEMPLATE_DIR / t).read_text(encoding="utf-8")) == [], t


def test_lint_catches_typed_numbers_and_render_rejects_unknown_claims() -> None:
    assert lint_template("the model wins by 0.0123 or 12% on 758 matches in 2024/25") != []
    assert len(lint_template("the model wins by 0.0123 or 12% on 758 matches in 2024/25")) == 3
    assert lint_template("see D-040 and `10%` and 2024/25 and {{a_n_matches}}") == []
    with pytest.raises(KeyError):
        render_text("{{no_such_claim}}")


def test_rendered_documents_match_a_fresh_render_of_the_metrics() -> None:
    assert check_all() == []


def test_documents_state_the_nulls_and_do_not_claim_an_edge() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pack = (ROOT / "reports" / "evidence_pack.md").read_text(encoding="utf-8")
    report = (ROOT / "reports" / "technical_report.md").read_text(encoding="utf-8")
    for doc in (readme, pack):
        assert "does not beat the market" in doc or "does the pre-match model beat" in doc.lower()
        assert (
            "No in-play market prices exist" in doc
            or "no in-play market prices exist" in doc.lower()
        )
    assert "The market is better." in report
    assert "No betting edge" in readme and "not claimed" in readme.lower()
    for doc in (readme, pack, report):
        low = doc.lower()
        for bad in ("guaranteed", "risk-free", "we beat the market", "profitable strategy"):
            assert bad not in low
    assert "neutralising" not in readme.lower() and "neutralising" not in pack.lower()
    assert all("player_name" not in d for d in (readme, pack, report))


def test_mixed_case_claim_ids_render_and_leftover_placeholders_fail() -> None:
    assert render_text("{{s_compB_survivors}}") == evidence.render("s_compB_survivors")
    with pytest.raises(KeyError):
        render_text("{{ Not_A_Claim }}")
    rendered = (ROOT / "reports" / "technical_report.md").read_text(encoding="utf-8")
    assert "{{" not in rendered and "}}" not in rendered


def test_the_withdrawn_neutralising_margin_figures_are_not_in_the_documents() -> None:
    for name in (
        "README.md",
        "reports/technical_report.md",
        "reports/evidence_pack.md",
        "reports/cv_project_summary.md",
    ):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "260%" not in text and "330%" not in text and "485%" not in text
    assert not (ROOT / "artifacts" / "figures" / "phase6_policy_heatmap.png").exists()


def test_positioning_and_efficiency_wording() -> None:
    docs = {
        n: (ROOT / n).read_text(encoding="utf-8")
        for n in (
            "README.md",
            "reports/technical_report.md",
            "reports/evidence_pack.md",
            "reports/cv_project_summary.md",
        )
    }
    for n, text in docs.items():
        assert "audited research prototype" in text.lower(), n
        assert "no market inefficiency replicates" not in text.lower(), n
        assert "confirmatory findings" in text and "did not" in text, n
    assert "## What this shows" in docs["README.md"]
    assert "not a proof that the market is efficient" in docs["README.md"]
