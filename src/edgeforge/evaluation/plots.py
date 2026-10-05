"""Figures for evaluation outputs (matplotlib, Agg backend). Aggregates only; no raw data."""

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def reliability_plot(
    curves: dict[str, list[dict[str, float]]], path: Path, title: str, n_label: str = ""
) -> None:
    fig, ax = plt.subplots(figsize=(5.2, 5.2))
    ax.plot([0, 1], [0, 1], color="grey", linestyle="--", linewidth=1, label="perfect")
    for name, bins in curves.items():
        ax.plot(
            [b["mean_pred"] for b in bins],
            [b["obs_freq"] for b in bins],
            marker="o",
            markersize=3,
            linewidth=1,
            label=name,
        )
    ax.set_xlabel("predicted probability")
    ax.set_ylabel("observed frequency")
    ax.set_title(title + (f"\n{n_label}" if n_label else ""), fontsize=9)
    ax.legend(fontsize=7)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def pit_plot(hists: dict[str, list[float]], path: Path, title: str) -> None:
    fig, axes = plt.subplots(1, len(hists), figsize=(3.2 * len(hists), 3), squeeze=False)
    for ax, (name, h) in zip(axes[0], hists.items(), strict=True):
        n = len(h)
        ax.bar([(i + 0.5) / n for i in range(n)], h, width=1.0 / n, edgecolor="white")
        ax.axhline(1.0 / n, color="red", linewidth=1)
        ax.set_title(name, fontsize=8)
        ax.set_xlabel("PIT")
    fig.suptitle(title, fontsize=9)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def line_plot(
    series: dict[str, list[tuple[float, float]]], path: Path, title: str, xlabel: str, ylabel: str
) -> None:
    fig, ax = plt.subplots(figsize=(5.5, 3.6))
    for name, pts in series.items():
        ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", markersize=3, label=name)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=9)
    ax.legend(fontsize=7)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def as_series(rows: list[dict[str, Any]], x: str, y: str) -> list[tuple[float, float]]:
    return [(float(r[x]), float(r[y])) for r in rows]


def forest_plot(
    rows: list[dict[str, Any]], path: Path, title: str, xlabel: str, null: float = 0.0
) -> None:
    """Point estimates with 95% CIs, one row per label. Rows: label, estimate, ci_low, ci_high."""
    fig, ax = plt.subplots(figsize=(6.2, 0.32 * len(rows) + 1.4))
    for i, r in enumerate(rows):
        ax.plot([r["ci_low"], r["ci_high"]], [i, i], color="tab:blue", linewidth=1.4)
        ax.plot(r["estimate"], i, "o", color="tab:blue", markersize=4)
    ax.axvline(null, color="grey", linestyle="--", linewidth=1)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r["label"] for r in rows], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_title(title, fontsize=9)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def hist_plot(values: Any, path: Path, title: str, xlabel: str, ref: float | None = None) -> None:
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    ax.hist(values, bins=40, edgecolor="white")
    if ref is not None:
        ax.axvline(ref, color="red", linewidth=1)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("matches")
    ax.set_title(title, fontsize=9)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
