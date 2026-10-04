"""Period-aware match clock for StatsBomb data (D-030).

StatsBomb's `minute` restarts at 45 in period 2 while period-1 stoppage runs past 45, so minute
alone is not monotonic. All elapsed times here are seconds of real match time (stoppage included):
period 1 = period clock; period 2 = length of period 1 + (period clock - 2700).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

P2_START_CLOCK_S = 2700
DISMISSAL_CARDS = frozenset({"Red Card", "Second Yellow"})


def clock_secs(clock: str) -> int:
    """'MM:SS' (minutes may exceed 59) -> seconds."""
    mm, ss = clock.split(":")[:2]
    return int(mm) * 60 + int(ss)


def period_ends(events: Iterable[dict[str, Any]]) -> dict[int, int]:
    """Period-clock seconds at the end of each period (from 'Half End', else last event)."""
    half_end: dict[int, int] = {}
    last: dict[int, int] = {}
    for e in events:
        t = e["minute"] * 60 + e["second"]
        p = e["period"]
        last[p] = max(last.get(p, 0), t)
        if e["type"]["name"] == "Half End":
            half_end[p] = max(half_end.get(p, 0), t)
    return {p: half_end.get(p, last[p]) for p in last}


def match_length_s(ends: dict[int, int]) -> int:
    return ends[1] + (ends[2] - P2_START_CLOCK_S)


def elapsed(clock_s: int, period: int, ends: dict[int, int]) -> int:
    """Elapsed match seconds for a time on the period clock. Periods 1 and 2 only."""
    if period == 1:
        return clock_s
    if period == 2:
        return ends[1] + (clock_s - P2_START_CLOCK_S)
    raise ValueError(f"unsupported period {period}")


def dismissal_cap_s(cards: Iterable[dict[str, Any]], ends: dict[int, int]) -> int | None:
    """Earliest elapsed second at which the player was dismissed, from lineup `cards` entries."""
    caps = [
        elapsed(clock_secs(c["time"]), c["period"], ends)
        for c in cards
        if c["card_type"] in DISMISSAL_CARDS
    ]
    return min(caps) if caps else None


def span_bounds(pos: dict[str, Any], ends: dict[int, int]) -> tuple[int, int]:
    """Elapsed (start, end) seconds of one lineup position span; open spans end at the whistle."""
    start = elapsed(clock_secs(pos["from"]), pos["from_period"], ends)
    end = (
        match_length_s(ends)
        if pos["to"] is None
        else elapsed(clock_secs(pos["to"]), pos["to_period"], ends)
    )
    return start, end


def _clamp(cap_s: int | None) -> Any:
    return (lambda x: x) if cap_s is None else (lambda x: min(x, cap_s))


def minutes_played(
    positions: Iterable[dict[str, Any]], ends: dict[int, int], cap_s: int | None = None
) -> float:
    """Minutes on pitch, capped at dismissal.

    StatsBomb orders some position spans by mm:ss ignoring the period (e.g. a span from
    45:12 of period 2 to 47:40 of period 1), so a span's duration can be negative while the chain
    of spans still telescopes to the true time. Hence a SIGNED sum of f(end) - f(start), with
    f(x) = min(x, cap).
    """
    f = _clamp(cap_s)
    secs = 0
    for pos in positions:
        start, end = span_bounds(pos, ends)
        secs += f(end) - f(start)
    return secs / 60


@dataclass(frozen=True)
class Presence:
    """Event-stream presence of one player.

    vacancy_s is the person-time this player's departure left a slot empty: a temporary absence
    (Player Off ... Player On), a gap before a late replacement, an exit never replaced, or a
    dismissal. A normal substitution (replacement on at the same second) leaves none.
    """

    seconds: int
    gap_seconds: int  # temporary absences only (Player Off ... Player On)
    vacancy_s: int
    sub_on_s: int | None
    sub_off_s: int | None
    exit_kind: str | None  # 'substitution' | 'player_off' | 'dismissal' | None (stayed on)
    exit_s: int | None


def event_presence(events: Iterable[dict[str, Any]], ends: dict[int, int]) -> dict[int, Presence]:
    """On-pitch time per player from the event stream (D-031).

    Events are processed in `index` order, which is true chronological order (unlike the lineup
    `positions` chain, whose spans are ordered by mm:ss ignoring the period). A player is on from
    the Starting XI event or as a Substitution replacement, off at a Substitution (off), Player Off
    or dismissal card, back on at Player On. Dismissal caps are therefore automatic.
    """
    length = match_length_s(ends)
    OPEN = -1
    spans: dict[int, list[list[int]]] = {}  # [start, end]; end == OPEN while on pitch
    sub_on: dict[int, int] = {}
    sub_off: dict[int, int] = {}
    gap: dict[int, int] = {}
    vacancy: dict[int, int] = {}
    pending: dict[int, tuple[int, str]] = {}  # off, slot empty since (t, kind)
    exit_info: dict[int, tuple[str, int]] = {}

    def turn_on(pid: int, t: int) -> None:
        sp = spans.setdefault(pid, [])
        if sp and sp[-1][1] == OPEN:
            return
        sp.append([t, OPEN])
        if pid in pending and pending[pid][1] == "player_off":
            t0, _ = pending.pop(pid)
            gap[pid] = gap.get(pid, 0) + (t - t0)
            vacancy[pid] = vacancy.get(pid, 0) + (t - t0)
            exit_info.pop(pid, None)

    def turn_off(pid: int, t: int, kind: str) -> None:
        sp = spans.get(pid)
        if kind == "substitution":
            if sp and sp[-1][1] == OPEN:
                sp[-1][1] = t
            if pid in pending:  # already off (Player Off) before the replacement arrived
                t0, _ = pending.pop(pid)
                vacancy[pid] = vacancy.get(pid, 0) + (t - t0)
            exit_info[pid] = ("substitution", t)
            return
        if sp and sp[-1][1] == OPEN:
            sp[-1][1] = t
            pending[pid] = (t, kind)
            exit_info[pid] = (kind, t)

    for e in sorted(events, key=lambda x: x["index"]):
        name = e["type"]["name"]
        t = elapsed(e["minute"] * 60 + e["second"], e["period"], ends)
        if name == "Starting XI":
            for x in e["tactics"]["lineup"]:
                turn_on(x["player"]["id"], 0)
        elif name == "Substitution":
            pid = e["player"]["id"]
            turn_off(pid, t, "substitution")
            sub_off[pid] = t
            rid = e["substitution"]["replacement"]["id"]
            turn_on(rid, t)
            sub_on.setdefault(rid, t)
        elif name == "Player Off":
            turn_off(e["player"]["id"], t, "player_off")
        elif name == "Player On":
            turn_on(e["player"]["id"], t)
        else:
            for k in ("foul_committed", "bad_behaviour"):
                if k in e and "card" in e[k] and e[k]["card"]["name"] in DISMISSAL_CARDS:
                    turn_off(e["player"]["id"], t, "dismissal")
    for pid, (t0, _) in pending.items():  # never replaced / dismissed: slot empty to the whistle
        vacancy[pid] = vacancy.get(pid, 0) + (length - t0)
    out: dict[int, Presence] = {}
    for pid, sp in spans.items():
        secs = sum((length if end == OPEN else end) - start for start, end in sp)
        kind, ex_t = exit_info.get(pid, (None, None))
        out[pid] = Presence(
            secs,
            gap.get(pid, 0),
            vacancy.get(pid, 0),
            sub_on.get(pid),
            sub_off.get(pid),
            kind,
            ex_t,
        )
    return out
