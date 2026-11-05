"""`edgeforge docs render|check` (CLAUDE.md evidence rule): documents that contain numbers are
rendered from `artifacts/metrics/` through the evidence registry. Templates live in
`configs/doc_templates/*.tmpl` and may contain `{{claim_id}}`, `{{provenance_table}}` and
`{{claims_table}}`. A number typed into a template outside a placeholder is a lint error.
"""

import re
from pathlib import Path

from edgeforge import evidence
from edgeforge.config import PROJECT_ROOT

TEMPLATE_DIR = PROJECT_ROOT / "configs" / "doc_templates"
TARGETS = {
    "README.tmpl": "README.md",
    "technical_report.tmpl": "reports/technical_report.md",
    "evidence_pack.tmpl": "reports/evidence_pack.md",
    "cv_project_summary.tmpl": "reports/cv_project_summary.md",
}
PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")
# numbers that may stay in prose: decision ids, years, short counts, version-like tokens
BARE_NUMBER = re.compile(r"(?<![\w.-])(\d+\.\d+|\d+%|\d{3,})(?![\w-])")
ALLOWED_YEARS = re.compile(r"^((19|20)\d{2}|95%)$")  # years and the confidence level


def provenance_table(metrics_dir: Path) -> str:
    files = sorted({c.file for c in evidence.CLAIMS.values()})
    rows = [
        "| metrics file | produced by | git sha | created (UTC) | data version |",
        "|---|---|---|---|---|",
    ]
    for f in files:
        j = evidence.load_metric(f, metrics_dir)
        p = j.get("provenance", {})
        sha = str(p.get("git_sha", j.get("commit_sha", "")))[:7]
        rows.append(
            f"| `{f}` | `{p.get('command', 'edgeforge ci-local' if 'steps' in j else '')}` | `{sha}` | {p.get('created_at', j.get('created_at', ''))} | {p.get('data_version', '')} |"
        )
    return "\n".join(rows)


def claims_table(metrics_dir: Path) -> str:
    rows = ["| claim | value | source file | path in the file |", "|---|---|---|---|"]
    for c in evidence.CLAIMS.values():
        path = (
            "(derived: count or sum over the file)"
            if c.derive
            else " / ".join(str(x) for x in c.path)
        )
        rows.append(
            f"| {c.label} | {evidence.fmt(evidence.value(c, metrics_dir), c.fmt)} | `{c.file}` | `{path}` |"
        )
    return "\n".join(rows)


def render_text(template: str, metrics_dir: Path = evidence.METRICS_DIR) -> str:
    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key == "provenance_table":
            return provenance_table(metrics_dir)
        if key == "claims_table":
            return claims_table(metrics_dir)
        if key not in evidence.CLAIMS:
            raise KeyError(f"unknown claim {key!r} in a template")
        return evidence.render(key, metrics_dir)

    return PLACEHOLDER.sub(sub, template)


def lint_template(template: str) -> list[str]:
    """Numbers typed into prose outside placeholders (decimals, percentages, integers >= 100)."""
    bad: list[str] = []
    for n, line in enumerate(PLACEHOLDER.sub("", template).splitlines(), 1):
        if (
            line.lstrip().startswith("```")
            or "uv run" in line
            or line.lstrip().startswith("<!--")
            or "<img" in line
        ):
            continue
        for m in BARE_NUMBER.finditer(re.sub(r"`[^`]*`|D-\d+", "", line)):
            if not ALLOWED_YEARS.match(m.group(1)):
                bad.append(f"line {n}: bare number {m.group(1)!r}")
    return bad


def render_all(
    write: bool = True,
    template_dir: Path = TEMPLATE_DIR,
    root: Path = PROJECT_ROOT,
    metrics_dir: Path = evidence.METRICS_DIR,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for tmpl, target in TARGETS.items():
        text = render_text((template_dir / tmpl).read_text(encoding="utf-8"), metrics_dir)
        out[target] = text
        if write:
            path = root / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    return out


def check_all(
    template_dir: Path = TEMPLATE_DIR,
    root: Path = PROJECT_ROOT,
    metrics_dir: Path = evidence.METRICS_DIR,
) -> list[str]:
    """Problems: lint errors in templates and rendered files that differ from a fresh render."""
    problems: list[str] = []
    for tmpl in TARGETS:
        problems += [
            f"{tmpl}: {p}" for p in lint_template((template_dir / tmpl).read_text(encoding="utf-8"))
        ]
    for target, text in render_all(False, template_dir, root, metrics_dir).items():
        path = root / target
        have = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
        if have != text:
            problems.append(
                f"{target}: differs from a fresh render of the metrics (run `edgeforge docs render`)"
            )
    return problems
