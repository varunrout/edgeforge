import pytest

from edgeforge.evaluation.tuning import GridEdgeError, Tuned, require_tuned, tune_interior


def test_interior_optimum_needs_no_widening() -> None:
    t = tune_interior("x", lambda v: (v - 5) ** 2, [1, 3, 5, 7, 9])
    assert t.value == 5 and t.interior and t.n_widenings == 0
    assert require_tuned(t) == 5


def test_edge_optimum_is_widened_automatically_before_use() -> None:
    calls: list[float] = []

    def score(v: float) -> float:
        calls.append(v)
        return (v - 100.0) ** 2  # optimum far above the initial grid

    t = tune_interior("kappa", score, [1, 2, 4, 8])
    assert t.interior and t.n_widenings >= 4
    assert 64 <= t.value <= 128 and max(calls) > 8
    assert require_tuned(t) == t.value


def test_natural_bound_is_accepted_and_flagged() -> None:
    t = tune_interior("k", lambda v: v, [0, 1, 2, 4], lower_bound=0.0)
    assert t.value == 0 and t.at_natural_bound and not t.interior
    assert require_tuned(t) == 0


def test_unverified_or_edge_results_cannot_reach_test_code() -> None:
    with pytest.raises(TypeError):
        require_tuned(3.0)  # type: ignore[arg-type]
    edge = Tuned("x", 1.0, interior=False, at_natural_bound=False, history=(), n_widenings=0)
    with pytest.raises(GridEdgeError):
        require_tuned(edge)


def test_widening_stops_with_an_error_when_no_interior_exists() -> None:
    with pytest.raises(GridEdgeError):
        tune_interior("monotone", lambda v: -v, [1, 2, 4], max_widen=3)


def test_tuning_score_is_never_called_on_test_data_by_construction() -> None:
    """The routine only calls the supplied score function, which is a tuning-window closure."""
    seen: list[float] = []
    tune_interior("c", lambda v: seen.append(v) or (v - 2) ** 2, [0.5, 1, 2, 4, 8])
    assert set(seen) == {0.5, 1, 2, 4, 8}


def test_non_finite_scores_are_rejected() -> None:
    with pytest.raises(ValueError):
        tune_interior("bad", lambda v: float("nan"), [1, 2, 3])
