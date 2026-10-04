"""football-data date and kick-off parsing (dd/mm/yy before 2019/20, dd/mm/yyyy after)."""

from datetime import date, datetime, time


def parse_fd_date(raw: str) -> date | None:
    """Parse 'dd/mm/yy' (yy >= 90 -> 19yy, else 20yy) or 'dd/mm/yyyy'. Blank/invalid -> None."""
    raw = raw.strip()
    if not raw:
        return None
    parts = raw.split("/")
    if len(parts) != 3:
        return None
    try:
        d, m, y = (int(p) for p in parts)
        if len(parts[2]) == 2:
            y += 1900 if y >= 90 else 2000
        elif len(parts[2]) != 4:
            return None
        return date(y, m, d)
    except ValueError:
        return None


def parse_kickoff(raw: str) -> time | None:
    """'HH:MM' -> time, blank/invalid -> None. Local time of the league; no zone in the payload."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None
