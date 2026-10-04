"""Post-hoc calibration (Platt and isotonic) for the team markets.

Calibrators are fitted on tuning-window predictions only and applied to the held-out window.
A calibrator is kept for a market only if it improves held-out log loss with a paired bootstrap
CI that excludes zero.
"""

from typing import Any

import numpy as np
from numpy.typing import NDArray
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

FloatArray = NDArray[np.float64]
EPS = 1e-6


def _logit(p: FloatArray) -> FloatArray:
    p = np.clip(p, EPS, 1.0 - EPS)
    out: FloatArray = np.log(p / (1.0 - p))
    return out


class BinaryCalibrator:
    def __init__(self, method: str) -> None:
        self.method = method
        self._m: Any = None

    def fit(self, p: FloatArray, y: FloatArray) -> "BinaryCalibrator":
        if self.method == "platt":
            self._m = LogisticRegression(C=1e6, max_iter=1000)
            self._m.fit(_logit(p).reshape(-1, 1), y)
        elif self.method == "isotonic":
            self._m = IsotonicRegression(y_min=EPS, y_max=1 - EPS, out_of_bounds="clip")
            self._m.fit(p, y)
        else:
            raise ValueError(self.method)
        return self

    def predict(self, p: FloatArray) -> FloatArray:
        if self.method == "platt":
            out: FloatArray = self._m.predict_proba(_logit(p).reshape(-1, 1))[:, 1]
        else:
            out = np.clip(self._m.predict(p), EPS, 1 - EPS)
        return out


def calibrate_binary(
    p_tune: FloatArray, y_tune: FloatArray, p_test: FloatArray, method: str
) -> FloatArray:
    return BinaryCalibrator(method).fit(p_tune, y_tune).predict(p_test)


def calibrate_1x2(
    p_tune: FloatArray, y_tune: NDArray[np.int64], p_test: FloatArray, method: str
) -> FloatArray:
    """One-vs-rest calibration per class, then renormalised to sum to one."""
    cols = []
    for k in range(3):
        cols.append(
            calibrate_binary(p_tune[:, k], (y_tune == k).astype(float), p_test[:, k], method)
        )
    out: FloatArray = np.stack(cols, axis=1)
    return out / out.sum(axis=1, keepdims=True)


def platt_parameters(p_tune: FloatArray, y_tune: FloatArray) -> dict[str, float]:
    """Intercept and slope of the Platt map on logit(p), fitted on the tuning window."""
    m = LogisticRegression(C=1e6, max_iter=1000).fit(_logit(p_tune).reshape(-1, 1), y_tune)
    return {"intercept": float(m.intercept_[0]), "slope": float(m.coef_[0, 0])}
