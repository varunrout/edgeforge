"""Settlement classifications fixed by D-030. Unknown values raise: never guess."""

ON_TARGET_OUTCOMES = frozenset({"Goal", "Saved", "Saved to Post"})
OFF_TARGET_OUTCOMES = frozenset({"Off T", "Wayward", "Blocked", "Post"})


class UnclassifiedOutcomeError(ValueError):
    """A shot outcome that D-030 does not classify."""


def is_on_target(outcome: str) -> bool:
    """D-030: on target = Goal, Saved, Saved to Post; 'Saved Off Target'-type is not."""
    if outcome in ON_TARGET_OUTCOMES:
        return True
    if outcome in OFF_TARGET_OUTCOMES or "Off Target" in outcome:
        return False
    raise UnclassifiedOutcomeError(f"shot outcome {outcome!r} is not classified in D-030")
