"""D-040: grid-edge rule for every tuning routine.

`tune_interior` evaluates a one-dimensional grid on the *tuning* window, and if the best value lies
on an edge of the grid it widens that side and re-tunes automatically (up to `max_widen` times).
It returns a `Tuned` result. Test-window code must call `require_tuned(...)`, which raises unless
the optimum was verified interior (or sits on a declared natural bound), so a test-window metric
cannot be computed from an edge optimum. The check happens before any test metric exists because
the test code only receives the `Tuned` value.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


class GridEdgeError(RuntimeError):
    """The tuned optimum is on the edge of its grid and could not be widened further."""


@dataclass(frozen=True)
class Tuned:
    name: str
    value: float
    interior: bool
    at_natural_bound: bool
    history: tuple[tuple[float, float], ...]  # (grid value, tuning score), all evaluated values
    n_widenings: int
    grid_initial: tuple[float, ...] = field(default_factory=tuple)

    def as_record(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "interior": self.interior,
            "at_natural_bound": self.at_natural_bound,
            "n_widenings": self.n_widenings,
            "grid_initial": list(self.grid_initial),
            "evaluated": [{"value": v, "score": s} for v, s in self.history],
        }


def require_tuned(t: Tuned) -> float:
    """Gate for test-window code: returns the tuned value only if its optimum is interior."""
    if not isinstance(t, Tuned):
        raise TypeError("test-window code needs a Tuned value, not a bare number")
    if not (t.interior or t.at_natural_bound):
        raise GridEdgeError(f"{t.name}: optimum {t.value} is on the grid edge")
    return t.value


def tune_interior(
    name: str,
    score: Callable[[float], float],
    grid: Sequence[float],
    widen_low: Callable[[float], float] | None = None,
    widen_high: Callable[[float], float] | None = None,
    lower_bound: float | None = None,
    upper_bound: float | None = None,
    max_widen: int = 6,
) -> Tuned:
    """Minimise `score` over `grid` on the tuning window with automatic widening.

    `widen_low`/`widen_high` give the next grid point beyond the current extreme (default: halve /
    double). `lower_bound`/`upper_bound` are natural bounds (for example 0 for a shrinkage
    strength); an optimum exactly on a natural bound is accepted and flagged.
    """
    pts = sorted(set(float(g) for g in grid))
    if len(pts) < 3:
        raise ValueError("a grid needs at least three points to have an interior")
    cache: dict[float, float] = {}

    def ev(v: float) -> float:
        if v not in cache:
            val = float(score(v))
            if val != val or val in (float("inf"), float("-inf")):
                raise ValueError(f"{name}: non-finite tuning score at {v}")
            cache[v] = val
            log.info("tune %s: value %s -> score %.6f", name, v, cache[v])
        return cache[v]

    for v in pts:
        ev(v)
    n_wide = 0
    while True:
        best = min(pts, key=ev)
        on_low, on_high = best == pts[0], best == pts[-1]
        natural_low = lower_bound is not None and best <= lower_bound
        natural_high = upper_bound is not None and best >= upper_bound
        if (not on_low or natural_low) and (not on_high or natural_high):
            return Tuned(
                name,
                best,
                interior=not (on_low or on_high),
                at_natural_bound=natural_low or natural_high,
                history=tuple(sorted(cache.items())),
                n_widenings=n_wide,
                grid_initial=tuple(sorted(set(float(g) for g in grid))),
            )
        if n_wide >= max_widen:
            raise GridEdgeError(
                f"{name}: optimum {best} still on the grid edge after {n_wide} widenings"
            )
        n_wide += 1
        if on_low and not natural_low:
            nxt = (widen_low or (lambda x: x / 2.0))(pts[0])
            if lower_bound is not None:
                nxt = max(nxt, lower_bound)
            if nxt >= pts[0]:
                raise GridEdgeError(f"{name}: cannot widen below {pts[0]}")
            pts.insert(0, nxt)
            ev(nxt)
        if on_high and not natural_high:
            nxt = (widen_high or (lambda x: x * 2.0))(pts[-1])
            if upper_bound is not None:
                nxt = min(nxt, upper_bound)
            if nxt <= pts[-1]:
                raise GridEdgeError(f"{name}: cannot widen above {pts[-1]}")
            pts.append(nxt)
            ev(nxt)
