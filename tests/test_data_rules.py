from datetime import date, time

import pytest

from edgeforge.data.classify import UnclassifiedOutcomeError, is_on_target
from edgeforge.data.dates import parse_fd_date, parse_kickoff
from edgeforge.data.fd_tables import detect_specs, make_match_id, season_start_year
from edgeforge.data.sb_tables import UnknownPositionError, position_group


def test_parse_fd_date_both_formats_and_pivot() -> None:
    assert parse_fd_date("16/08/2024") == date(2024, 8, 16)
    assert parse_fd_date("14/08/93") == date(1993, 8, 14)
    assert parse_fd_date("09/08/05") == date(2005, 8, 9)
    assert parse_fd_date("") is None
    assert parse_fd_date("31/02/2024") is None
    assert parse_fd_date("1/2") is None


def test_parse_kickoff() -> None:
    assert parse_kickoff("20:00") == time(20, 0)
    assert parse_kickoff("") is None
    assert parse_kickoff("late") is None


@pytest.mark.parametrize("outcome", ["Goal", "Saved", "Saved to Post"])
def test_on_target(outcome: str) -> None:
    assert is_on_target(outcome)


@pytest.mark.parametrize("outcome", ["Off T", "Wayward", "Blocked", "Post", "Saved Off Target"])
def test_not_on_target(outcome: str) -> None:
    assert not is_on_target(outcome)


def test_unknown_outcome_raises() -> None:
    with pytest.raises(UnclassifiedOutcomeError):
        is_on_target("Mystery")


def test_position_groups() -> None:
    assert position_group("Goalkeeper") == "GK"
    assert position_group("Right Wing Back") == "DEF"
    assert position_group("Left Center Back") == "DEF"
    assert position_group("Center Attacking Midfield") == "MID"
    assert position_group("Left Wing") == "FWD"
    assert position_group("Secondary Striker") == "FWD"
    with pytest.raises(UnknownPositionError):
        position_group("Substitute")


def test_detect_specs_splits_early_and_closing_by_header() -> None:
    header = [
        "PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA",
        "VCH", "VCD", "VCA",  # VC Bet early, NOT a closing price
        "P>2.5", "P<2.5", "PC>2.5", "PC<2.5",
        "AHh", "PAHH", "PAHA", "AHCh", "PCAHH", "PCAHA",
    ]  # fmt: skip
    got = {(s.bookmaker, s.market, s.snapshot) for s in detect_specs(header)}
    assert ("PS", "1X2", "early") in got and ("PS", "1X2", "close") in got
    assert ("VC", "1X2", "early") in got and ("VC", "1X2", "close") not in got
    assert ("P", "OU", "early") in got and ("P", "OU", "close") in got
    assert ("P", "AH", "early") in got and ("P", "AH", "close") in got
    ah = {(s.snapshot): s.line_col for s in detect_specs(header) if s.market == "AH"}
    assert ah == {"early": "AHh", "close": "AHCh"}


def test_match_id_and_season_year() -> None:
    assert make_match_id("E0", "2425", date(2024, 8, 16), "Man United", "Fulham") == (
        "E0_2425_20240816_man_united_fulham"
    )
    assert season_start_year("9394") == 1993 and season_start_year("0506") == 2005
