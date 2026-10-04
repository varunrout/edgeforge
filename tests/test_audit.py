from pathlib import Path

from edgeforge.data.audit import audit_file

HEADER = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,PSH,PSCH,B365H,B365CH,HR,AR"


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "x.body"
    p.write_bytes(("﻿" + HEADER + "\n" + body).encode("utf-8"))
    return p


def test_counts_nonnull_and_skips_blank_rows(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "E0,01/08/2024,A,B,1,0,1.5,1.6,1.4,1.5,0,0\n"
        ",,,,,,,,,,,\n"
        "E0,02/08/2024,C,D,0,0,,,1.4,1.5,0,1\n",
    )
    out = audit_file(p, "E0", "2425")
    assert out["rows"] == 2
    assert out["nonnull"]["PSH"] == 1
    assert out["nonnull"]["HR"] == 2
    assert out["closing_bookmakers_1x2"] == ["B365", "PS"]


def test_ragged_rows_are_counted_not_dropped(tmp_path: Path) -> None:
    p = _write(tmp_path, "E0,01/08/2024,A,B,1,0,1.5,1.6,1.4,1.5,0,0,extra,fields\n")
    out = audit_file(p, "E0", "2425")
    assert out["rows"] == 1 and out["ragged_rows"] == 1
