"""Participation models (Phase 4): who starts, how long starters stay, and when bench players enter.

All three use history-only features (see `edgeforge.features.pit`) so they can price in the opening
state; the minutes models only need the player's history, so the same fitted model applies to
announced squads (lineups state) and to hypothetical squads (opening state).

  StartModel          multinomial logistic: out / bench / start, with L2 shrinkage
  StarterHazard       discrete-time hazard of leaving the pitch, 5-minute bins to 90 plus a 90+ bin
  BenchEntryHazard    discrete-time hazard of entering the pitch; P(appear) = 1 - prod(1 - h)

Minutes are real elapsed match minutes (stoppage included, D-031). A starter who is never taken off
plays to the whistle (mean match length `l_bar`).
"""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy import sparse
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

FloatArray = NDArray[np.float64]
GROUPS = ("GK", "DEF", "MID", "FWD", "UNK")
N_BINS = 19  # bins 0..17 = [5j, 5j+5) minutes, bin 18 = 90+
BIN_W = 5.0
OUT, BENCH, START = 0, 1, 2


def group_onehot(g: pd.Series) -> FloatArray:
    gg = g.fillna("UNK").where(g.fillna("UNK").isin(GROUPS), "UNK")
    return np.stack([(gg == k).to_numpy(float) for k in GROUPS], axis=1)


def minute_to_bin(t_min: FloatArray) -> NDArray[np.int64]:
    return np.minimum((np.asarray(t_min) // BIN_W).astype(np.int64), N_BINS - 1)


# ----------------------------------------------------------------------------- start / bench / out


def start_design(df: pd.DataFrame) -> pd.DataFrame:
    """Numeric design (before scaling) for candidate rows from starter_features + opening player
    features merged on (sb_match_id, player_id)."""
    x = pd.DataFrame(index=df.index)
    for k in range(1, 6):
        x[f"squad_t{k}"] = df[f"squad_t{k}"].fillna(0).astype(float)
        x[f"started_t{k}"] = df[f"started_t{k}"].fillna(0).astype(float)
    m = np.stack([df[f"mins_t{k}"].fillna(0).to_numpy(float) for k in range(1, 6)], axis=1) / 90.0
    x["mins_last5"] = m.mean(axis=1)
    x["mins_trend"] = m[:, :2].mean(axis=1) - m[:, 2:].mean(axis=1)
    x["days_since_team_last"] = df["days_since_team_last"].clip(0, 14).fillna(7.0) / 7.0
    x["team_matches_14d"] = df["team_matches_14d"].fillna(0).astype(float)
    x["team_matches_28d"] = df["team_matches_28d"].fillna(0).astype(float)
    x["n_team_prior"] = df["n_team_prior"].fillna(0).astype(float)
    x["is_home"] = df["is_home"].astype(float)
    for n, nm in ((5, "sr5"), (10, "sr10"), (1000, "sr_all")):
        x[nm] = (df[f"n_start_{n}"] + 0.4) / (df[f"n_squad_{n}"] + 1.0)
    x["ar_all"] = (df["n_app_1000"] + 0.5) / (df["n_squad_1000"] + 1.0)
    x["log_n_squad"] = np.log1p(df["n_squad_1000"])
    x["mean_start_min"] = ((df["mins_started_1000"] + 2 * 75.0) / (df["n_start_1000"] + 2.0)) / 90.0
    g = group_onehot(df["position_group"])
    for gi, gname in enumerate(GROUPS):
        x[f"g_{gname}"] = g[:, gi]
    return x


@dataclass
class StartModel:
    c: float
    scaler: StandardScaler = field(default_factory=StandardScaler)
    clf: Any = None

    def fit(self, df: pd.DataFrame, y: NDArray[np.int64]) -> "StartModel":
        x = start_design(df)
        self.cols = list(x.columns)
        xs = self.scaler.fit_transform(x.to_numpy(float))
        self.clf = LogisticRegression(C=self.c, max_iter=800)
        self.clf.fit(xs, y)
        self.classes_ = self.clf.classes_
        return self

    def predict_proba(self, df: pd.DataFrame) -> FloatArray:
        xs = self.scaler.transform(start_design(df)[self.cols].to_numpy(float))
        p = self.clf.predict_proba(xs)
        out = np.zeros((len(df), 3))
        for i, c in enumerate(self.classes_):
            out[:, int(c)] = p[:, i]
        return np.clip(out, 1e-6, 1.0)


@dataclass
class StartedLastBaseline:
    """P(start) from a single binary feature, 'started the team's previous match'."""

    p1: float = 0.5
    p0: float = 0.1

    def fit(self, df: pd.DataFrame, started: NDArray[np.int64]) -> "StartedLastBaseline":
        s1 = df["started_t1"].fillna(0).to_numpy() > 0
        self.p1 = float((started[s1].sum() + 1) / (s1.sum() + 2))
        self.p0 = float((started[~s1].sum() + 1) / ((~s1).sum() + 2))
        return self

    def predict(self, df: pd.DataFrame) -> FloatArray:
        s1 = df["started_t1"].fillna(0).to_numpy() > 0
        return np.where(s1, self.p1, self.p0)


# ----------------------------------------------------------------------------- hazards


def _bin_design(bins: NDArray[np.int64], groups: FloatArray) -> sparse.csr_matrix:
    """One-hot bin (19) plus bin x position-group interactions (19 x 5)."""
    n = len(bins)
    rows = np.arange(n)
    b = sparse.csr_matrix((np.ones(n), (rows, bins)), shape=(n, N_BINS))
    gi = groups.argmax(axis=1)
    bg = sparse.csr_matrix(
        (np.ones(n), (rows, bins * len(GROUPS) + gi)), shape=(n, N_BINS * len(GROUPS))
    )
    return sparse.hstack([b, bg]).tocsr()


def starter_covariates(df: pd.DataFrame) -> FloatArray:
    """Player-level covariates for the exit hazard (history only, shrunk)."""
    m_start = ((df["mins_started_1000"] + 2 * 75.0) / (df["n_start_1000"] + 2.0)) / 90.0
    sub_rate = (df["n_subbed_off_1000"] + 1.0) / (df["n_start_1000"] + 2.0)
    sub5 = (df["n_subbed_off_5"] + 0.5) / (df["n_start_5"] + 1.0)
    return np.stack(
        [m_start.to_numpy(float), sub_rate.to_numpy(float), sub5.to_numpy(float),
         np.log1p(df["n_start_1000"].to_numpy(float)),
         df["is_home"].to_numpy(float) if "is_home" in df else np.zeros(len(df))],
        axis=1,
    )  # fmt: skip


def bench_covariates(df: pd.DataFrame) -> FloatArray:
    n_b = (df["n_squad_1000"] - df["n_start_1000"]).clip(lower=0)
    app_rate = (df["n_bench_app_1000"] + 0.35) / (n_b + 1.0)
    n_b5 = (df["n_squad_5"] - df["n_start_5"]).clip(lower=0)
    app5 = (df["n_bench_app_5"] + 0.35) / (n_b5 + 1.0)
    mean_in = ((df["mins_bench_app_1000"] + 2 * 25.0) / (df["n_bench_app_1000"] + 2.0)) / 90.0
    return np.stack(
        [app_rate.to_numpy(float), app5.to_numpy(float), mean_in.to_numpy(float),
         np.log1p(n_b.to_numpy(float)),
         df["is_home"].to_numpy(float) if "is_home" in df else np.zeros(len(df))],
        axis=1,
    )  # fmt: skip


class _Hazard:
    covariates = staticmethod(starter_covariates)

    def __init__(self, c: float) -> None:
        self.c = c
        self.scaler = StandardScaler()
        self.clf: Any = None

    def _expand(self, df: pd.DataFrame, event_bin: NDArray[np.int64]) -> tuple[Any, Any, Any]:
        """Person-period expansion: event_bin in 0..18 is the bin of the event, N_BINS = none."""
        n = len(df)
        last = np.minimum(event_bin, N_BINS - 1)  # last observed bin
        counts = last + 1
        idx = np.repeat(np.arange(n), counts)
        bins = np.concatenate([np.arange(c) for c in counts])
        y = np.zeros(len(idx))
        has_event = event_bin < N_BINS
        y[np.cumsum(counts)[has_event] - 1] = 1.0
        return idx, bins, y

    def _design(self, cov: FloatArray, groups: FloatArray, idx: Any, bins: Any, fit: bool) -> Any:
        c = cov[idx]
        c = self.scaler.fit_transform(c) if fit else self.scaler.transform(c)
        return sparse.hstack([_bin_design(bins, groups[idx]), sparse.csr_matrix(c)]).tocsr()

    def fit(self, df: pd.DataFrame, event_bin: NDArray[np.int64]) -> "_Hazard":
        cov, groups = self.covariates(df), group_onehot(df["position_group"])
        idx, bins, y = self._expand(df, event_bin)
        x = self._design(cov, groups, idx, bins, True)
        self.clf = LogisticRegression(C=self.c, max_iter=400)
        self.clf.fit(x, y)
        return self

    def hazards(self, df: pd.DataFrame) -> FloatArray:
        """(n, N_BINS) per-bin event probabilities given the event has not yet happened."""
        n = len(df)
        cov, groups = self.covariates(df), group_onehot(df["position_group"])
        idx = np.repeat(np.arange(n), N_BINS)
        bins = np.tile(np.arange(N_BINS), n)
        x = self._design(cov, groups, idx, bins, False)
        out: FloatArray = self.clf.predict_proba(x)[:, 1].reshape(n, N_BINS)
        return out

    def pmf(self, df: pd.DataFrame) -> FloatArray:
        """(n, N_BINS + 1): P(event in bin j) for j < N_BINS, last column P(no event)."""
        h = self.hazards(df)
        surv = np.cumprod(1.0 - h, axis=1)
        prev = np.concatenate([np.ones((len(h), 1)), surv[:, :-1]], axis=1)
        return np.concatenate([prev * h, surv[:, -1:]], axis=1)


class StarterHazard(_Hazard):
    covariates = staticmethod(starter_covariates)


class BenchEntryHazard(_Hazard):
    covariates = staticmethod(bench_covariates)


def starter_event_bins(exit_kind: pd.Series, exit_s: pd.Series) -> NDArray[np.int64]:
    """Bin of the final exit; N_BINS if the starter stayed on to the whistle."""
    out = np.full(len(exit_kind), N_BINS, dtype=np.int64)
    has = exit_kind.notna().to_numpy() & exit_s.notna().to_numpy()
    out[has] = minute_to_bin(exit_s[has].to_numpy(float) / 60.0)
    return out


def bench_event_bins(appeared: pd.Series, sub_on_s: pd.Series) -> NDArray[np.int64]:
    out = np.full(len(appeared), N_BINS, dtype=np.int64)
    has = appeared.to_numpy(bool) & sub_on_s.notna().to_numpy()
    out[has] = minute_to_bin(sub_on_s[has].to_numpy(float) / 60.0)
    return out


# ----------------------------------------------------------------------------- minute grids


def starter_minutes_grid(l_bar: float) -> FloatArray:
    """Minutes played for each pmf column of a starter: exit bins 0..18, then 'stays on'."""
    mids = np.array([BIN_W * j + BIN_W / 2 for j in range(N_BINS - 1)] + [(90.0 + l_bar) / 2.0])
    return np.append(mids, l_bar)


def bench_minutes_grid(l_bar: float) -> FloatArray:
    """Minutes played for each entry bin 0..18 (the last pmf column, 'never enters', is dropped)."""
    mids = np.array([BIN_W * j + BIN_W / 2 for j in range(N_BINS - 1)] + [(90.0 + l_bar) / 2.0])
    return np.maximum(l_bar - mids, 1.0)
